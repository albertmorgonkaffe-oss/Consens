"""Publika sidor: startsida, teaser-sidor för signaler, sök och sitemap.

Teaser-principen (Tendium-modellen, inte Mercells modal):
- Upphandlingar visas generöst — publik info hos källorna ändå; trafikmagnet.
- Avtalsutgångar låser det säljbara: exakt slutdatum, leverantör, värde och
  antal anbud kräver inloggning. Tidsangivelsen grovavrundas till spann.
- Detaljsidor indexeras (sitemap), söksidan gör det inte (noindex).
"""
import re
import unicodedata

from flask import (Blueprint, Response, g, redirect, render_template,
                   request, url_for)

from ..cpv import BRANSCHER, bransch_for_cpv, prefix_for_bransch
from ..yrken import YRKEN
from ..db import get_db

bp = Blueprint("publikt", __name__)


def slugga(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = t.encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:80] or "signal"


def signal_url(signal) -> str:
    del_ = "upphandling" if signal["typ"] == "upphandling" else "avtalsutgang"
    return f"/{del_}/{slugga(signal['titel'])}-{signal['id']}"


def utgangsspann(man_kvar) -> str:
    """Grovavrundat spann. Exakt datum är betalinnehåll."""
    if man_kvar is None:
        return "okänt"
    if man_kvar < 0:
        return "har löpt ut"
    if man_kvar <= 3:
        return "inom 3 månader"
    if man_kvar <= 6:
        return "inom 3–6 månader"
    if man_kvar <= 12:
        return "inom 6–12 månader"
    if man_kvar <= 18:
        return "inom 12–18 månader"
    return "om mer än 18 månader"


def _hamta_signal(typ: str, slug: str):
    """Plocka id ur slugens svans; 301 till kanonisk URL vid fel slug."""
    m = re.search(r"-(\d+)$", slug)
    if not m:
        return None, None
    rad = get_db().execute(
        "SELECT s.*, o.namn AS org_namn, o.orgnr, o.kontotyp, "
        "o.lan AS org_lan FROM signal s "
        "JOIN organisation o ON o.id = s.organisation_id "
        "WHERE s.id = ? AND s.typ = ?",
        (int(m.group(1)), typ),
    ).fetchone()
    if rad is None:
        return None, None
    kanonisk = signal_url(rad)
    if request.path != kanonisk:
        return rad, redirect(kanonisk, code=301)
    return rad, None


@bp.route("/")
def start():
    if g.get("anvandare"):
        return redirect(url_for("flode.flodet"))
    db = get_db()
    stat = db.execute(
        "SELECT "
        "(SELECT COUNT(*) FROM signal WHERE typ='upphandling' AND dublett_av IS NULL) AS upphandlingar, "
        "(SELECT COUNT(*) FROM signal WHERE typ='avtalsutgang') AS utgangar, "
        "(SELECT COUNT(*) FROM organisation) AS organisationer").fetchone()
    senaste = db.execute(
        "SELECT s.id, s.titel, s.deadline, o.namn AS org_namn "
        "FROM signal s JOIN organisation o ON o.id=s.organisation_id "
        "WHERE s.typ='upphandling' AND s.deadline >= date('now') "
        "ORDER BY COALESCE(s.publicerad, s.upptackt) DESC, s.id DESC LIMIT 3"
    ).fetchall()
    rad = db.execute("SELECT MAX(startad) AS t FROM korningshistorik "
                     "WHERE status='ok'").fetchone()
    senast_hamtad = (rad["t"] or "")[11:16] or None
    return render_template("start.html", stat=stat, senaste=senaste,
                           senast_hamtad=senast_hamtad)


@bp.route("/upphandling/<slug>")
def upphandling(slug):
    rad, omdirigering = _hamta_signal("upphandling", slug)
    if omdirigering:
        return omdirigering
    if rad is None:
        return render_template("404.html"), 404
    return render_template(
        "teaser_upphandling.html", s=rad,
        bransch=bransch_for_cpv(rad["cpv"]),
    )


@bp.route("/avtalsutgang/<slug>")
def avtalsutgang(slug):
    from .flode import man_kvar
    rad, omdirigering = _hamta_signal("avtalsutgang", slug)
    if omdirigering:
        return omdirigering
    if rad is None:
        return render_template("404.html"), 404
    return render_template(
        "teaser_avtalsutgang.html", s=rad,
        bransch=bransch_for_cpv(rad["cpv"]),
        spann=utgangsspann(man_kvar(rad["deadline"])),
    )


@bp.route("/sok")
def sok():
    from ..matchning import for_manga_forsok
    from ..db import get_db as _gdb
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    if for_manga_forsok(_gdb(), "sok", ip, 120, 1):
        return "För många sökningar, vänta en liten stund.", 429
    """Publik sök: fritext + bransch. Övriga filter visas låsta. Noindex."""
    db = get_db()
    q = request.args.get("q", "").strip()
    branscher = request.args.getlist("bransch")
    yrken_valda = request.args.getlist("yrke")

    sql = ("SELECT s.*, o.namn AS org_namn FROM signal s "
           "JOIN organisation o ON o.id = s.organisation_id "
           "WHERE s.dublett_av IS NULL")
    params = []
    if q:
        sql += " AND (s.titel LIKE ? OR o.namn LIKE ?)"
        params += [f"%{q}%"] * 2
    prefixar = [p for b in branscher for p in prefix_for_bransch(b)]
    if prefixar:
        sql += " AND (" + " OR ".join("s.cpv LIKE ?" for _ in prefixar) + ")"
        params += [p + "%" for p in prefixar]
    if yrken_valda:
        sql += (" AND s.id IN (SELECT signal_id FROM signal_yrke WHERE yrke "
                f"IN ({','.join('?' * len(yrken_valda))}))")
        params += yrken_valda
    if q:
        sql += " ORDER BY s.id DESC LIMIT 60"
        rader = db.execute(sql, params).fetchall()
    else:
        # Skyltfönstret: första intrycket för varje ny besökare. Hämta
        # varje signaltyp för sig, annars tränger de högst värderade
        # avtalsutgångarna undan allt annat och blandningen nedan får
        # bara en typ att väva med.
        ordning = (" ORDER BY "
                   "(s.deadline IS NOT NULL AND s.deadline < date('now')), "
                   "(s.prioritet='hog') DESC, "
                   "s.varde IS NULL, s.varde DESC, s.id DESC LIMIT 40")
        rader = []
        for typ in ("upphandling", "avtalsutgang", "planerad"):
            rader += db.execute(sql + " AND s.typ = ?" + ordning,
                                params + [typ]).fetchall()
    if not q:
        # Väv ihop signaltyperna så skyltfönstret visar upphandlingar,
        # avtalsutgångar och planerade sida vid sida i stället för en
        # enda typ.
        hinkar: dict = {}
        for r in rader:
            hinkar.setdefault(r["typ"], []).append(r)
        vavda, ordning = [], sorted(hinkar, key=lambda t: -len(hinkar[t]))
        while any(hinkar[t] for t in ordning) and len(vavda) < 50:
            for t in ordning:
                if hinkar[t]:
                    vavda.append(hinkar[t].pop(0))
        rader = vavda[:50]
    signaler = [dict(r, url=signal_url(r))
                for r in rader]
    return render_template(
        "teaser_sok.html", signaler=signaler, q=q,
        branscher=BRANSCHER, yrkeskategorier=YRKEN,
        valda=branscher, valda_yrken=yrken_valda,
    )


@bp.route("/integritetspolicy")
def integritetspolicy():
    from datetime import date
    return render_template("integritetspolicy.html",
                           idag=date.today().isoformat())


@bp.route("/villkor")
def villkor():
    from datetime import date
    return render_template("villkor.html", idag=date.today().isoformat())


@bp.route("/bevakar/<slug>")
def bevakar(slug):
    """Fördjupning bakom korten i "Vad vi bevakar". Nås bara därifrån."""
    from ..bevakar import SIDOR
    sida = SIDOR.get(slug)
    if sida is None:
        return render_template("404.html"), 404
    ovriga = [(s2, d["rubrik"]) for s2, d in SIDOR.items() if s2 != slug]
    return render_template("bevakar.html", sida=sida, slug=slug,
                           ovriga=ovriga)


@bp.route("/faq")
def faq():
    return render_template("faq.html")


@bp.route("/produkt")
def produkt():
    return render_template("produkt.html")


@bp.route("/ansok", methods=("GET", "POST"))
def ansok():
    """Publikt ansökningsformulär -> kontoförfrågan i admin."""
    if request.method == "POST":
        from ..matchning import for_manga_forsok
        from ..db import get_db as _gdb
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
        if for_manga_forsok(_gdb(), "ansok", ip, 5, 60):
            return render_template("ansok.html",
                                   fel="För många ansökningar från din "
                                       "adress. Försök igen om en timme."), 429
        namn = request.form.get("namn", "").strip()
        epost = request.form.get("epost", "").strip()
        if namn and epost:
            db = get_db()
            db.execute(
                "INSERT INTO kontoforfragan (namn, epost, foretag, "
                "meddelande) VALUES (?,?,?,?)",
                (namn, epost, request.form.get("foretag", "").strip() or None,
                 request.form.get("meddelande", "").strip() or None))
            db.commit()
            return render_template("ansok.html", skickad=True)
    return render_template("ansok.html", skickad=False)


@bp.route("/testa")
def testa():
    """Mellansida före Checkout: villkoren i klartext, vad som ingår,
    och vägen till paketpris för flera licenser."""
    return render_template("testa.html")


@bp.route("/sitemap.xml")
def sitemap():
    bas = request.url_root.rstrip("/")
    from ..bevakar import SIDOR
    urlar = [f"{bas}{p}" for p in
             ["/", "/testa", "/produkt", "/faq", "/villkor",
              "/integritetspolicy"]]
    urlar += [f"{bas}/bevakar/{slug}" for slug in SIDOR]
    rader = get_db().execute(
        "SELECT s.id, s.typ, s.titel FROM signal s ORDER BY s.id"
    ).fetchall()
    urlar += [f"{bas}{signal_url(r)}" for r in rader]
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(f"  <url><loc>{u}</loc></url>" for u in urlar)
        + "\n</urlset>\n"
    )
    return Response(xml, mimetype="application/xml")


@bp.route("/robots.txt")
def robots():
    bas = request.url_root.rstrip("/")
    return Response(
        f"User-agent: *\nDisallow: /sok\nDisallow: /flode\n"
        f"Disallow: /admin\nDisallow: /api\n"
        f"Sitemap: {bas}/sitemap.xml\n",
        mimetype="text/plain",
    )
