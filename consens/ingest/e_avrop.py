"""e-Avrop - öppet läsbar annonslista, klassisk ASP.NET utan JS-rendering.

Listsidan ger titel/köpare/datum/CPV; detaljsidan hämtas per NY annons
(efter dubblettkontroll, 0,5 s paus) och ger beskrivning, alla etiketterade
fält och - guld för matchningen - köparens organisationsnummer.

Extraktionen är medvetet tolerant: tabellrader och dl-listor blir
etikett/värde-par, längsta textblocket blir beskrivningen. ASP.NET-sidor
varierar; hellre några skräpfält i rådatat än en parser som knäcks av
en malländring."""
import json
import re
import time

from bs4 import BeautifulSoup

from ..db import get_db
from ..matchning import bygg_namnindex, hamta_eller_skapa
from .bas import kor_kalla
from .gemensamt import TIMEOUT, http_session

BAS = "https://www.e-avrop.com"
LISTA_URL = f"{BAS}/e-Upphandling/Default.aspx"
PAUS = 0.5
_CPV = re.compile(r"\b(\d{8})\b")
_DATUM = re.compile(r"(\d{4}-\d{2}-\d{2})")
_ORGNR = re.compile(r"(\d{6}-?\d{4})")


def _datum(text_):
    m = _DATUM.search(text_ or "")
    return m.group(1) if m else None


def tolka_lista(html_text: str):
    """Rader ur listsidan. Bruten ut för testbarhet."""
    soup = BeautifulSoup(html_text, "html.parser")
    for rad in soup.select("tr.rowline"):
        celler = rad.find_all("td")
        if len(celler) < 5:
            continue
        lank = celler[0].find("a")
        if not lank:
            continue
        id_m = re.search(r"id=(\d+)", lank.get("href", ""))
        if not id_m:
            continue
        cpv_koder = _CPV.findall(celler[3].get_text(separator=" "))
        yield dict(
            annons_id=id_m.group(1),
            lank=lank.get("href", ""),
            titel=lank.get_text(strip=True),
            kopare=celler[2].get_text(strip=True),
            publicerad=_datum(celler[1].get_text()),
            deadline=_datum(celler[4].get_text()),
            cpv=cpv_koder[0] if cpv_koder else None,
        )


def tolka_detalj(html_text: str) -> dict:
    """Detaljsidan -> {falt, beskrivning, orgnr}. Bruten ut för testbarhet."""
    soup = BeautifulSoup(html_text, "html.parser")
    for skrap in soup(["script", "style", "select", "nav", "header", "footer"]):
        skrap.decompose()

    falt: dict = {}

    def _ta(etikett, varde):
        etikett = (etikett or "").strip(" :\u00a0")
        varde = (varde or "").strip()
        if (0 < len(etikett) <= 60 and varde and varde != etikett
                and len(varde) <= 2000 and "\n" not in etikett
                and len(falt) < 30):
            falt.setdefault(etikett, varde)

    for rad in soup.find_all("tr"):
        celler = rad.find_all(["th", "td"])
        if len(celler) == 2:
            _ta(celler[0].get_text(" ", strip=True),
                celler[1].get_text(" ", strip=True))
    for dl in soup.find_all("dl"):
        for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
            _ta(dt.get_text(" ", strip=True), dd.get_text(" ", strip=True))

    kandidater = []
    for el in soup.find_all(["p", "div", "td"]):
        if el.find(["p", "div", "table", "tr"]):
            continue  # bara lövnoder - undviker att hela sidan blir "texten"
        t = el.get_text(" ", strip=True)
        if len(t) >= 120:
            kandidater.append(t)
    beskrivning = max(kandidater, key=len)[:4000] if kandidater else None

    orgnr = None
    for etikett, varde in falt.items():
        if "rganisationsnummer" in etikett:
            if (m := _ORGNR.search(varde)):
                orgnr = m.group(1)
                break
    if not orgnr:
        m = re.search(r"[Oo]rganisationsnummer\D{0,30}(\d{6}-?\d{4})",
                      soup.get_text(" ", strip=True))
        orgnr = m.group(1) if m else None

    return {"falt": falt, "beskrivning": beskrivning, "orgnr": orgnr}


def hamta():
    db = get_db()
    session = http_session()
    resp = session.get(LISTA_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    exakt, normaliserat, per_orgnr = bygg_namnindex(db)

    for rad in tolka_lista(resp.text):
        extern_id = f"e-avrop:{rad['annons_id']}"
        if db.execute("SELECT 1 FROM signal WHERE extern_id=?",
                      (extern_id,)).fetchone():
            continue  # detaljsidan hämtas aldrig för redan kända

        url = BAS + rad["lank"] if rad["lank"] else None
        detalj = {"falt": {}, "beskrivning": None, "orgnr": None}
        if url:
            try:
                dresp = session.get(url, timeout=TIMEOUT)
                if dresp.status_code == 200:
                    detalj = tolka_detalj(dresp.text)
            except Exception:
                pass
            time.sleep(PAUS)

        org, _, _ = hamta_eller_skapa(db, rad["kopare"], detalj["orgnr"],
                                      exakt, normaliserat, per_orgnr)
        if org is None:
            continue

        sammanfattning = None
        if detalj["beskrivning"]:
            kort = detalj["beskrivning"]
            sammanfattning = kort[:240] + ("…" if len(kort) > 240 else "")
        detaljer = dict(detalj["falt"])
        if detalj["beskrivning"]:
            detaljer["Beskrivning"] = detalj["beskrivning"]

        yield dict(
            typ="upphandling",
            organisation_id=org["id"],
            titel=rad["titel"][:500],
            sammanfattning=sammanfattning,
            cpv=rad["cpv"],
            lan=org.get("lan"),
            kalla="e-avrop",
            extern_id=extern_id,
            deadline=rad["deadline"],
            publicerad=rad["publicerad"],
            url=url,
            detaljer=json.dumps(detaljer, ensure_ascii=False)
                     if detaljer else None,
        )


def kor(torr=False):
    return kor_kalla("e_avrop", hamta, torr=torr)
