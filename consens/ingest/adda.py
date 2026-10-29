"""Adda Inköpscentral - nationella ramavtal för kommuner och regioner.

Källan är sidan "Ramavtal A-Ö": en enda tabell med samtliga ramavtal och
dynamiska inköpssystem, med namn, länk och "Nuvarande avtalstid" (slutdatum
i ISO-format). En hämtning täcker hela katalogen.

Registeregenhet: Adda förlänger löpande avtalstider genom optioner, så ett
redan känt avtal kan få NYTT slutdatum. Då uppdateras avtalet och en
eventuell utgångssignal med det gamla datumet tas bort - nästa körning av
avtalsutgangar återskapar den med rätt datum."""
import json
import re
import time

from bs4 import BeautifulSoup

from ..db import get_db
from ..matchning import bygg_namnindex, hamta_eller_skapa
from .bas import kor_avtalskalla
from .gemensamt import TIMEOUT, http_session

LISTA_URL = ("https://www.adda.se/upphandling-och-ramavtal/"
             "ramavtal-och-avtalskategorier/ramavtal-a-o/")
KOPARE = "Adda Inköpscentral"
_DATUM = re.compile(r"(\d{4}-\d{2}-\d{2})")


def tolka_lista(html_text: str):
    """Tabellrader -> ramavtal. Bokstavsrubriker (A, B, C...) och rader
    utan slutdatum hoppas över. Bruten ut för testbarhet."""
    soup = BeautifulSoup(html_text, "html.parser")
    for rad in soup.find_all("tr"):
        celler = rad.find_all("td")
        if len(celler) < 2:
            continue
        lank = celler[0].find("a")
        if not lank or not lank.get("href"):
            continue
        # Länktexten är uppdelad i flera element (namn, radbrytningar,
        # chevron-ikon) - mellanslag som separator, annars klistras
        # "Vård och omsorg" ihop till "Vård ochomsorg".
        namn = lank.get_text(" ", strip=True).replace("chevron_right", "")
        namn = re.sub(r"\s+", " ", namn).strip()
        if len(namn) <= 1:
            continue  # bokstavsrubrik
        m = _DATUM.search(celler[1].get_text())
        if not m:
            continue  # "-" = ingen avtalstid ännu (kommande upphandling)
        url = lank["href"]
        if url.startswith("/"):
            url = "https://www.adda.se" + url
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        kategori = (celler[2].get_text(strip=True)
                    if len(celler) > 2 else "")
        yield dict(namn=namn, url=url, slug=slug,
                   slutdatum=m.group(1), kategori=kategori)


_PERIOD = re.compile(r"Avtalsperiod:?\s*(\d{4}-\d{2}-\d{2})\s*[-\u2013]\s*"
                     r"(\d{4}-\d{2}-\d{2})")
# Dragspelsrubriker i Leverantörer-sektionen som INTE är leverantörer.
_EJ_LEVERANTOR = ("Uppföljning", "Är jag berättigad", "Upphandlingsdokument",
                  "Produktkataloger", "Hållbarhet", "Så här gör du",
                  "Avropsberättigade", "Leverantörer")


def tolka_detalj(html_text: str) -> dict:
    """Startdatum och leverantörslista ur en ramavtalssida.
    Leverantörerna ligger som dragspel efter rubriken "Leverantörer";
    knapptexterna slutar med materialikonerna "remove add". Saknade
    fält blir None/tom lista - berikningen får aldrig fälla källan."""
    ut = {"startdatum": None, "slutdatum": None, "leverantorer": []}
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        m = _PERIOD.search(re.sub(r"\s+", " ", soup.get_text(" ")))
        if m:
            ut["startdatum"], ut["slutdatum"] = m.group(1), m.group(2)
        rubrik = soup.find(lambda t: t.name in ("h2", "h3", "h4")
                           and t.get_text(strip=True) == "Leverantörer")
        if not rubrik:
            return ut
        sedda = set()
        for el in rubrik.find_all_next(("button", "h3", "h4", "summary")):
            txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
            if el.name in ("h3", "h4") and any(
                    txt.startswith(x) for x in ("Nyttor", "Frågor och svar",
                                                "Globala mål", "Kontakt")):
                break  # nästa sektion - klart
            namn = re.sub(r"\s*(remove|add|expand_more|expand_less)\s*$",
                          "", txt)
            while namn != txt:
                txt, namn = namn, re.sub(
                    r"\s*(remove|add|expand_more|expand_less)\s*$", "", namn)
            namn = namn.strip()
            if (not namn or len(namn) > 80 or namn in sedda
                    or any(namn.startswith(x) for x in _EJ_LEVERANTOR)):
                continue
            # Företagsheuristik: bolagsform eller minst två versalord.
            if (re.search(r"\b(AB|Aktiebolag|HB|KB|AS|Oy|Ltd|Gruppen)\b",
                          namn)
                    or len(re.findall(r"\b[A-ZÅÄÖ]", namn)) >= 2):
                sedda.add(namn)
                ut["leverantorer"].append(namn)
    except Exception:
        pass
    return ut


def _berika(session, url: str) -> dict:
    try:
        resp = session.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        d = tolka_detalj(resp.text)
        time.sleep(0.35)
        return d
    except Exception:
        return {"startdatum": None, "slutdatum": None, "leverantorer": []}


def hamta():
    db = get_db()
    session = http_session()
    resp = session.get(LISTA_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    exakt, normaliserat, per_orgnr = bygg_namnindex(db)

    for rad in tolka_lista(resp.text):
        extern_id = f"adda:{rad['slug']}"
        kand = db.execute("SELECT id, slutdatum FROM avtal WHERE extern_id=?",
                          (extern_id,)).fetchone()
        if kand:
            if kand["slutdatum"] != rad["slutdatum"]:
                # Option utlöst: nytt slutdatum. Signal med gammalt datum
                # rensas; avtalsutgangar återskapar den korrekt.
                db.execute("UPDATE avtal SET slutdatum=?, url=? WHERE id=?",
                           (rad["slutdatum"], rad["url"], kand["id"]))
                db.execute("DELETE FROM signal WHERE extern_id=?",
                           (f"utgang:{extern_id}",))
                db.commit()
            # Engångsberikning av befintliga avtal utan leverantörslista.
            bef = db.execute("SELECT detaljer, startdatum FROM avtal "
                             "WHERE id=?", (kand["id"],)).fetchone()
            har = json.loads(bef["detaljer"]) if bef["detaljer"] else {}
            if "Leverantörer" not in har:
                d = _berika(session, rad["url"])
                if d["leverantorer"]:
                    har["Leverantörer"] = ", ".join(d["leverantorer"][:40])
                har.setdefault("Avtalsform",
                               "Nationellt ramavtal (Adda Inköpscentral)")
                db.execute(
                    "UPDATE avtal SET detaljer=?, startdatum=? WHERE id=?",
                    (json.dumps(har, ensure_ascii=False),
                     bef["startdatum"] or d["startdatum"], kand["id"]))
                db.commit()
            continue

        org, _, _ = hamta_eller_skapa(db, KOPARE, None,
                                      exakt, normaliserat, per_orgnr)
        if org is None:
            continue
        d = _berika(session, rad["url"])
        detaljer = {k: v for k, v in {
            "Avtalsform": "Nationellt ramavtal (Adda Inköpscentral)",
            "Kategori": rad["kategori"] or None,
            "Leverantörer": (", ".join(d["leverantorer"][:40])
                             if d["leverantorer"] else None),
        }.items() if v}
        yield dict(
            organisation_id=org["id"],
            titel=rad["namn"][:500],
            startdatum=d["startdatum"],
            slutdatum=rad["slutdatum"],
            kalla="adda",
            extern_id=extern_id,
            url=rad["url"],
            detaljer=json.dumps(detaljer, ensure_ascii=False),
        )


def kor(torr=False):
    return kor_avtalskalla("adda", hamta, torr=torr)
