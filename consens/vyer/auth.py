"""Autentisering och behörighet.

Två nivåer: 'anvandare' (hela appen) och 'admin' (även /admin).
Allt utom startsidan och inloggningen kräver inloggning.
"""
import functools
from datetime import date, timedelta

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

from ..db import get_db

bp = Blueprint("auth", __name__)

ROLLER = {"trial": "Trial (14 dagar)", "kompis": "Kompis",
          "anvandare": "Normalanvändare",
          "admin": "Admin"}
TRIAL_DAGAR = 14


def trial_utgangen(rad) -> bool:
    if rad["roll"] != "trial" or not rad["trial_start"]:
        return False
    start = date.fromisoformat(rad["trial_start"][:10])
    return date.today() > start + timedelta(days=TRIAL_DAGAR)


def _tillgang_passerad(rad) -> bool:
    """Tidsbegränsade konton (giltig_till) stängs även för redan
    inloggade sessioner, inte bara vid nästa inloggning."""
    return (rad["roll"] != "admin" and rad["giltig_till"]
            and rad["giltig_till"] < date.today().isoformat())


@bp.before_app_request
def ladda_anvandare():
    anvandare_id = session.get("anvandare_id")
    g.anvandare = None
    if anvandare_id is not None:
        rad = get_db().execute(
            "SELECT * FROM anvandare WHERE id = ? AND aktiv = 1",
            (anvandare_id,),
        ).fetchone()
        g.betalvantande = None
        if rad is not None and (trial_utgangen(rad)
                                or _tillgang_passerad(rad)):
            # Utgången trial eller passerad giltighet: ingen access,
            # men personen ska kunna nå betalsidan och uppgradera.
            g.betalvantande = rad
        elif rad is not None:
            g.anvandare = rad


def kraver_inloggning(vy):
    @functools.wraps(vy)
    def wrapper(**kwargs):
        if g.anvandare is None:
            return redirect(url_for("auth.logga_in", nasta=request.path))
        return vy(**kwargs)
    return wrapper


def kraver_admin(vy):
    @functools.wraps(vy)
    def wrapper(**kwargs):
        if g.anvandare is None:
            return redirect(url_for("auth.logga_in", nasta=request.path))
        if g.anvandare["roll"] != "admin":
            return render_template("403.html"), 403
        return vy(**kwargs)
    return wrapper


def skydda_blueprint(blueprint, admin=False):
    """Kräv inloggning (eller admin) för ett helt blueprint."""
    @blueprint.before_request
    def _vakt():
        if g.anvandare is None:
            return redirect(url_for("auth.logga_in", nasta=request.path))
        if admin and g.anvandare["roll"] != "admin":
            return render_template("403.html"), 403


@bp.route("/logga-in", methods=("GET", "POST"))
def logga_in():
    from ..matchning import for_manga_forsok, sanera
    if g.anvandare is not None:
        return redirect(url_for("flode.flodet"))
    fel = None
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
        epost_ratelimit = sanera(request.form.get("epost", ""), 254).lower()
        db_rl = get_db()
        if (for_manga_forsok(db_rl, "login", f"{ip}|{epost_ratelimit}", 10, 15)
                or for_manga_forsok(db_rl, "login-ip", ip, 30, 15)):
            return render_template("logga_in.html",
                                   fel="För många inloggningsförsök. "
                                       "Vänta en kvart och försök igen."), 429
        epost = request.form.get("epost", "").strip().lower()
        losenord = request.form.get("losenord", "")
        rad = get_db().execute(
            "SELECT * FROM anvandare WHERE epost = ? AND aktiv = 1", (epost,)
        ).fetchone()
        if rad is None or not check_password_hash(
            rad["losenord_hash"], losenord
        ):
            fel = "Fel e-post eller lösenord."
        elif (trial_utgangen(rad)
              or (rad["roll"] != "admin" and rad["giltig_till"]
                  and rad["giltig_till"] < date.today().isoformat())):
            # Utgången access: släpp in till betalsidan i stället för
            # att stänga dörren helt.
            session.clear()
            session["anvandare_id"] = rad["id"]
            return redirect(url_for("betalning.uppgradera"))
        else:
            session.clear()
            session["anvandare_id"] = rad["id"]
            nasta = request.args.get("nasta", "")
            if not nasta.startswith("/"):  # skydd mot öppen redirect
                nasta = url_for("flode.flodet")
            return redirect(nasta)
    return render_template("logga_in.html", fel=fel)


@bp.route("/logga-ut")
def logga_ut():
    session.clear()
    return redirect(url_for("publikt.start"))


def skapa_anvandare(db, epost, losenord, namn=None, roll="anvandare"):
    db.execute(
        "INSERT INTO anvandare (epost, namn, losenord_hash, roll, "
        "trial_start) VALUES (?, ?, ?, ?, ?)",
        (epost.strip().lower(), namn, generate_password_hash(losenord), roll,
         date.today().isoformat() if roll == "trial" else None),
    )
    db.commit()


@bp.route("/glomt-losenord", methods=("GET", "POST"))
def glomt_losenord():
    """Neutralt svar oavsett om adressen finns - avslöjar inga konton."""
    skickat = False
    if request.method == "POST":
        from ..matchning import for_manga_forsok
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
        if for_manga_forsok(get_db(), "glomt", ip, 5, 60):
            return render_template("glomt_losenord.html",
                                   fel="För många försök. Vänta en stund."), 429
        epost = (request.form.get("epost") or "").strip().lower()
        db = get_db()
        rad = db.execute("SELECT id FROM anvandare WHERE epost=?",
                         (epost,)).fetchone()
        if rad:
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO losenordsaterstallning "
                       "(anvandare_id, token) VALUES (?, ?)",
                       (rad["id"], token))
            db.commit()
            lank = request.url_root.rstrip("/") + url_for(
                "auth.aterstall", token=token)
            from ..digest import _skicka
            _skicka(epost, "Återställ ditt lösenord hos Consens",
                    "Hej!\n\nKlicka på länken för att välja ett nytt "
                    "lösenord. Länken gäller i 60 minuter.\n\n"
                    f"{lank}\n\nOm du inte har begärt detta kan du "
                    "bortse från mejlet.\n\nConsens")
        skickat = True
    return render_template("glomt_losenord.html", skickat=skickat)


def _giltig_aterstallning(db, token):
    return db.execute(
        "SELECT a.* FROM losenordsaterstallning a WHERE a.token=? "
        "AND a.anvand=0 AND a.skapad > datetime('now', '-60 minutes')",
        (token,)).fetchone()


@bp.route("/aterstall/<token>", methods=("GET", "POST"))
def aterstall(token):
    db = get_db()
    rad = _giltig_aterstallning(db, token)
    if rad is None:
        return render_template("aterstall.html", ogiltig=True), 410
    konto = db.execute("SELECT foretag FROM anvandare WHERE id=?",
                       (rad["anvandare_id"],)).fetchone()
    visa_foretag = konto is not None and not konto["foretag"]
    fel = None
    if request.method == "POST":
        losenord = request.form.get("losenord") or ""
        if len(losenord) < 8:
            fel = "Lösenordet behöver minst 8 tecken."
        else:
            foretag = (request.form.get("foretag") or "").strip()
            if visa_foretag and foretag:
                db.execute("UPDATE anvandare SET foretag=? WHERE id=?",
                           (foretag[:120], rad["anvandare_id"]))
            db.execute("UPDATE anvandare SET losenord_hash=? WHERE id=?",
                       (generate_password_hash(losenord),
                        rad["anvandare_id"]))
            db.execute("UPDATE losenordsaterstallning SET anvand=1 "
                       "WHERE id=?", (rad["id"],))
            db.commit()
            flash("Lösenordet är bytt. Logga in med det nya.")
            return redirect(url_for("auth.logga_in"))
    return render_template("aterstall.html", ogiltig=False, fel=fel,
                           visa_foretag=visa_foretag)


@bp.route("/inbjudan/<token>", methods=("GET", "POST"))
def inbjudan(token):
    db = get_db()
    rad = db.execute(
        "SELECT * FROM inbjudan WHERE token=? AND anvand=0 "
        "AND skapad > datetime('now', '-14 days')", (token,)).fetchone()
    if rad is None:
        return render_template("inbjudan.html", ogiltig=True), 410
    fel = None
    if request.method == "POST":
        from ..matchning import sanera
        namn = sanera(request.form.get("namn"))
        foretag = sanera(request.form.get("foretag"))
        losenord = (request.form.get("losenord") or "")[:200]
        if len(losenord) < 8:
            fel = "Lösenordet behöver minst 8 tecken."
        elif not request.form.get("godkanner"):
            fel = ("Du behöver godkänna användarvillkoren och "
                   "personuppgiftsbehandlingen för att skapa kontot.")
        elif db.execute("SELECT 1 FROM anvandare WHERE epost=?",
                        (rad["epost"],)).fetchone():
            fel = "Det finns redan ett konto med den här adressen."
        else:
            nyhetsbrev = 1 if request.form.get("nyhetsbrev") else 0
            ny_id = db.execute(
                "INSERT INTO anvandare (epost, namn, foretag, losenord_hash, "
                "roll, trial_start, giltig_till, samtycke_marknadsforing, "
                "tur_visad) VALUES (?,?,?,?,?,?,?,?,0)",
                (rad["epost"], namn or None, foretag or None,
                 generate_password_hash(losenord), rad["roll"],
                 date.today().isoformat() if rad["roll"] == "trial" else None,
                 rad["giltig_till"], nyhetsbrev)).lastrowid
            db.execute("UPDATE inbjudan SET anvand=1 WHERE id=?", (rad["id"],))
            db.commit()
            session.clear()
            session["anvandare_id"] = ny_id
            flash("Välkommen till Consens! Kontot är klart.")
            return redirect(url_for("flode.flodet"))
    return render_template("inbjudan.html", ogiltig=False, fel=fel,
                           epost=rad["epost"], roll=ROLLER.get(rad["roll"]))


@bp.route("/installningar", methods=("GET", "POST"))
def installningar():
    if not g.get("anvandare"):
        return redirect(url_for("auth.logga_in"))
    db = get_db()
    aid = g.anvandare["id"]
    meddelande = fel = None
    if request.method == "POST":
        atgard = request.form.get("atgard")
        if atgard == "tema":
            morkt = 1 if request.form.get("mork_lage") == "1" else 0
            db.execute("UPDATE anvandare SET mork_lage=? WHERE id=?",
                       (morkt, aid))
            db.commit()
            svar = redirect(url_for("auth.installningar"))
            svar.set_cookie("tema", "mork" if morkt else "ljus",
                            max_age=31536000, samesite="Lax")
            return svar
        if atgard == "uppgifter":
            from ..matchning import sanera
            db.execute("UPDATE anvandare SET namn=?, foretag=? WHERE id=?",
                       (sanera(request.form.get("namn")) or None,
                        sanera(request.form.get("foretag")) or None, aid))
            db.commit()
            meddelande = "Uppgifterna sparade."
        elif atgard == "epost":
            ny = (request.form.get("epost") or "").strip().lower()
            if "@" not in ny:
                fel = "Ange en giltig e-postadress."
            elif db.execute("SELECT 1 FROM anvandare WHERE epost=? AND id!=?",
                            (ny, aid)).fetchone():
                fel = "Adressen används redan."
            else:
                db.execute("UPDATE anvandare SET epost=? WHERE id=?",
                           (ny, aid))
                db.commit()
                meddelande = "E-postadressen är bytt."
        elif atgard == "losenord":
            nuvarande = request.form.get("nuvarande") or ""
            nytt = request.form.get("nytt") or ""
            rad = db.execute("SELECT losenord_hash FROM anvandare WHERE id=?",
                             (aid,)).fetchone()
            if not check_password_hash(rad["losenord_hash"], nuvarande):
                fel = "Nuvarande lösenord stämmer inte."
            elif len(nytt) < 8:
                fel = "Nya lösenordet behöver minst 8 tecken."
            else:
                db.execute("UPDATE anvandare SET losenord_hash=? WHERE id=?",
                           (generate_password_hash(nytt), aid))
                db.commit()
                meddelande = "Lösenordet är bytt."
    a = db.execute("SELECT * FROM anvandare WHERE id=?", (aid,)).fetchone()
    return render_template("installningar.html", a=a,
                           rollnamn=ROLLER.get(a["roll"], a["roll"]),
                           meddelande=meddelande, fel=fel)


@bp.route("/kontakt", methods=("GET", "POST"))
def kontakt():
    if not g.get("anvandare"):
        return redirect(url_for("auth.logga_in"))
    skickat = False
    if request.method == "POST":
        amne = (request.form.get("amne") or "").strip() or "Meddelande via kontaktformuläret"
        meddelande = (request.form.get("meddelande") or "").strip()[:5000]
        from ..matchning import for_manga_forsok
        if for_manga_forsok(get_db(), "kontakt", str(g.anvandare["id"]), 10, 60):
            meddelande = None
            flash("För många meddelanden på kort tid. Vänta en stund.")
        if meddelande:
            a = g.anvandare
            from ..digest import _skicka
            _skicka("info@consens.se", f"[Kontakt] {amne}",
                    f"Från: {a['namn'] or '-'} <{a['epost']}>\n"
                    f"Företag: {a['foretag'] or '-'}\n\n{meddelande}",
                    typ="kontakt", anvandare_id=a["id"])
            skickat = True
    return render_template("kontakt.html", skickat=skickat)


@bp.route("/tur-klar", methods=("POST",))
def tur_klar():
    if g.get("anvandare"):
        db = get_db()
        db.execute("UPDATE anvandare SET tur_visad=1 WHERE id=?",
                   (g.anvandare["id"],))
        db.commit()
    return ("", 204)


@bp.route("/tur-igen", methods=("POST",))
def tur_igen():
    if not g.get("anvandare"):
        return redirect(url_for("auth.logga_in"))
    db = get_db()
    db.execute("UPDATE anvandare SET tur_visad=0 WHERE id=?",
               (g.anvandare["id"],))
    db.commit()
    return redirect(url_for("flode.flodet"))
