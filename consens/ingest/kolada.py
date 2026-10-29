"""Kolada (RKA) - öppna nyckeltal för kommuner och regioner.

Berikar organisationssidorna med t.ex. invånarantal. KPI-id:n är
konfigurerade i KPIER; etiketten hämtas alltid från Koladas API så att
ett felkonfigurerat id hoppar i stället för att visa fel siffra.
Matchning: Koladas titlar ("Göteborg", "Södertälje") mot våra
organisationsnamn ("Göteborgs Stad", "Södertälje kommun") via
suffixstädning och s-genitiv."""
from datetime import date

from ..db import get_db
from .gemensamt import TIMEOUT, http_session

BAS = "https://api.kolada.se/v3"
KPIER = ["N01951"]  # Invånare totalt. Fler läggs till efter
# verifiering mot kolada.se - etiketten hämtas alltid från API:t.


def _namnvarianter(namn: str):
    n = namn.lower().strip()
    for suffix in (" kommun", " stad", "s stad", "s kommun"):
        if n.endswith(suffix):
            n = n[: -len(suffix)]
            break
    ut = {n}
    if n.endswith("s"):
        ut.add(n[:-1])
    return ut


def _hamta_sidor(session, url: str):
    """Kolada paginerar med next_page-fält."""
    while url:
        resp = session.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        yield from data.get("values", [])
        url = data.get("next_page")


def kor(torr=False) -> dict:
    db = get_db()
    session = http_session()
    # 1. Koladas kommun/region-register -> våra organisations-id
    kolada_id = {}
    for m in _hamta_sidor(session, f"{BAS}/municipality"):
        if m.get("type") in ("K", "L"):
            kolada_id[m["title"].lower().strip()] = m["id"]
    vara = {}
    for org in db.execute("SELECT id, namn, kontotyp FROM organisation "
                          "WHERE kontotyp IN ('kommun','region')"):
        for variant in _namnvarianter(org["namn"]):
            if variant in kolada_id:
                vara[kolada_id[variant]] = org["id"]
                break
        else:
            # Regionsnamn matchar ofta ordagrant ("Region Skåne")
            if org["namn"].lower().strip() in kolada_id:
                vara[kolada_id[org["namn"].lower().strip()]] = org["id"]
    if not vara:
        return dict(status="ok", matchade=0, varden=0)
    # 2. Per KPI: etikett från API:t, sedan senaste årets värden
    varden = 0
    iar = date.today().year
    for kpi in KPIER:
        resp = session.get(f"{BAS}/kpi/{kpi}", timeout=TIMEOUT)
        if resp.status_code != 200:
            continue
        kpirader = resp.json().get("values", [])
        if not kpirader:
            continue  # okänt KPI-id: hoppa hellre än att gissa etikett
        etikett = kpirader[0].get("title", kpi)
        skrivna = set()
        for ar in range(iar - 1, iar - 4, -1):
            rader = list(_hamta_sidor(
                session, f"{BAS}/data/kpi/{kpi}/year/{ar}"))
            for rad in rader:
                oid = vara.get(rad.get("municipality"))
                if oid is None or oid in skrivna:
                    continue
                varde = next((v.get("value") for v in rad.get("values", [])
                              if v.get("gender") == "T"
                              and v.get("value") is not None), None)
                if varde is None:
                    continue
                if not torr:
                    db.execute(
                        "INSERT INTO nyckeltal (organisation_id, slag, "
                        "etikett, ar, varde) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(organisation_id, slag) DO UPDATE SET "
                        "etikett=excluded.etikett, ar=excluded.ar, "
                        "varde=excluded.varde, hamtad=date('now')",
                        (oid, kpi, etikett, ar, float(varde)))
                skrivna.add(oid)
                varden += 1
            if skrivna:
                db.commit()
            if len(skrivna) >= len(vara):
                break  # alla matchade organisationer har färskt värde
    return dict(status="ok", matchade=len(vara), varden=varden)
