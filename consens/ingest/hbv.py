"""HBV - allmännyttans inköpscentral (Husbyggnadsvaror HBV Förening).

Källan är sidan "Våra ramavtal": en tabell med refnr, ramavtalsnamn (länkat
till avtalskatalogen i TendSign) och "Giltigt t.o.m". Egenheter som
hanteras: asterisk efter datumet betyder förlängningsoption, parentesen
"(tillfälligt förlängningsavtal)" markerar övergångsläge, "Se respektive
avtalsdel" och tomma datum hoppas över, refnr kan saknas eller dubbleras.

Samma optionslogik som Adda/Avropa: ändrat slutdatum uppdaterar avtalet
och rensar en inaktuell utgångssignal."""
import json
import re

from bs4 import BeautifulSoup

from ..db import get_db
from ..matchning import bygg_namnindex, hamta_eller_skapa
from .avropa import _slugga
from .bas import kor_avtalskalla
from .gemensamt import TIMEOUT, http_session

LISTA_URL = "https://hbv.se/vara-ramavtal/"
KOPARE = "HBV"
KOPARE_ORGNR = "702000-9226"
_DATUM = re.compile(r"(\d{4}-\d{2}-\d{2})")


def tolka_lista(html_text: str):
    """Tabellrader -> ramavtal. Bruten ut för testbarhet."""
    soup = BeautifulSoup(html_text, "html.parser")
    for rad in soup.find_all("tr"):
        celler = rad.find_all("td")
        if len(celler) < 3:
            continue
        lank = celler[1].find("a")
        namn = re.sub(r"\s+", " ", celler[1].get_text(" ", strip=True)).strip()
        if not namn:
            continue
        datumtext = celler[2].get_text(" ", strip=True)
        m = _DATUM.search(datumtext)
        if not m:
            continue  # "Se respektive avtalsdel" eller tomt
        refnr = celler[0].get_text(strip=True)
        yield dict(
            refnr=refnr or None,
            namn=namn,
            url=(lank["href"] if lank and lank.get("href") else LISTA_URL),
            slutdatum=m.group(1),
            forlangning="*" in datumtext,
            tillfallig="tillfällig" in datumtext.lower(),
        )


def hamta():
    db = get_db()
    session = http_session()
    resp = session.get(LISTA_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    exakt, normaliserat, per_orgnr = bygg_namnindex(db)

    for rad in tolka_lista(resp.text):
        extern_id = "hbv:" + _slugga(
            f"{rad['refnr'] or ''} {rad['namn']}".strip())
        kand = db.execute("SELECT id, slutdatum FROM avtal WHERE extern_id=?",
                          (extern_id,)).fetchone()
        if kand:
            if kand["slutdatum"] != rad["slutdatum"]:
                db.execute("UPDATE avtal SET slutdatum=?, url=? WHERE id=?",
                           (rad["slutdatum"], rad["url"], kand["id"]))
                db.execute("DELETE FROM signal WHERE extern_id=?",
                           (f"utgang:{extern_id}",))
                db.commit()
            continue

        org, _, _ = hamta_eller_skapa(db, KOPARE, KOPARE_ORGNR,
                                      exakt, normaliserat, per_orgnr)
        if org is None:
            continue
        detaljer = {k: v for k, v in {
            "Avtalsform": "Ramavtal för allmännyttan (HBV)",
            "Referensnummer": rad["refnr"],
            "Förlängningsoption": "Ja" if rad["forlangning"] else None,
            "Läge": ("Tillfälligt förlängningsavtal i väntan på ny "
                     "upphandling") if rad["tillfallig"] else None,
        }.items() if v}
        yield dict(
            organisation_id=org["id"],
            titel=rad["namn"][:500],
            slutdatum=rad["slutdatum"],
            kalla="hbv",
            extern_id=extern_id,
            url=rad["url"],
            detaljer=json.dumps(detaljer, ensure_ascii=False),
        )


def kor(torr=False):
    return kor_avtalskalla("hbv", hamta, torr=torr)
