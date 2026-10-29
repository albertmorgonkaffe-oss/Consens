import secrets
"""Admin: Översikt, Statistik, Utskick, Användare, Kontoförfrågningar,
Sök signal. Hela blueprintet kräver admin-roll (skyddas i appfabriken)."""
import json
import re
from urllib.parse import parse_qs

from datetime import date, timedelta

from flask import (Blueprint, flash, g, redirect, render_template,
                   request, url_for)

from ..db import get_db
from .auth import ROLLER, skapa_anvandare

bp = Blueprint("admin", __name__, url_prefix="/admin")

SIGNALTYPER = {"avtalsutgang": "Avtalsutgång", "upphandling": "Upphandling"}


# ---------- Redigera organisation ----------

@bp.route("/organisationer/<int:oid>/redigera", methods=("GET", "POST"))
def redigera_organisation(oid):
    from ..matchning import normalisera_orgnr
    from .flode import KONTOTYPER, LAN
    db = get_db()
    org = db.execute("SELECT * FROM organisation WHERE id=?",
                     (oid,)).fetchone()
    if org is None:
        return render_template("404.html"), 404
    varden = dict(org)
    fel = []
    if request.method == "POST":
        varden = dict(
            namn=request.form.get("namn", "").strip(),
            orgnr=request.form.get("orgnr", "").strip(),
            kontotyp=request.form.get("kontotyp", ""),
            lan=request.form.get("lan", "").strip(),
            webbplats=request.form.get("webbplats", "").strip())
        if not varden["namn"]:
            fel.append("Namn måste anges.")
        elif len(varden["namn"]) > 200:
            fel.append("Namnet är för långt (max 200 tecken).")
        orgnr = None
        if varden["orgnr"]:
            orgnr = normalisera_orgnr(varden["orgnr"])
            if orgnr is None:
                fel.append("Organisationsnumret ser inte giltigt ut "
                           "(förväntat format: 212000-0159).")
            else:
                dubblett = db.execute(
                    "SELECT id, namn FROM organisation "
                    "WHERE orgnr=? AND id != ?", (orgnr, oid)).fetchone()
                if dubblett:
                    fel.append(f"Organisationsnumret används redan av "
                               f"\u201d{dubblett['namn']}\u201d (id "
                               f"{dubblett['id']}) - två organisationer "
                               "kan inte dela nummer.")
        if varden["kontotyp"] not in KONTOTYPER:
            fel.append("Välj en organisationstyp.")
        if varden["lan"] and varden["lan"] not in LAN:
            fel.append("Okänt län.")
        webbplats = varden["webbplats"]
        if webbplats and not webbplats.startswith(("http://", "https://")):
            webbplats = "https://" + webbplats
        if not fel:
            db.execute(
                "UPDATE organisation SET namn=?, orgnr=?, kontotyp=?, "
                "lan=?, webbplats=? WHERE id=?",
                (varden["namn"], orgnr, varden["kontotyp"],
                 varden["lan"] or None, webbplats or None, oid))
            db.commit()
            flash("Organisationen uppdaterad.")
            return redirect(url_for("flode.organisation_id", oid=oid))
    return render_template("admin_org_redigera.html", org=org,
                           varden=varden, fel=fel,
                           kontotyper=KONTOTYPER, alla_lan=LAN)


# ---------- Översikt ----------

# Förväntad körfrekvens per källfamilj (dagar) - grunden för
# hälsobedömningen: en källa som inte hörts av på länge är lika
# illa som en som felar, men syns annars inte alls.
FORVANTAT_INTERVALL = {
    "plan-": 8, "budget-": 8, "protokoll-": 8,   # veckovis (måndagar)
    "inkop-": 200,                                # halvårsvis
    "": 2,                                        # dagliga källor
}


def _kallhalsa(db):
    """Status per källa: senaste körning, ålder och bedömning."""
    from datetime import datetime as _dt
    rader = db.execute(
        "SELECT kalla, MAX(startad) AS senast, status, antal_nya, fel "
        "FROM korningshistorik GROUP BY kalla ORDER BY kalla").fetchall()
    nu = _dt.now()
    ut = []
    for r in rader:
        try:
            alder = (nu - _dt.fromisoformat(r["senast"])).days
        except Exception:
            alder = None
        grans = next((v for k, v in FORVANTAT_INTERVALL.items()
                      if k and r["kalla"].startswith(k)),
                     FORVANTAT_INTERVALL[""])
        if r["status"] == "fel":
            lage, etikett = "fel", "Fel vid senaste körning"
        elif alder is None:
            lage, etikett = "okand", "Okänd"
        elif alder > grans * 2:
            lage, etikett = "fel", f"Tyst i {alder} dagar"
        elif alder > grans:
            lage, etikett = "varning", f"Sen ({alder} dagar)"
        else:
            lage, etikett = "ok", "Aktuell"
        ut.append(dict(kalla=r["kalla"], senast=r["senast"],
                       status=r["status"], antal_nya=r["antal_nya"],
                       fel=r["fel"], alder=alder, lage=lage,
                       etikett=etikett))
    ordning = {"fel": 0, "varning": 1, "okand": 2, "ok": 3}
    ut.sort(key=lambda x: (ordning[x["lage"]], x["kalla"]))
    return ut


@bp.route("/")
def oversikt():
    db = get_db()
    historik = db.execute(
        "SELECT * FROM korningshistorik ORDER BY id DESC LIMIT 50").fetchall()
    per_kalla = db.execute(
        "SELECT kalla, MAX(startad) AS senast, status, antal_nya "
        "FROM korningshistorik GROUP BY kalla ORDER BY kalla").fetchall()
    antal = db.execute(
        "SELECT (SELECT COUNT(*) FROM signal) AS signaler, "
        "(SELECT COUNT(*) FROM avtal) AS avtal, "
        "(SELECT COUNT(*) FROM organisation) AS organisationer, "
        "(SELECT COUNT(*) FROM anvandare WHERE aktiv=1) AS anvandare"
    ).fetchone()
    from ..cache import status as cache_status
    halsa = _kallhalsa(db)
    problem = [h for h in halsa if h["lage"] in ("fel", "varning")]
    return render_template("admin_oversikt.html", historik=historik,
                           per_kalla=per_kalla, antal=antal,
                           halsa=halsa, problem=problem,
                           cache=cache_status())


@bp.route("/rensa-cache", methods=("POST",))
def rensa_cache():
    from ..cache import rensa
    rensa()
    flash("Cachen rensad, nästa sidvisning räknar om.")
    return redirect(url_for("admin.oversikt"))


# ---------- Statistik ----------

@bp.route("/statistik")
def statistik():
    db = get_db()
    dagar = request.args.get("dagar", "30")
    if dagar not in ("7", "30", "90"):
        dagar = "30"
    sedan = f"-{dagar} days"

    mest_klickade = db.execute(
        "SELECT s.id, s.titel, s.typ, o.namn AS org_namn, COUNT(*) AS klick "
        "FROM klick k JOIN signal s ON s.id=k.signal_id "
        "JOIN organisation o ON o.id=s.organisation_id "
        "WHERE k.tidpunkt >= datetime('now', ?) "
        "GROUP BY s.id ORDER BY klick DESC LIMIT 15", (sedan,)).fetchall()

    mest_sparade_signaler = db.execute(
        "SELECT s.titel, s.typ, o.namn AS org_namn, COUNT(*) AS antal "
        "FROM sparad_signal sp JOIN signal s ON s.id=sp.signal_id "
        "JOIN organisation o ON o.id=s.organisation_id "
        "GROUP BY s.id ORDER BY antal DESC LIMIT 10").fetchall()
    mest_sparade_org = db.execute(
        "SELECT o.namn, COUNT(*) AS antal FROM ("
        "  SELECT s.organisation_id AS oid FROM sparad_signal sp"
        "    JOIN signal s ON s.id=sp.signal_id"
        "  UNION ALL"
        "  SELECT a.organisation_id FROM sparad_avtal sp"
        "    JOIN avtal a ON a.id=sp.avtal_id) x "
        "JOIN organisation o ON o.id=x.oid "
        "GROUP BY o.id ORDER BY antal DESC LIMIT 10").fetchall()

    # Populäraste filterattribut ur bevakningarna
    attribut: dict = {}
    for b in db.execute("SELECT params FROM bevakning").fetchall():
        d = parse_qs(b["params"])
        for nyckel, etikett in (("typ", "Typ"), ("bransch", "Bransch"),
                                ("yrke", "Tjänst"), ("lan", "Län"),
                                ("kontotyp", "Organisationstyp")):
            for v in d.get(nyckel, []):
                visning = SIGNALTYPER.get(v, v)
                attribut[f"{etikett}: {visning}"] = \
                    attribut.get(f"{etikett}: {visning}", 0) + 1
    populara_attribut = sorted(attribut.items(), key=lambda x: -x[1])[:15]

    per_roll = db.execute(
        "SELECT roll, COUNT(*) AS antal FROM anvandare WHERE aktiv=1 "
        "GROUP BY roll").fetchall()

    return render_template(
        "admin_statistik.html", dagar=dagar, mest_klickade=mest_klickade,
        mest_sparade_signaler=mest_sparade_signaler,
        mest_sparade_org=mest_sparade_org,
        populara_attribut=populara_attribut, per_roll=per_roll,
        roller=ROLLER, signaltyper=SIGNALTYPER,
        flik=request.args.get("flik", "inloggade"),
        publik=_publik_statistik(db, sedan))


def _publik_statistik(db, sedan):
    """Besöksstatistik för den publika delen (utloggade besökare)."""
    totalt = db.execute(
        "SELECT COUNT(*) FROM besok WHERE tidpunkt >= datetime('now', ?)",
        (sedan,)).fetchone()[0]
    per_sida = db.execute(
        "SELECT sokvag, COUNT(*) AS antal FROM besok "
        "WHERE tidpunkt >= datetime('now', ?) "
        "GROUP BY sokvag ORDER BY antal DESC LIMIT 15", (sedan,)).fetchall()
    per_dag = db.execute(
        "SELECT date(tidpunkt) AS dag, COUNT(*) AS antal FROM besok "
        "WHERE tidpunkt >= datetime('now', ?) "
        "GROUP BY dag ORDER BY dag DESC LIMIT 31", (sedan,)).fetchall()
    dagar_med = max(1, len(per_dag))
    return dict(totalt=totalt, per_sida=per_sida, per_dag=per_dag,
                snitt_dag=round(totalt / dagar_med, 1) if totalt else 0)


# ---------- Utskick ----------

def _mottagare(db, form):
    """Beräkna mottagarlistan enligt formulärets val."""
    typ = form.get("typ", "marknadsforing")
    sql = "SELECT DISTINCT u.id, u.epost FROM anvandare u WHERE u.aktiv=1"
    params: list = []
    if typ == "marknadsforing":
        sql += " AND u.samtycke_marknadsforing=1"
    kategorier = form.getlist("kategori")
    if kategorier:
        sql += f" AND u.roll IN ({','.join('?' * len(kategorier))})"
        params += kategorier
    signaltyper = form.getlist("signaltyp")
    if signaltyper:
        villkor = " OR ".join("b.params LIKE ?" for _ in signaltyper)
        sql += (" AND u.id IN (SELECT b.anvandare_id FROM bevakning b "
                f"WHERE {villkor})")
        params += [f"%typ={t}%" for t in signaltyper]
    branscher = form.getlist("bransch")
    if branscher:
        villkor = " OR ".join("b.params LIKE ?" for _ in branscher)
        sql += (" AND u.id IN (SELECT b.anvandare_id FROM bevakning b "
                f"WHERE {villkor})")
        params += [f"%bransch={b}%" for b in branscher]
    return db.execute(sql, params).fetchall()


SIGNATUR = "\n\n—\nConsens\nconsens.se"


@bp.route("/utskick", methods=("GET", "POST"))
def utskick():
    from ..cpv import BRANSCHER
    db = get_db()
    forhandsgranskning = None
    mottagare = []
    if request.method == "POST":
        mottagare = _mottagare(db, request.form)
        if request.form.get("atgard") == "skicka" and mottagare:
            from ..digest import _skicka
            amne = request.form.get("amne", "").strip()
            brodtext = request.form.get("brodtext", "").strip() + SIGNATUR
            for m in mottagare:
                _skicka(m["epost"], amne, brodtext)
            db.execute(
                "INSERT INTO utskick (typ, amne, brodtext, mottagarfilter, "
                "antal_mottagare, skickad_av) VALUES (?,?,?,?,?,?)",
                (request.form.get("typ"), amne, brodtext,
                 json.dumps({k: request.form.getlist(k) for k in
                             ("kategori", "signaltyp", "bransch")},
                            ensure_ascii=False),
                 len(mottagare), g.anvandare["id"]))
            db.commit()
            return redirect(url_for("admin.utskick", skickat=len(mottagare)))
        forhandsgranskning = {
            "amne": request.form.get("amne", ""),
            "brodtext": request.form.get("brodtext", "") + SIGNATUR,
        }
    tidigare = db.execute(
        "SELECT u.*, a.epost AS avsandare FROM utskick u "
        "LEFT JOIN anvandare a ON a.id=u.skickad_av "
        "ORDER BY u.id DESC LIMIT 20").fetchall()
    return render_template(
        "admin_utskick.html", roller=ROLLER, signaltyper=SIGNALTYPER,
        branscher=BRANSCHER, mottagare=mottagare,
        forhandsgranskning=forhandsgranskning, form=request.form,
        tidigare=tidigare, skickat=request.args.get("skickat"))


# ---------- Användare ----------

@bp.route("/anvandare", methods=("GET", "POST"))
def anvandare():
    db = get_db()
    fel = None
    if request.method == "POST":
        atgard = request.form.get("atgard")
        uid = request.form.get("uid", type=int)
        if atgard == "skapa":
            try:
                skapa_anvandare(
                    db, request.form["epost"], request.form["losenord"],
                    request.form.get("namn") or None,
                    request.form.get("roll", "anvandare"))
            except Exception:
                fel = "Kunde inte skapa användaren (finns e-posten redan?)."
        elif atgard == "roll" and uid:
            ny = request.form.get("roll")
            if ny in ROLLER:
                db.execute(
                    "UPDATE anvandare SET roll=?, trial_start=CASE WHEN ? "
                    "= 'trial' THEN date('now') ELSE trial_start END "
                    "WHERE id=?", (ny, ny, uid))
        elif atgard == "vaxla" and uid:
            db.execute("UPDATE anvandare SET aktiv = 1 - aktiv WHERE id=?",
                       (uid,))
        elif atgard == "samtycke" and uid:
            db.execute("UPDATE anvandare SET samtycke_marknadsforing = "
                       "1 - samtycke_marknadsforing WHERE id=?", (uid,))
        db.commit()

    lista = db.execute(
        "SELECT u.*, (SELECT COUNT(*) FROM bevakning b "
        "WHERE b.anvandare_id=u.id) AS bevakningar, "
        "(SELECT COUNT(*) FROM sparad_signal sp "
        "WHERE sp.anvandare_id=u.id) AS sparade "
        "FROM anvandare u ORDER BY u.skapad DESC").fetchall()
    vantande = db.execute(
        "SELECT i.*, "
        "CAST(julianday(i.skapad, '+14 days') - julianday('now') AS INTEGER)"
        " AS dagar_kvar "
        "FROM inbjudan i WHERE i.anvand=0 ORDER BY i.id DESC").fetchall()
    return render_template("admin_anvandare.html", lista=lista,
                           vantande=vantande,
                           roller=ROLLER, fel=fel)


# ---------- Kontoförfrågningar ----------

@bp.route("/kontoforfragningar", methods=("GET", "POST"))
def kontoforfragningar():
    db = get_db()
    if request.method == "POST":
        fid = request.form.get("fid", type=int)
        status = request.form.get("status")
        if fid and status in ("hanterad", "avvisad", "ny"):
            db.execute("UPDATE kontoforfragan SET status=? WHERE id=?",
                       (status, fid))
            db.commit()
    lista = db.execute(
        "SELECT * FROM kontoforfragan ORDER BY "
        "CASE status WHEN 'ny' THEN 0 ELSE 1 END, id DESC").fetchall()
    return render_template("admin_kontoforfragningar.html", lista=lista)


# ---------- Sök signal ----------

@bp.route("/sok-signal")
def sok_signal():
    db = get_db()
    ref = request.args.get("ref", "").strip()
    resultat = fel = None
    if ref:
        m = re.fullmatch(r"[Cc]-?(\d{1,8})", ref)
        if not m:
            fel = "Kontrollera formatet. Referensnummer skrivs C-xxxxxx."
        else:
            sid = int(m.group(1))
            s = db.execute(
                "SELECT s.*, o.namn AS org_namn, o.orgnr "
                "FROM signal s JOIN organisation o "
                "ON o.id=s.organisation_id WHERE s.id=?", (sid,)).fetchone()
            if s is None:
                fel = f"Ingen signal hittades med referensnummer {ref}."
            else:
                resultat = {
                    "signal": s,
                    "korning": db.execute(
                        "SELECT * FROM korningshistorik WHERE id=?",
                        (s["korning_id"],)).fetchone()
                    if s["korning_id"] else None,
                    "yrken": [r["yrke"] for r in db.execute(
                        "SELECT yrke FROM signal_yrke WHERE signal_id=?",
                        (sid,)).fetchall()],
                    "klick": db.execute(
                        "SELECT COUNT(*) c FROM klick WHERE signal_id=?",
                        (sid,)).fetchone()["c"],
                    "sparad_av": db.execute(
                        "SELECT COUNT(*) c FROM sparad_signal "
                        "WHERE signal_id=?", (sid,)).fetchone()["c"],
                    "i_listor": db.execute(
                        "SELECT COUNT(*) c FROM lista_post WHERE signal_id=?",
                        (sid,)).fetchone()["c"],
                    "avtal": db.execute(
                        "SELECT * FROM avtal WHERE id=?",
                        (s["avtal_id"],)).fetchone()
                    if s["avtal_id"] else None,
                }
    return render_template("admin_sok_signal.html", ref=ref,
                           resultat=resultat, fel=fel)


@bp.route("/anvandare/inbjudan/<int:iid>/ta-bort", methods=("POST",))
def inbjudan_ta_bort(iid):
    db = get_db()
    db.execute("DELETE FROM inbjudan WHERE id=? AND anvand=0", (iid,))
    db.commit()
    flash("Inbjudan makulerad.")
    return redirect(url_for("admin.anvandare"))


@bp.route("/anvandare/bjud-in", methods=("POST",))
def bjud_in():
    db = get_db()
    epost = (request.form.get("epost") or "").strip().lower()
    roll = request.form.get("roll", "anvandare")
    if roll not in ROLLER:
        roll = "anvandare"
    dagar = request.form.get("dagar", type=int)
    giltig_till = ((date.today() + timedelta(days=dagar)).isoformat()
                   if dagar else None)
    if not epost or "@" not in epost:
        flash("Ange en giltig e-postadress.")
        return redirect(url_for("admin.anvandare"))
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO inbjudan (epost, roll, giltig_till, token, "
               "skapad_av) VALUES (?,?,?,?,?)",
               (epost, roll, giltig_till, token, g.anvandare["id"]))
    db.commit()
    lank = request.url_root.rstrip("/") + url_for("auth.inbjudan", token=token)
    from ..digest import _skicka
    antal_org = db.execute("SELECT COUNT(*) FROM organisation").fetchone()[0]
    org_text = f"{antal_org:,}".replace(",", " ")
    gick = _skicka(epost, "Du är inbjuden till Consens",
                   "Hej!\n\nDu har blivit inbjuden till Consens, tjänsten "
                   "som bevakar upphandlingar och avtalsutgångar för "
                   f"{org_text} organisationer.\n\n"
                   f"Skapa ditt konto här (länken gäller i 14 dagar):\n"
                   f"{lank}\n\nVänliga hälsningar\nConsens", typ="inbjudan")
    if gick:
        flash(f"Inbjudan skickad till {epost}. "
              f"Länken kan också delas: {lank}")
    else:
        flash(f"Inbjudan är skapad men MEJLET GICK INTE FRAM (kontrollera "
              f"Resend-domänverifieringen). Dela länken själv: {lank}")
    return redirect(url_for("admin.anvandare"))


@bp.route("/anvandare/<int:aid>", methods=("GET", "POST"))
def anvandare_detalj(aid):
    db = get_db()
    a = db.execute("SELECT * FROM anvandare WHERE id=?", (aid,)).fetchone()
    if a is None:
        return render_template("404.html"), 404
    if request.method == "POST":
        atgard = request.form.get("atgard")
        if atgard == "spara":
            roll = request.form.get("roll", a["roll"])
            if roll not in ROLLER:
                roll = a["roll"]
            db.execute(
                "UPDATE anvandare SET roll=?, giltig_till=?, epost=?, "
                "namn=?, foretag=? WHERE id=?",
                (roll, request.form.get("giltig_till") or None,
                 (request.form.get("epost") or a["epost"]).strip().lower(),
                 request.form.get("namn") or None,
                 request.form.get("foretag") or None, aid))
            db.commit()
            flash("Uppgifterna sparade.")
        elif atgard == "radera":
            if aid == g.anvandare["id"]:
                flash("Du kan inte radera ditt eget konto.")
                return redirect(url_for("admin.anvandare_detalj", aid=aid))
            for tabell in ("bevakning", "sparad_signal", "sparat_avtal",
                           "losenordsaterstallning", "utskickslogg"):
                try:
                    db.execute(f"DELETE FROM {tabell} WHERE anvandare_id=?",
                               (aid,))
                except Exception:
                    pass  # tabellnamn kan skilja mellan versioner
            db.execute("DELETE FROM anvandare WHERE id=?", (aid,))
            db.commit()
            flash(f"Kontot {a['epost']} är raderat.")
            return redirect(url_for("admin.anvandare"))
        elif atgard == "aterstall":
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO losenordsaterstallning "
                       "(anvandare_id, token) VALUES (?,?)", (aid, token))
            db.commit()
            lank = (request.url_root.rstrip("/")
                    + url_for("auth.aterstall", token=token))
            from ..digest import _skicka
            gick = _skicka(a["epost"],
                           "Återställ ditt lösenord hos Consens",
                           f"Hej!\n\nVälj nytt lösenord här (gäller i 60 "
                           f"min):\n{lank}\n\nConsens",
                           typ="aterstallning", anvandare_id=aid)
            flash("Återställningslänk skickad." if gick else
                  f"Mejlet gick INTE fram, dela länken själv: {lank}")
        return redirect(url_for("admin.anvandare_detalj", aid=aid))
    bevakningar = db.execute(
        "SELECT * FROM bevakning WHERE anvandare_id=? ORDER BY id DESC",
        (aid,)).fetchall()
    utskick = db.execute(
        "SELECT * FROM utskickslogg WHERE anvandare_id=? OR epost=? "
        "ORDER BY id DESC LIMIT 20", (aid, a["epost"])).fetchall()
    return render_template("admin_anvandare_detalj.html", a=a,
                           roller=ROLLER, bevakningar=bevakningar,
                           utskick=utskick)
