"""TED - upphandlingsannonser (alla typer, hela Sverige). Anonym åtkomst.

Lyft från Nodpunkt med IT-filtret borttaget: Consens tar allt. Okända köpare
skapas automatiskt i organisationsregistret med gissad kontotyp.

Datumgolv alltid satt (rullande 14 dagar): utan golv returnerar TED en
odefinierad delmängd och kan fastna i gamla notiser år efter år.
"""
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from ..db import get_db
from ..matchning import bygg_namnindex, hamta_eller_skapa
from .bas import kor_kalla
from .gemensamt import (PROCEDUR_NAMN, TIMEOUT, flat, http_session, lista,
                        nuts_till_lan, rensa_ted_titel, tolka_datum)

API_URL = "https://api.ted.europa.eu/v3/notices/search"
FIELDS = [
    "publication-number", "notice-title", "buyer-name", "publication-date",
    "notice-type", "classification-cpv", "deadline-receipt-tender-date-lot",
    # BT-15: direktadress till upphandlingsdokumenten (kravspecifikation,
    # utvärderingsmodell, avtalsvillkor). BT-18: var anbudet lämnas.
    "document-url-lot", "submission-url-lot",
    # Köparens kontaktadress för upphandlingen: bättre än en gissad
    # registratorsadress när användaren begär ut handlingar.
    "buyer-touchpoint-email", "organisation-email-buyer",
]
ROLLANDE_DAGAR = 14
MAX_SIDOR = 40
XML_PAUS = 0.5
SIDPAUS = 0.5          # artighet mellan söksidor - 200 sidor utan paus gav 429
MAX_429_FORSOK = 6


def _tala_429(gor_anrop):
    """Kör anropet; vid 429 backas det exponentiellt (eller enligt
    Retry-After) i upp till sex försök innan vi ger upp. TED:s rate limit
    är tillfällig - en död backfill är onödig."""
    import time as _t
    resp = None
    for forsok in range(MAX_429_FORSOK):
        resp = gor_anrop()
        if resp.status_code != 429:
            return resp
        try:
            paus = int(resp.headers.get("Retry-After", "0"))
        except ValueError:
            paus = 0
        _t.sleep(min(paus or 30 * (forsok + 1), 300))
    return resp


def _lokalt(tag):
    return tag.split("}")[-1] if "}" in tag else tag


def _cn_detaljer(session, pubnr: str) -> dict:
    """XML-detaljer för en annons: sök-API:et ger bara sju fält, XML:en har
    hela underlaget. Hämtas EFTER dubblettkontrollen - ett anrop per ny notis.
    Returnerar {beskrivning, forfarande, nuts_lan, uppskattat, detaljer_json}."""
    ut = {"beskrivning": None, "forfarande": None, "nuts_lan": None,
          "uppskattat": None, "referens": None}
    try:
        resp = _tala_429(lambda: session.get(
            f"https://ted.europa.eu/en/notice/{pubnr}/xml", timeout=TIMEOUT))
        if resp.status_code != 200:
            return ut
        rot = ET.fromstring(resp.text)
    except Exception:
        return ut

    beskrivningar, nuts, belopp = [], [], []
    for e in rot.iter():
        namn = _lokalt(e.tag)
        if namn == "ProcurementProject":
            for sub in e:
                subnamn = _lokalt(sub.tag)
                if subnamn == "Description" and sub.text:
                    t = sub.text.strip()
                    if t and t not in beskrivningar:
                        beskrivningar.append(t)
                elif subnamn == "ID" and sub.text:  # BT-22 köparens referens - vinner alltid
                    ut["referens"] = sub.text.strip()
        elif (namn == "ContractFolderID" and e.text
              and not ut["referens"]):
            ut["referens"] = e.text.strip()
        elif namn == "ProcedureCode" and e.text and not ut["forfarande"]:
            kod = e.text.strip()
            ut["forfarande"] = PROCEDUR_NAMN.get(kod, kod)
        elif namn == "CountrySubentityCode" and e.text:
            if e.text.strip() not in nuts:
                nuts.append(e.text.strip())
        elif namn == "EstimatedOverallContractAmount" and e.text:
            if (e.get("currencyID", "SEK") or "SEK").upper() == "SEK":
                try:
                    belopp.append(int(float(e.text.strip())))
                except ValueError:
                    pass

    if beskrivningar:
        ut["beskrivning"] = "\n\n".join(beskrivningar[:3])
    for kod in nuts:
        if (lan := nuts_till_lan(kod)):
            ut["nuts_lan"] = lan
            break
    if belopp:
        ut["uppskattat"] = max(belopp)
    return ut


def _hamta_notiser(session, query, max_sidor):
    """Sidar via iterationNextToken. limit MÅSTE vara sträng - TED:s API."""
    notiser, token, sidor = [], None, 0
    while sidor < max_sidor:
        payload = {"query": query, "fields": FIELDS, "limit": "100",
                   "scope": "ALL", "checkQuerySyntax": False,
                   "paginationMode": "ITERATION"}
        if token:
            payload["iterationNextToken"] = token
        resp = _tala_429(lambda: session.post(API_URL, json=payload,
                                              timeout=TIMEOUT))
        if resp.status_code != 200:
            raise RuntimeError(f"TED API-fel {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        sida = data.get("notices", [])
        notiser.extend(sida)
        sidor += 1
        token = data.get("iterationNextToken")
        if not token or not sida:
            break
        time.sleep(SIDPAUS)
    return notiser


def hamta(sedan: str | None = None, max_sidor: int = MAX_SIDOR,
          med_xml: bool = True):
    db = get_db()
    if not sedan:
        sedan = (datetime.utcnow() - timedelta(days=ROLLANDE_DAGAR)).strftime("%Y%m%d")
    sedan = sedan.replace("-", "")  # TED kräver YYYYMMDD; tillåt 2024-01-01
    query = f"buyer-country = SWE AND publication-date>={sedan}"
    session = http_session()
    notiser = _hamta_notiser(session, query, max_sidor)
    exakt, normaliserat, per_orgnr = bygg_namnindex(db)

    for n in notiser:
        pubnr = flat(n.get("publication-number"))
        if not pubnr:
            continue
        notistyp = flat(n.get("notice-type")).lower()
        if "can" in notistyp:      # tilldelningar hanteras av ted_tilldelningar
            continue
        extern_id = f"ted:{pubnr}"
        if db.execute("SELECT 1 FROM signal WHERE extern_id=?",
                      (extern_id,)).fetchone():
            continue  # XML hämtas aldrig för redan kända

        org = None
        for kandidat in lista(n.get("buyer-name")):
            org, _, _ = hamta_eller_skapa(db, flat(kandidat), None,
                                          exakt, normaliserat, per_orgnr)
            if org is not None:
                break
        if org is None:
            continue

        d = _cn_detaljer(session, pubnr) if med_xml else {
            "beskrivning": None, "forfarande": None, "nuts_lan": None,
            "uppskattat": None}
        if med_xml:
            time.sleep(XML_PAUS)

        titel = rensa_ted_titel(flat(n.get("notice-title"))) or "Upphandlingsnotis"
        forhands = any(t in notistyp for t in ("pin", "prior"))
        underlag = flat(n.get("document-url-lot"))
        anbudsplats = flat(n.get("submission-url-lot"))
        epost = (flat(n.get("buyer-touchpoint-email"))
                 or flat(n.get("organisation-email-buyer")) or "")
        detaljer = {k: v for k, v in {
            "Beskrivning": d["beskrivning"],
            "Förfarande": d["forfarande"],
            "Plats": d["nuts_lan"],
            "Referensnummer": d.get("referens"),
            "Notistyp": notistyp or None,
            "Underlag": underlag if underlag and underlag.startswith("http") else None,
            "Anbud lämnas": anbudsplats if anbudsplats and anbudsplats.startswith("http") else None,
            "Kontakt": epost if "@" in epost else None,
        }.items() if v}
        sammanfattning = ("Förhandsannonsering. " if forhands else "")
        if d["beskrivning"]:
            kort = d["beskrivning"].split("\n")[0]
            sammanfattning += kort[:240] + ("…" if len(kort) > 240 else "")
        else:
            sammanfattning += f"Notistyp: {notistyp or 'okänd'}."
        yield dict(
            typ="upphandling",
            organisation_id=org["id"],
            titel=titel[:500],
            sammanfattning=sammanfattning,
            varde=d["uppskattat"],
            varde_sort="uppskattning" if d["uppskattat"] else None,
            cpv=flat(n.get("classification-cpv")) or None,
            lan=d["nuts_lan"] or org.get("lan"),
            kalla="ted",
            extern_id=extern_id,
            deadline=tolka_datum(flat(n.get("deadline-receipt-tender-date-lot"))),
            url=f"https://ted.europa.eu/sv/notice/-/detail/{pubnr}",
            publicerad=tolka_datum(flat(n.get("publication-date"))),
            detaljer=json.dumps(detaljer, ensure_ascii=False) if detaljer else None,
        )


def kor(torr=False, sedan=None, max_sidor=None):
    return kor_kalla("ted", lambda: hamta(sedan=sedan,
                     max_sidor=max_sidor or MAX_SIDOR), torr=torr)


def berika_underlag(db, max_antal: int = 800) -> dict:
    """Hämta dokumentlänkar (BT-15) för aktuella TED-annonser som saknar
    dem.

    Fälten började hämtas först i efterhand, så äldre poster saknar dem.
    Vi berikar bara annonser vars anbudstid inte gått ut: för passerade
    är underlaget ändå bara åtkomligt via utlämnandebegäran."""
    import json as _json
    from datetime import date as _date

    rader = db.execute(
        "SELECT id, extern_id, detaljer FROM signal "
        "WHERE kalla='ted' AND deadline >= ? "
        "ORDER BY deadline LIMIT ?",
        (_date.today().isoformat(), max_antal * 3)).fetchall()
    behover = []
    for r in rader:
        try:
            det = _json.loads(r["detaljer"] or "{}")
        except Exception:
            det = {}
        # Poster kan sakna underlagslänk, kontaktadress eller båda:
        # fälten lades till vid olika tillfällen.
        if "Underlag" not in det or "Kontakt" not in det:
            behover.append((r["id"], r["extern_id"].split(":")[-1], det))
    behover = behover[:max_antal]
    session = http_session()
    berikade = utan = fel = 0
    lankar = adresser = 0
    for signal_id, pubnr, det in behover:
        try:
            svar = _tala_429(lambda: session.post(
                API_URL,
                json={"query": f'publication-number="{pubnr}"',
                      "fields": ["publication-number", "document-url-lot",
                                 "submission-url-lot",
                                 "buyer-touchpoint-email",
                                 "organisation-email-buyer"],
                      "limit": "1"}, timeout=TIMEOUT))
            svar.raise_for_status()
            notiser = svar.json().get("notices") or []
        except Exception:
            fel += 1
            continue
        if not notiser:
            utan += 1
            continue
        n = notiser[0]
        underlag = flat(n.get("document-url-lot"))
        anbud = flat(n.get("submission-url-lot"))
        epost = (flat(n.get("buyer-touchpoint-email"))
                 or flat(n.get("organisation-email-buyer")) or "")
        andrad = False
        if "@" in epost and "Kontakt" not in det:
            det["Kontakt"] = epost
            adresser += 1
            andrad = True
        if underlag and underlag.startswith("http") and "Underlag" not in det:
            det["Underlag"] = underlag
            lankar += 1
            andrad = True
        if anbud and anbud.startswith("http"):
            det["Anbud lämnas"] = anbud
            andrad = True
        if andrad:
            db.execute("UPDATE signal SET detaljer=? WHERE id=?",
                       (_json.dumps(det, ensure_ascii=False), signal_id))
            berikade += 1
            # Commit direkt: annars hålls skrivlåset i hela körningen och
            # webben kan inte skriva under tiden (inbjudningar, bevakningar).
            db.commit()
        else:
            utan += 1
        time.sleep(XML_PAUS)
    db.commit()
    return dict(prövade=len(behover), berikade=berikade, lankar=lankar,
                adresser=adresser, utan_lank=utan, fel=fel)
