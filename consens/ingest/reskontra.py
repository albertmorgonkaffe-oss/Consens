"""Leverantörsreskontra - kommunala inköpsdata som öppna data.

Importerar filer som följer den nationella specifikationen
(lankadedata.se/spec/leverantorsreskontra/1.0): CSV eller JSON med en
rad per faktura. Vi lagrar aldrig fakturaraderna - de aggregeras per
(organisation, leverantör, år) till tabellen inkopsmonster, som driver
sektionen "Inköpsmönster" på organisationssidorna.

Egenheter specen kräver tolerans för: både komma- och semikolon-
separerad CSV, BOM, decimalkomma trots att punkt är påbjudet, samt
kopare-orgnr som identifierar köparen per rad (en fil kan alltså
innehålla flera organisationer). Okända köpar-orgnr räknas och hoppas.
Omimport av samma fil är säker: berörda (organisation, år) rensas och
skrivs om."""
import csv
import io
import json

from ..db import get_db
from ..matchning import (bygg_namnindex, normalisera_foretagsnamn,
                         normalisera_orgnr)
from .gemensamt import TIMEOUT, http_session

# Kolumnnamn som avviker från specen hos enskilda utgivare (t.ex. Umeå).
_ALIAS = {
    "leverantör": "leverantor",
    "organisationsnummer": "leverantor_id",
    "summa exkl moms": "belopp",
    "summa exkl. moms": "belopp",
    "belopp exkl moms": "belopp",
    "belopp exkl. moms": "belopp",
    "konteringsmånad": "datum",
}


def _avkoda(ra: bytes) -> str:
    """Kommunfiler är UTF-8 enligt specen men i praktiken ofta
    Latin-1/Windows-1252 (Härryda m.fl.)."""
    try:
        return ra.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ra.decode("cp1252", errors="replace")


def tolka_rader(text: str):
    """Fakturarader ur CSV (komma/semikolon) eller JSON enligt specen.
    Ger dicts med kopare, leverantor, leverantor_id, belopp (float),
    ar (int) och grund - trasiga rader hoppas tyst."""
    text = text.lstrip("\ufeff").strip()
    if text.startswith("[") or text.startswith("{"):
        try:
            rader = json.loads(text)
        except Exception:
            return
        if isinstance(rader, dict):
            rader = rader.get("data") or rader.get("rows") or []
    else:
        forsta = text.split("\n", 1)[0]
        avskiljare = ";" if forsta.count(";") > forsta.count(",") else ","
        rader = csv.DictReader(io.StringIO(text), delimiter=avskiljare)
    for rad in rader:
        try:
            r = {}
            for k, v in rad.items():
                nyckel = (k or "").strip().lower()
                r[_ALIAS.get(nyckel, nyckel)] = (v or "").strip()
            belopp_text = (r.get("belopp", "")
                           .replace(" ", "").replace("\xa0", ""))
            if "," in belopp_text and "." not in belopp_text:
                belopp_text = belopp_text.replace(",", ".")
            belopp = float(belopp_text)
            datum = r.get("datum", "")
            ar = (int(datum[:4]) if datum[:4].isdigit()
                  and int(datum[:4]) >= 2000 else None)
            leverantor = r.get("leverantor", "")[:200]
            if not leverantor:
                continue
            # Vissa kommuner (t.ex. Västerås) lägger köparens NAMN i
            # kopare och orgnumret i en egen kolumn kopare_id.
            yield dict(
                kopare=(normalisera_orgnr(r.get("kopare"))
                        or normalisera_orgnr(r.get("kopare_id"))),
                kopare_namn=(r.get("kopare") or "").strip(),
                leverantor=leverantor,
                leverantor_id=normalisera_orgnr(r.get("leverantor_id")),
                belopp=belopp,
                ar=ar,
                grund=(r.get("grund") or "").upper()[:1],
            )
        except Exception:
            continue


def importera(text: str, kalla_url: str = "",
              kopare_standard: str | None = None,
              ar_standard: int | None = None) -> dict:
    """Aggregera och skriv in ett filinnehåll. Posterna märks med filens
    URL: omimport av SAMMA fil rensar och skriver om sina egna poster
    (idempotent), medan andra filer - t.ex. övriga månader från samma
    kommun - lämnas orörda och adderas vid visning."""
    db = get_db()
    exakt, normaliserat, per_orgnr = bygg_namnindex(db)
    standard = per_orgnr.get(normalisera_orgnr(kopare_standard))
    grupper: dict = {}
    okanda: set = set()
    rader_in = 0
    for rad in tolka_rader(text):
        rader_in += 1
        namn = rad["kopare_namn"].lower()
        org = (per_orgnr.get(rad["kopare"])
               or exakt.get(namn)
               or normaliserat.get(normalisera_foretagsnamn(namn) or "")
               or standard)
        if org is None:
            okanda.add(rad["kopare"] or rad["kopare_namn"] or "(tom)")
            continue
        ar = rad["ar"] or ar_standard
        if ar is None:
            continue  # varken datumkolumn eller --ar angivet
        nyckel = (org["id"], rad["leverantor"], ar)
        g = grupper.setdefault(
            nyckel, {"belopp": 0.0, "antal": 0, "antal_avtal": 0,
                     "antal_grund": 0,
                     "leverantor_orgnr": rad["leverantor_id"]})
        del ar  # aggregatet nedan använder nyckelns år
        g["belopp"] += rad["belopp"]
        g["antal"] += 1
        if rad["grund"]:
            g["antal_grund"] += 1
            if rad["grund"] in ("R", "U"):
                g["antal_avtal"] += 1
    beror = {(oid, ar) for (oid, _, ar) in grupper}
    for oid, ar in beror:
        db.execute("DELETE FROM inkopsmonster WHERE organisation_id=? "
                   "AND ar=? AND kalla_url=?", (oid, ar, kalla_url))
    for (oid, lev, ar), g in grupper.items():
        db.execute(
            "INSERT INTO inkopsmonster (organisation_id, leverantor, "
            "leverantor_orgnr, ar, belopp, antal, antal_avtal, "
            "antal_grund, kalla_url) VALUES (?,?,?,?,?,?,?,?,?)",
            (oid, lev, g["leverantor_orgnr"], ar,
             round(g["belopp"], 2), g["antal"], g["antal_avtal"],
             g["antal_grund"], kalla_url))
    db.commit()
    return dict(rader=rader_in, leverantorer=len(grupper),
                organisationer=len({o for (o, _, _) in grupper}),
                okanda_kopare=sorted(okanda))


# Kända reskontrakällor: (url, kopar-orgnr eller None, år eller None).
# Kommunerna publicerar årsvis, så halvårskörning räcker gott.
KALLOR_INKOP = (
    ("vasteras", "https://opendata.vasteras.se/store/1/resource/131", None, None),
    ("lund", "https://katalog.lund.se/store/1/resource/65", None, None),
    ("harryda", "https://catalog.harryda.se/store/2/resource/142", None, None),
    ("harryda", "https://catalog.harryda.se/store/2/resource/95", None, None),
    ("ale", "https://catalog.ale.se/store/1/resource/100", None, None),
    ("sodertalje", "https://catalog.sodertalje.se/store/12/resource/145", None, None),
    ("molndal", "https://catalog.molndal.se/store/1/resource/54", None, None),
    ("molndal", "https://catalog.molndal.se/store/1/resource/102", None, None),
    ("molndal", "https://catalog.molndal.se/store/1/resource/108", None, None),
    ("molndal", "https://catalog.molndal.se/store/1/resource/109", None, None),
    ("molndal", "https://catalog.molndal.se/store/1/resource/110", None, None),
    ("molndal", "https://catalog.molndal.se/store/1/resource/113", None, None),
    ("molndal", "https://catalog.molndal.se/store/1/resource/129", None, None),
    ("molndal", "https://catalog.molndal.se/store/1/resource/133", None, None),
    ("trollhattan", "https://catalog.trollhattan.se/store/1/resource/155", None, None),
    ("digg", "https://catalog.digg.se/store/1/resource/137", None, None),
    ("digg", "https://catalog.digg.se/store/1/resource/122", None, None),
    ("goteborg", "https://catalog.goteborg.se/store/6/resource/84384", "212000-1355", 2025),
    ("goteborg", "https://catalog.goteborg.se/store/6/resource/88867", "212000-1355", 2025),
    ("goteborg", "https://catalog.goteborg.se/store/6/resource/103806", "212000-1355", 2025),
    ("goteborg", "https://catalog.goteborg.se/store/6/resource/106084", "212000-1355", 2025),
    ("umea", "https://opendata.umea.se/api/explore/v2.1/catalog/datasets/"
     "leverantorsfakturor-2025/exports/csv", "212000-2627", None),
    ("umea", "https://opendata.umea.se/api/explore/v2.1/catalog/datasets/"
     "leverantorsfakturor-2024/exports/csv", "212000-2627", None),
)


def kor_alla():
    """Kör alla kända reskontrakällor. Loggar per källa i
    korningshistorik så adminöversikten ser status och ålder."""
    from datetime import datetime as _dt
    from ..db import get_db
    db = get_db()
    resultat = []
    for kort, url, kopare, ar in KALLOR_INKOP:
        namn = f"inkop-{kort}"
        start = _dt.now().isoformat(timespec="seconds")
        try:
            r = importera_url(url, kopare_standard=kopare, ar_standard=ar)
            poster = r.get("leverantorer", 0)
            db.execute(
                "INSERT INTO korningshistorik (kalla, startad, avslutad, "
                "status, antal_nya) VALUES (?,?,?,'ok',?)",
                (namn, start, _dt.now().isoformat(timespec="seconds"),
                 poster))
            resultat.append((namn, "ok", poster))
        except Exception as e:
            db.execute(
                "INSERT INTO korningshistorik (kalla, startad, avslutad, "
                "status, fel) VALUES (?,?,?,'fel',?)",
                (namn, start, _dt.now().isoformat(timespec="seconds"),
                 f"{type(e).__name__}: {e}"[:500]))
            resultat.append((namn, "fel", f"{type(e).__name__}: {e}"))
        db.commit()
    return resultat


def importera_url(url: str, kopare_standard: str | None = None,
                  ar_standard: int | None = None) -> dict:
    resp = http_session().get(url, timeout=max(TIMEOUT, 60))
    resp.raise_for_status()
    return importera(_avkoda(resp.content), kalla_url=url,
                     kopare_standard=kopare_standard,
                     ar_standard=ar_standard)
