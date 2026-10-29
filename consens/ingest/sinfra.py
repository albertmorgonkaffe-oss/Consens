"""Sinfra - Svensk Inköpscentral för Infrastruktur (energi, VA, avfall).

Källan är den publika listan över ramavtalsområden plus varje områdes
detaljsida, som öppet visar avtalsperiod ("från och med X till och med Y
med möjlighet till ytterligare förlängning som längst till Z"), lagrum
(LUF/LOU) och hela leverantörslistan. Kategorin (Värme & Kyla, El &
Fiber, Vatten & Avlopp, Avfall & Återvinning, Tjänster & Konsulter,
Hamn) läses ur URL-strukturen.

Samma optionslogik som Adda/Avropa/HBV: ändrat slutdatum uppdaterar
avtalet och rensar en inaktuell utgångssignal. CPV gissas ur titeln via
kategorier.gissa_cpv med kategorin som reserv, så att centralavtalen
fångas av branschfiltret."""
import json
import re
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..db import get_db
from ..kategorier import gissa_cpv
from ..matchning import bygg_namnindex, hamta_eller_skapa
from .avropa import _slugga
from .bas import kor_avtalskalla
from .gemensamt import TIMEOUT, http_session

LISTA_URL = "https://sinfra.se/ramavtal/ramavtalsomraden/"
KOPARE = "Sinfra (Svensk Inköpscentral för Infrastruktur)"
KOPARE_ORGNR = "716419-3323"

KATEGORIER = {
    "varme-kyla": "Värme & Kyla",
    "el-fiber": "El & Fiber",
    "vatten-avlopp": "Vatten & Avlopp",
    "avfall-atervinning": "Avfall & Återvinning",
    "tjanster-konsulter": "Tjänster & Konsulter",
    "hamn": "Hamn",
}
# Reserv-CPV per kategori när titeln inte ger någon träff.
KATEGORI_CPV = {
    "varme-kyla": "09300000",
    "el-fiber": "31600000",
    "vatten-avlopp": "65110000",
    "avfall-atervinning": "90500000",
    "tjanster-konsulter": "71300000",
    "hamn": None,  # ingen bra branschmappning ännu
}

_PERIOD = re.compile(
    r"från och med\s+(\d{4}-\d{2}-\d{2})\s+till och med\s+"
    r"(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_FORLANGNING = re.compile(r"som längst till\s+(\d{4}-\d{2}-\d{2})",
                          re.IGNORECASE)
_LAGRUM = re.compile(r"Upphandlat enligt\s*[:\s]\s*(LUF|LOU)",
                     re.IGNORECASE)


def tolka_lista(html_text: str):
    """(kategori_slug, titel, url) för varje ramavtalsområde i listan."""
    soup = BeautifulSoup(html_text, "html.parser")
    sedda = set()
    for a in soup.select('a[href*="/ramavtal/ramavtalsomraden/"]'):
        href = urljoin(LISTA_URL, a.get("href", ""))
        delar = [d for d in urlparse(href).path.split("/") if d]
        # /ramavtal/ramavtalsomraden/<kategori>/<slug>/
        if len(delar) != 4 or delar[2] not in KATEGORIER:
            continue
        if href in sedda:
            continue
        sedda.add(href)
        titel = a.get_text(" ", strip=True)
        # Anchortexten inleds ofta med kategorietiketten - skala av den.
        kat_namn = KATEGORIER[delar[2]]
        if titel.startswith(kat_namn):
            titel = titel[len(kat_namn):].strip()
        if not titel:
            titel = delar[3].replace("-", " ").capitalize()
        yield delar[2], titel, href


def tolka_detalj(html_text: str) -> dict:
    """Avtalsperiod, max förlängning, lagrum och leverantörer ur en
    detaljsida. Saknade fält blir None/tom lista."""
    soup = BeautifulSoup(html_text, "html.parser")
    text = soup.get_text(" ", strip=True)
    ut = {"startdatum": None, "slutdatum": None, "max_forlangning": None,
          "lagrum": None, "leverantorer": []}
    m = _PERIOD.search(text)
    if m:
        ut["startdatum"], ut["slutdatum"] = m.group(1), m.group(2)
    m = _FORLANGNING.search(text)
    if m:
        ut["max_forlangning"] = m.group(1)
    m = _LAGRUM.search(text)
    if m:
        ut["lagrum"] = m.group(1).upper()
    # Leverantörer: länkarna efter rubriken "Leverantörer".
    rubrik = soup.find(lambda t: t.name in ("h2", "h3", "h4", "h5")
                       and t.get_text(strip=True) == "Leverantörer")
    if rubrik:
        for el in rubrik.find_all_next():
            if el.name in ("h2", "h3", "h4", "h5", "footer"):
                break
            if el.name == "a":
                namn = el.get_text(" ", strip=True)
                if namn and "sinfra.se" not in (el.get("href") or ""):
                    ut["leverantorer"].append(namn)
    return ut


def hamta(paus: float = 0.4):
    db = get_db()
    session = http_session()
    resp = session.get(LISTA_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    exakt, normaliserat, per_orgnr = bygg_namnindex(db)

    for kategori, titel, url in tolka_lista(resp.text):
        extern_id = "sinfra:" + _slugga(f"{kategori} {titel}")
        kand = db.execute(
            "SELECT id, slutdatum FROM avtal WHERE extern_id=?",
            (extern_id,)).fetchone()

        # Detaljsidan behövs både för nya avtal och för optionskollen.
        d = None
        try:
            dresp = session.get(url, timeout=TIMEOUT)
            dresp.raise_for_status()
            d = tolka_detalj(dresp.text)
            time.sleep(paus)
        except Exception:
            if kand:
                continue  # känt avtal, misslyckad detalj - rör inget
            d = {"startdatum": None, "slutdatum": None,
                 "max_forlangning": None, "lagrum": None,
                 "leverantorer": []}

        if kand:
            if d["slutdatum"] and kand["slutdatum"] != d["slutdatum"]:
                db.execute("UPDATE avtal SET slutdatum=?, url=? WHERE id=?",
                           (d["slutdatum"], url, kand["id"]))
                db.execute("DELETE FROM signal WHERE extern_id=?",
                           (f"utgang:{extern_id}",))
                db.commit()
            continue

        org, _, _ = hamta_eller_skapa(db, KOPARE, KOPARE_ORGNR,
                                      exakt, normaliserat, per_orgnr)
        if org is None:
            continue
        detaljer = {k: v for k, v in {
            "Avtalsform": "Ramavtal för försörjningssektorn (Sinfra)",
            "Kategori": KATEGORIER[kategori],
            "Upphandlat enligt": d["lagrum"],
            "Max förlängning till": d["max_forlangning"],
            "Leverantörer": (", ".join(d["leverantorer"][:40])
                             if d["leverantorer"] else None),
        }.items() if v}
        yield dict(
            organisation_id=org["id"],
            titel=titel[:500],
            startdatum=d["startdatum"],
            slutdatum=d["slutdatum"],
            cpv=gissa_cpv(titel) or KATEGORI_CPV.get(kategori),
            kalla="sinfra",
            extern_id=extern_id,
            url=url,
            detaljer=json.dumps(detaljer, ensure_ascii=False),
        )


def kor(torr=False):
    return kor_avtalskalla("sinfra", hamta, torr=torr)
