"""TED-tilldelningar (notice-type=can-standard) -> avtal.

eForms-parsern med Nodpunkts betalda lärdomar:
  - AwardDate BARA från SettledContract (TenderResult-varianten är en
    platshållare - mätningen visade 2000-01-01).
  - Värderangordning: faktiskt anbud (bara när alla anger samma belopp) ->
    ramavtalstak -> uppskattning. Sorten lagras, blandas aldrig.
  - Leverantörskedjan LotTender -> TenderingParty -> Tenderer -> Organization
    följs via schemeName-id:n. Platt läsning ger domstolar som leverantör
    (Förvaltningsrätten i Malmö låg mitt i listan på notis 449310-2026).
  - Flerlotsnotiser: tidigaste datum vinner - första dörren som öppnas.
  - XML hämtas EFTER dubblettkontrollen: daglig körning kostar ett anrop
    per genuint ny notis. eForms-golv 2024-04-01.
"""
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from ..db import get_db
from ..matchning import bygg_namnindex, hamta_eller_skapa
from .bas import kor_avtalskalla
from .gemensamt import (PROCEDUR_NAMN, TIMEOUT, flat, http_session, lista,
                        nuts_till_lan, rensa_ted_titel, tolka_datum)
from .ted import _hamta_notiser, _tala_429, API_URL  # noqa: F401

FIELDS = [
    "publication-number", "notice-title", "buyer-name", "publication-date",
    "classification-cpv", "organisation-name-tenderer",
]
EFORMS_GOLV = "20240401"
ROLLANDE_DAGAR = 14
MAX_SIDOR = 300
XML_PAUS = 0.5


def _lokalt_namn(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _eforms_leverantorer(alla) -> list:
    tender_till_part, part_till_org, org_namn = {}, {}, {}
    for e in alla:
        namn = _lokalt_namn(e.tag)
        if namn == "LotTender":
            tid = pid = None
            for sub in e.iter():
                if _lokalt_namn(sub.tag) != "ID" or not sub.text:
                    continue
                if sub.get("schemeName") == "tender":
                    tid = sub.text.strip()
                elif sub.get("schemeName") == "tendering-party":
                    pid = sub.text.strip()
            if tid and pid:
                tender_till_part[tid] = pid
        elif namn == "TenderingParty":
            pid, orgar = None, []
            for sub in e.iter():
                if _lokalt_namn(sub.tag) != "ID" or not sub.text:
                    continue
                if sub.get("schemeName") == "tendering-party":
                    pid = sub.text.strip()
                elif sub.get("schemeName") == "organization":
                    orgar.append(sub.text.strip())
            if pid and orgar:
                part_till_org[pid] = orgar
        elif namn == "Organization":
            oid = nmn = None
            for sub in e.iter():
                sn = _lokalt_namn(sub.tag)
                if sn == "ID" and sub.text and sub.get("schemeName") == "organization":
                    oid = sub.text.strip()
                elif sn == "Name" and sub.text and nmn is None:
                    nmn = sub.text.strip()
            if oid:
                org_namn[oid] = nmn
    ut = []
    for pid in tender_till_part.values():
        for oid in part_till_org.get(pid, []):
            n = org_namn.get(oid)
            if n and n not in ut:
                ut.append(n)
    return ut


def _sek(par) -> int | None:
    varde, valuta = par
    if valuta and valuta.upper() != "SEK":
        return None
    try:
        return int(float(varde))
    except (TypeError, ValueError):
        return None


def _parsa_eforms(rot) -> dict | None:
    alla = list(rot.iter())
    startdatum, slutdatum, awarddatum = [], [], []
    faktiskt, ramavtal, uppskattat = [], [], []
    beskrivningar, nuts, anbud = [], [], []
    forfarande = None

    for e in alla:
        namn = _lokalt_namn(e.tag)
        if namn == "ProcurementProject":
            for sub in e:
                if _lokalt_namn(sub.tag) == "Description" and sub.text:
                    t = sub.text.strip()
                    if t and t not in beskrivningar:
                        beskrivningar.append(t)
        elif namn == "ProcedureCode" and e.text and forfarande is None:
            forfarande = PROCEDUR_NAMN.get(e.text.strip(), e.text.strip())
        elif namn == "CountrySubentityCode" and e.text:
            if e.text.strip() not in nuts:
                nuts.append(e.text.strip())
        elif namn == "SubmissionStatistics":
            kod = tal = None
            for sub in e.iter():
                sn = _lokalt_namn(sub.tag)
                if sn == "StatisticsCode" and sub.text:
                    kod = sub.text.strip()
                elif sn == "StatisticsNumeric" and sub.text:
                    tal = sub.text.strip()
            if kod == "tenders" and tal:
                try:
                    anbud.append(int(float(tal)))
                except ValueError:
                    pass
        if namn == "PlannedPeriod":
            for sub in e:
                sn = _lokalt_namn(sub.tag)
                if sn == "StartDate" and sub.text:
                    startdatum.append(sub.text.strip())
                elif sn == "EndDate" and sub.text:
                    slutdatum.append(sub.text.strip())
        elif namn == "SettledContract":
            for sub in e:
                if _lokalt_namn(sub.tag) == "AwardDate" and sub.text:
                    awarddatum.append(sub.text.strip())
        elif namn == "LegalMonetaryTotal":
            for sub in e:
                if _lokalt_namn(sub.tag) == "PayableAmount" and sub.text:
                    faktiskt.append((sub.text.strip(), sub.get("currencyID", "")))
        elif namn in ("OverallApproximateFrameworkContractsAmount",
                      "ReestimatedValueAmount"):
            if e.text:
                ramavtal.append((e.text.strip(), e.get("currencyID", "")))
        elif namn == "EstimatedOverallContractAmount":
            if e.text:
                uppskattat.append((e.text.strip(), e.get("currencyID", "")))

    if not (startdatum or slutdatum or awarddatum or faktiskt or ramavtal
            or uppskattat):
        return None

    varde = varde_sort = None
    unika_faktiska = {v for v, _ in faktiskt}
    if len(unika_faktiska) == 1 and (b := _sek(faktiskt[0])) is not None:
        varde, varde_sort = b, "anbud"
    elif ramavtal:
        kandidater = [b for b in map(_sek, ramavtal) if b is not None]
        if kandidater:
            varde, varde_sort = max(kandidater), "ramavtalstak"
    if varde is None and uppskattat:
        kandidater = [b for b in map(_sek, uppskattat) if b is not None]
        if kandidater:
            varde, varde_sort = max(kandidater), "uppskattning"

    start = min((d for d in map(tolka_datum, startdatum) if d), default=None)
    slut = min((d for d in map(tolka_datum, slutdatum) if d), default=None)

    plats = next((lan for k in nuts if (lan := nuts_till_lan(k))), None)
    return {"leverantor": ", ".join(_eforms_leverantorer(alla)[:5]) or None,
            "varde": varde, "varde_sort": varde_sort,
            "startdatum": start, "slutdatum": slut, "schema": "eforms",
            "beskrivning": "\n\n".join(beskrivningar[:3]) or None,
            "forfarande": forfarande, "plats": plats,
            "antal_anbud": max(anbud) if anbud else None}


def _parsa_gammalt(rot) -> dict | None:
    """TED:s äldre schema (före ca april 2024). Saknar avtalsperiod."""
    leverantorer, varden = [], []
    for elem in rot.iter():
        if _lokalt_namn(elem.tag) != "AWARDED_CONTRACT":
            continue
        for sub in elem.iter():
            lokal = _lokalt_namn(sub.tag)
            if lokal == "OFFICIALNAME" and sub.text and sub.text.strip() not in leverantorer:
                leverantorer.append(sub.text.strip())
            elif lokal == "VAL_TOTAL" and sub.text:
                b = _sek((sub.text.strip(), sub.get("CURRENCY", "")))
                if b is not None:
                    varden.append(b)
    if not leverantorer and not varden:
        return None
    return {"leverantor": ", ".join(leverantorer[:5]) or None,
            "varde": varden[0] if len(varden) == 1 else None,
            "varde_sort": "anbud" if len(varden) == 1 else None,
            "startdatum": None, "slutdatum": None, "schema": "gammalt"}


def _hamta_xml_detaljer(session, pubnr: str) -> dict | None:
    """Känner av schemat och väljer parser. Ska aldrig krascha körningen."""
    try:
        resp = _tala_429(lambda: session.get(
            f"https://ted.europa.eu/en/notice/{pubnr}/xml", timeout=TIMEOUT))
        if resp.status_code != 200:
            return None
        rot = ET.fromstring(resp.text)
    except Exception:
        return None
    if any(_lokalt_namn(e.tag) == "EformsExtension" for e in rot.iter()):
        return _parsa_eforms(rot)
    return _parsa_gammalt(rot)


def hamta(sedan: str | None = None, max_sidor: int = MAX_SIDOR, med_xml=True):
    db = get_db()
    if sedan:
        effektivt = sedan.replace("-", "")  # TED kräver YYYYMMDD
    else:
        rullande = (datetime.now() - timedelta(days=ROLLANDE_DAGAR)).strftime("%Y%m%d")
        effektivt = max(rullande, EFORMS_GOLV)
    query = (f"buyer-country = SWE AND notice-type = can-standard "
             f"AND publication-date>={effektivt}")
    session = http_session()
    notiser = _hamta_notiser(session, query, max_sidor)
    exakt, normaliserat, per_orgnr = bygg_namnindex(db)

    for n in notiser:
        pubnr = flat(n.get("publication-number"))
        if not pubnr:
            continue
        extern_id = f"ted-tilldelning:{pubnr}"
        if db.execute("SELECT 1 FROM avtal WHERE extern_id=?",
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

        leverantorer = [flat(x) for x in lista(n.get("organisation-name-tenderer"))]
        detaljer = _hamta_xml_detaljer(session, pubnr) if med_xml else None
        if med_xml:
            time.sleep(XML_PAUS)
        if detaljer and not leverantorer and detaljer.get("leverantor"):
            leverantorer = [detaljer["leverantor"]]
        detaljer = detaljer or {}

        rajson = {k: v for k, v in {
            "Beskrivning": detaljer.get("beskrivning"),
            "Förfarande": detaljer.get("forfarande"),
            "Plats": detaljer.get("plats"),
            "Tilldelningsdatum": None,
        }.items() if v}
        yield dict(
            organisation_id=org["id"],
            titel=(rensa_ted_titel(flat(n.get("notice-title")))
                   or "namnlöst avtal")[:500],
            leverantor=", ".join(leverantorer[:5])[:500] or None,
            varde=detaljer.get("varde"),
            varde_sort=detaljer.get("varde_sort"),
            antal_anbud=detaljer.get("antal_anbud"),
            startdatum=detaljer.get("startdatum"),
            slutdatum=detaljer.get("slutdatum"),
            cpv=(lista(n.get("classification-cpv")) or [None])[0],
            kalla="ted-tilldelning",
            extern_id=extern_id,
            url=f"https://ted.europa.eu/sv/notice/-/detail/{pubnr}",
            detaljer=json.dumps(rajson, ensure_ascii=False) if rajson else None,
        )


def kor(torr=False, sedan=None, med_xml=True, max_sidor=None):
    return kor_avtalskalla(
        "ted_tilldelningar",
        lambda: hamta(sedan=sedan, med_xml=med_xml,
                      max_sidor=max_sidor or MAX_SIDOR),
        torr=torr)
