"""Consens — bevakning av upphandlingar och avtalsutgångar.

Appfabrik. Vyerna är uppdelade i moduler från dag ett
(publikt/auth/flode/admin/api) — lärdomen från Nodpunkts main.py.
"""
import os

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix


def _las_env(app_rot):
    """Läs /opt/consens/.env (KEY=VAL per rad) in i miljön, så att
    webbappen, cron-jobben och CLI-kommandon alla ser samma nycklar.
    Befintliga miljövariabler vinner alltid över filen."""
    import os
    sokvag = os.path.join(os.path.dirname(app_rot), ".env")
    try:
        with open(sokvag) as f:
            for rad in f:
                rad = rad.strip()
                if not rad or rad.startswith("#") or "=" not in rad:
                    continue
                k, _, v = rad.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"'))
    except FileNotFoundError:
        pass


def create_app(test_config=None):
    _las_env(__file__ and __import__("os").path.dirname(__file__))
    app = Flask(__name__, instance_relative_config=True)
    # Bakom proxy (consens.se): lita på X-Forwarded-For/Proto/Host
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("CONSENS_SECRET", "dev-byt-mig"),
        DATABASE=os.path.join(app.instance_path, "consens.sqlite"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.from_mapping(test_config)

    os.makedirs(app.instance_path, exist_ok=True)

    from . import db
    db.init_app(app)

    from .vyer import publikt, auth, flode, admin, api
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    if not app.config.get("TESTING"):
        app.config.setdefault("SESSION_COOKIE_SECURE", True)

    @app.after_request
    def _sakerhetsheaders(svar):
        svar.headers.setdefault("X-Content-Type-Options", "nosniff")
        svar.headers.setdefault("X-Frame-Options", "DENY")
        svar.headers.setdefault("Referrer-Policy",
                                "strict-origin-when-cross-origin")
        svar.headers.setdefault("Permissions-Policy",
                                "camera=(), microphone=(), geolocation=()")
        return svar

    @app.after_request
    def _logga_besok(svar):
        """Publika sidvisningar (utloggade besökare) till besöksstatistiken."""
        try:
            from flask import g, request
            if (request.method == "GET" and svar.status_code == 200
                    and g.get("anvandare") is None
                    and request.endpoint
                    and request.endpoint.startswith("publikt.")
                    and "text/html" in (svar.content_type or "")):
                from .db import get_db
                db = get_db()
                db.execute("INSERT INTO besok (sokvag) VALUES (?)",
                           (request.path,))
                db.commit()
        except Exception:
            pass  # statistik får aldrig fälla en sidvisning
        return svar

    import os as _os
    _css = _os.path.join(_os.path.dirname(__file__), "static", "consens.css")
    app.jinja_env.globals["css_v"] = int(_os.path.getmtime(_css))

    import secrets as _secrets
    from flask import abort, request as _req, session as _session

    def _csrf_token():
        if "csrf" not in _session:
            _session["csrf"] = _secrets.token_urlsafe(32)
        return _session["csrf"]
    app.jinja_env.globals["csrf_token"] = _csrf_token

    @app.before_request
    def _csrf_skydd():
        if _req.method != "POST":
            return
        if not app.config.get("CSRF_SKYDD", not app.config.get("TESTING")):
            return
        if _req.path == "/tur-klar":
            return  # beacon utan formulär, ofarlig (sätter bara egen flagga)
        if _req.path == "/betalning/webhook":
            return  # Stripe-anrop utan session; skyddas av signaturkontroll
        skickad = _req.form.get("csrf") or _req.headers.get("X-CSRF")
        if not _session.get("csrf") or skickad != _session["csrf"]:
            abort(400)

    app.register_blueprint(publikt.bp)
    app.register_blueprint(auth.bp)

    # Allt utom startsidan och inloggningen ligger bakom inloggning.
    auth.skydda_blueprint(flode.bp)
    from .vyer import leverantorer as _leverantorer
    auth.skydda_blueprint(_leverantorer.bp)
    auth.skydda_blueprint(api.bp)
    auth.skydda_blueprint(admin.bp, admin=True)
    app.register_blueprint(flode.bp)
    from .vyer import insikter as _insikter
    app.register_blueprint(_insikter.bp)
    app.register_blueprint(_leverantorer.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(api.bp)

    from . import betalning as _betalning
    app.register_blueprint(_betalning.bp)
    app.cli.add_command(_betalning.paminn_trial_command)

    KALLNAMN = {"ted": "TED", "ted-tilldelning": "TED",
                "avtalsutgangar": "Beräknad ur tilldelningsdata",
                "plan-trollhattan": "Trollhättans Stads upphandlingsplan",
                "plan-skovde": "Skövde kommuns upphandlingsplan (Skaraborg)",
                "plan-region-stockholm": "Region Stockholms upphandlingsplan",
                "plan-stockholm": "Stockholms stads upphandlingsplan",
                "plan-adda": "Addas upphandlingsplan",
                "plan-uppsala": "Uppsala kommuns upphandlingsplan",
                "budget-trollhattan": "Trollhättans Stads investeringsplan (MRP)",
                "protokoll-meetingplus": "Kommunala möteshandlingar",
                "kommers": "KommersAnnons",
                "kommers-tilldelning": "KommersAnnons",
                "e-avrop": "e-Avrop", "e_avrop": "e-Avrop", "adda": "Adda", "avropa": "Avropa.se", "hbv": "HBV", "sinfra": "Sinfra",
                "demo": "Demo"}
    app.add_template_filter(lambda k: KALLNAMN.get(k, k), "kallnamn")

    def _kvartal(datum):
        """ISO-datum -> 'Q2 2026' (planerade upphandlingar anger sällan
        exakta dagar, så vi visar aldrig mer precision än kvartal)."""
        if not datum or len(str(datum)) < 7:
            return ""
        ar, manad = str(datum)[:4], int(str(datum)[5:7])
        return f"Q{(manad - 1) // 3 + 1} {ar}"
    app.add_template_filter(_kvartal, "kvartal")

    def _leverantorsdelar(text):
        """Flervinnarsträng -> lista med enskilda bolagsnamn."""
        import re as _re
        return [d.strip() for d in _re.split(r"[,;]", text or "")
                if 2 < len(d.strip()) <= 200]
    app.add_template_filter(_leverantorsdelar, "leverantorsdelar")

    def _bransch(cpv):
        from .cpv import bransch_for_cpv
        return bransch_for_cpv(cpv or "") or ""
    app.add_template_filter(_bransch, "bransch")

    def _formedling(titel):
        from .kategorier import ar_formedlingsavtal
        return ar_formedlingsavtal(titel)
    app.add_template_filter(_formedling, "formedling")

    def _begaran(signal):
        from .vyer.flode import begaran_text
        return begaran_text(signal)
    app.add_template_filter(_begaran, "begaran")

    def _registrator(webbplats):
        from .vyer.flode import registrator_epost
        return registrator_epost(webbplats) or ""
    app.add_template_filter(_registrator, "registrator")
    from .vyer.publikt import slugga
    app.add_template_filter(slugga, "slugga")

    return app
