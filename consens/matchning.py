"""Köparmatchning - Nodpunkts fullständiga logik, porterad till SQLite.

Dokumentets "svåraste lösta problem". Regler i ordning:
  1. Orgnr-index: entydigt när det finns.
  2. Exakt namn (inkl. alias, '|'-separerade).
  3. Normaliserat namn (bolagsform bortskalad iterativt).
  4. Landstingsalias ('Landstinget i X' -> 'X', ' län' bortskalat).
  5. Asymmetrisk substräng med ordgränser, MIN_SUBSTRANG-golv:
     A) registernamn inuti köparnamn - längsta träff vinner
     B) köparnamn inuti längre registernamn - kortaste träff vinner

ORGNR-VETOT (Gavlefastigheter-fallet): substrängsträff där kandidaten bär ett
ANNAT orgnr än annonsens avvisas - bevisligen fel juridisk person. Nio av tolv
substrängsträffar i mätningen var just moderkommun i stället för bolag.
"""
import json
import re

MIN_SUBSTRANG = 5  # verifierat värde från Nodpunkt - ändra inte utan mätning

FORETAGSFORMER = re.compile(
    r"\s+(aktiebolag|ab|ekonomisk\s+förening|ek\.?\s*för\.?|kommunalförbund"
    r"|holding\s*ab|koncern)\s*$"
)


def normalisera_orgnr(orgnr: str | None) -> str | None:
    if not orgnr:
        return None
    siffror = "".join(c for c in str(orgnr) if c.isdigit())
    if len(siffror) == 12 and siffror.startswith("16"):
        siffror = siffror[2:]
    if len(siffror) != 10:
        return None
    return f"{siffror[:6]}-{siffror[6:]}"


def normalisera_foretagsnamn(namn: str) -> str:
    """'Ljusdal Energi Aktiebolag' och 'Ljusdal Energi AB' ska matcha."""
    n = (namn or "").lower().strip()
    prev = None
    while prev != n:
        prev = n
        n = FORETAGSFORMER.sub("", n).strip()
    return n


def _varianter(org: dict):
    """Namn plus alias. Alias är data, inte kod."""
    ut = [org["namn"]]
    if org.get("alias"):
        ut += [a for a in str(org["alias"]).split("|") if a.strip()]
    return ut


def bygg_namnindex(db):
    """(exakt, normaliserat, per_orgnr) - byggs en gång per körning."""
    exakt, normaliserat, per_orgnr = {}, {}, {}
    for rad in db.execute("SELECT * FROM organisation").fetchall():
        o = dict(rad)
        for variant in _varianter(o):
            nyckel = (variant or "").lower().strip()
            if not nyckel:
                continue
            exakt.setdefault(nyckel, o)
            n = normalisera_foretagsnamn(nyckel)
            if n:
                normaliserat.setdefault(n, o)
        onr = normalisera_orgnr(o.get("orgnr"))
        if onr:
            per_orgnr.setdefault(onr, o)
    return exakt, normaliserat, per_orgnr


def _ordgrans(nal: str, halmstack: str) -> bool:
    """Utan ordgräns matchar 'arboga kommun' inuti 'arboga kommunalteknik'."""
    return re.search(rf"(?<!\w){re.escape(nal)}(?!\w)", halmstack) is not None


def _substrangstraff(behallare: dict, text_: str):
    a = [(len(n), o) for n, o in behallare.items()
         if len(n) >= MIN_SUBSTRANG and _ordgrans(n, text_)]
    if a:
        return max(a, key=lambda x: x[0])[1], "substrang"
    if len(text_) >= MIN_SUBSTRANG:
        b = [(len(n), o) for n, o in behallare.items() if _ordgrans(text_, n)]
        if b:
            return min(b, key=lambda x: x[0])[1], "substrang_omvand"
    return None, None


def matcha_namn(namn: str, exakt: dict, normaliserat: dict):
    bl = (namn or "").lower().strip()
    if not bl:
        return None, None
    if bl in exakt:
        return exakt[bl], "exakt"
    bn = normalisera_foretagsnamn(bl)
    if bn and bn in normaliserat:
        return normaliserat[bn], "normaliserad"
    alias = re.sub(r"^landstinget( i)?\s+", "", bl).removesuffix(" län").strip()
    if alias and alias != bl and alias in exakt:
        return exakt[alias], "landstingsalias"
    org, metod = _substrangstraff(exakt, bl)
    if org is not None:
        return org, metod
    if bn:
        org, metod = _substrangstraff(normaliserat, bn)
        if org is not None:
            return org, "normaliserad_" + metod
    return None, None


def matcha_kopare(namn, orgnr, exakt, normaliserat, per_orgnr):
    """Orgnr först, sedan namn - med orgnr-vetot på substrängsträffar."""
    onr = normalisera_orgnr(orgnr)
    if onr and onr in per_orgnr:
        return per_orgnr[onr], "orgnr"

    org, metod = matcha_namn(namn, exakt, normaliserat)
    if onr and org is not None and metod and "substrang" in metod:
        kandidatens = normalisera_orgnr(org.get("orgnr"))
        if kandidatens and kandidatens != onr:
            return None, None  # bevisligen annan juridisk person
        return org, metod + "_osaker"
    return org, metod


def gissa_kontotyp(namn: str, orgnr: str | None = None) -> str:
    """Heuristik för auto-skapade organisationer. Orgnr-prefixen är entydiga:
    212000=kommun, 232100=region, 202100=statlig myndighet."""
    o = (normalisera_orgnr(orgnr) or "").replace("-", "")
    if o.startswith("212000"):
        return "kommun"
    if o.startswith("232100"):
        return "region"
    if o.startswith("202100"):
        return "myndighet"
    n = (namn or "").lower()
    if re.search(r"\b(ab|aktiebolag|hb|kb)\b", n) or "ekonomisk förening" in n:
        if "kommun" in n or " stad" in n:
            return "kommunalt_bolag"
        if "region" in n or "landsting" in n:
            return "regionalt_bolag"
        return "ovrig"
    if n.endswith(" kommun") or n.endswith(" stad") or n.startswith("kommunalförbund"):
        return "kommun"
    if n.startswith("region ") or "landsting" in n:
        return "region"
    if any(k in n for k in ("myndighet", "verket", "styrelsen", "nämnden",
                            "domstol", "universitet", "högskola",
                            "länsstyrelse", "rätt")):
        return "myndighet"
    return "ovrig"


def hamta_eller_skapa(db, namn, orgnr, exakt, normaliserat, per_orgnr):
    """Matcha - och skapa organisationen om den saknas (Consens tar allt).
    Returnerar (org, metod, skapad). Uppdaterar indexen så att nästa annons
    i samma körning träffar den nya organisationen."""
    org, metod = matcha_kopare(namn, orgnr, exakt, normaliserat, per_orgnr)
    if org is not None:
        # Bonus från Kommers-lärdomen: fyll registrets orgnr vid SÄKER träff.
        onr = normalisera_orgnr(orgnr)
        if onr and not org.get("orgnr") and "osaker" not in (metod or ""):
            db.execute("UPDATE organisation SET orgnr=? WHERE id=? "
                       "AND orgnr IS NULL", (onr, org["id"]))
            org["orgnr"] = onr
            per_orgnr.setdefault(onr, org)
        return org, metod, False

    namn = (namn or "").strip()
    if not namn:
        return None, None, False
    onr = normalisera_orgnr(orgnr)
    oid = db.execute(
        "INSERT INTO organisation (namn, orgnr, kontotyp) VALUES (?,?,?)",
        (namn[:200], onr, gissa_kontotyp(namn, onr))).lastrowid
    org = dict(db.execute("SELECT * FROM organisation WHERE id=?",
                          (oid,)).fetchone())
    exakt[namn.lower()] = org
    nn = normalisera_foretagsnamn(namn)
    if nn:
        normaliserat.setdefault(nn, org)
    if onr:
        per_orgnr[onr] = org
    return org, "skapad", True


# ---------------------------------------------------------------------------
# Dubblettfacit: nationellt upphandlings-id / köparens referens.
# Samma upphandling annonseras ofta både i TED och en nationell databas.
# Regeln är medvetet konservativ: koppling kräver SAMMA organisation och
# SAMMA normaliserade referens - ingen titelgissning, låg felrisk.

_REF_NYCKLAR = ("Referensnummer", "Diarienummer", "Ärendenummer",
                "Ärende", "Upphandlings-ID", "Upphandlingsnummer")


def extrahera_referens(detaljer_json) -> str | None:
    """Normaliserad referens ur en signals detalj-JSON, eller None.
    Kräver minst 4 tecken och minst en siffra för att undvika brus."""
    if not detaljer_json:
        return None
    try:
        d = json.loads(detaljer_json) if isinstance(detaljer_json, str) \
            else detaljer_json
    except Exception:
        return None
    for nyckel in _REF_NYCKLAR:
        v = d.get(nyckel)
        if not v:
            continue
        norm = re.sub(r"\s+", " ", str(v)).strip().upper()
        norm = norm.strip(".,;:")
        if len(norm) >= 4 and any(c.isdigit() for c in norm):
            return norm[:120]
    return None


def koppla_signal_dubblett(db, sid, organisation_id, upphandlings_id) -> bool:
    """Koppla EN nyinlagd upphandlingssignal mot en befintlig med samma
    org + referens. Äldsta signalen förblir huvudpost."""
    if not upphandlings_id or not organisation_id:
        return False
    huvud = db.execute(
        "SELECT id FROM signal WHERE organisation_id=? AND upphandlings_id=? "
        "AND typ='upphandling' AND dublett_av IS NULL AND id != ? "
        "ORDER BY id LIMIT 1", (organisation_id, upphandlings_id, sid)
    ).fetchone()
    if huvud:
        db.execute("UPDATE signal SET dublett_av=? WHERE id=?",
                   (huvud["id"], sid))
        return True
    return False


def koppla_dubbletter(db) -> dict:
    """Backfill: extrahera referenser för alla upphandlingssignaler som
    saknar dem, koppla sedan dubbletter per (organisation, referens).
    Idempotent."""
    taggade = 0
    for r in db.execute(
        "SELECT id, detaljer FROM signal WHERE typ='upphandling' "
        "AND upphandlings_id IS NULL AND detaljer IS NOT NULL").fetchall():
        ref = extrahera_referens(r["detaljer"])
        if ref:
            db.execute("UPDATE signal SET upphandlings_id=? WHERE id=?",
                       (ref, r["id"]))
            taggade += 1
    kopplade = 0
    grupper = db.execute(
        "SELECT organisation_id, upphandlings_id FROM signal "
        "WHERE typ='upphandling' AND upphandlings_id IS NOT NULL "
        "AND organisation_id IS NOT NULL "
        "GROUP BY organisation_id, upphandlings_id "
        "HAVING COUNT(*) > 1").fetchall()
    for g in grupper:
        rader = db.execute(
            "SELECT id, dublett_av FROM signal WHERE organisation_id=? "
            "AND upphandlings_id=? AND typ='upphandling' ORDER BY id",
            (g["organisation_id"], g["upphandlings_id"])).fetchall()
        huvud = rader[0]["id"]
        for r in rader[1:]:
            if r["dublett_av"] is None:
                db.execute("UPDATE signal SET dublett_av=? WHERE id=?",
                           (huvud, r["id"]))
                kopplade += 1
    db.commit()
    return {"taggade": taggade, "kopplade": kopplade}


# ---------------------------------------------------------------------------
# Enkel hastighetsbegränsning, delad över processer via databasen.

def for_manga_forsok(db, typ: str, nyckel: str, max_antal: int,
                     minuter: int) -> bool:
    """True om nyckeln (t.ex. IP eller IP+epost) har gjort fler än
    max_antal försök av typen de senaste `minuter` minuterna.
    Registrerar samtidigt det aktuella försöket."""
    db.execute("CREATE TABLE IF NOT EXISTS forsok ("
               "id INTEGER PRIMARY KEY AUTOINCREMENT, typ TEXT NOT NULL, "
               "nyckel TEXT NOT NULL, tidpunkt TEXT NOT NULL "
               "DEFAULT (datetime('now')))")
    db.execute("DELETE FROM forsok WHERE tidpunkt < datetime('now', ?)",
               (f"-{max(minuter, 60)} minutes",))
    antal = db.execute(
        "SELECT COUNT(*) FROM forsok WHERE typ=? AND nyckel=? "
        "AND tidpunkt >= datetime('now', ?)",
        (typ, nyckel, f"-{minuter} minutes")).fetchone()[0]
    db.execute("INSERT INTO forsok (typ, nyckel) VALUES (?, ?)",
               (typ, nyckel))
    db.commit()
    return antal >= max_antal


def sanera(text, max_langd: int = 200) -> str:
    """Trimma, kapa och rensa kontrolltecken ur ett formulärfält."""
    if not text:
        return ""
    ut = "".join(c for c in str(text) if c >= " " or c in "\n\t")
    return ut.strip()[:max_langd]
