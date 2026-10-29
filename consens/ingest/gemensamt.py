"""Delade hjälpare för källorna. Lyft från Nodpunkts common.py + ted.py."""
from datetime import datetime

import requests

TIMEOUT = 30
USER_AGENT = "Consens/1.0 (bevakning av upphandlingar och avtal; consens.se)"


def http_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def flat(value) -> str:
    """TED-fält kan vara str, dict (per språk) eller list - normalisera till str."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for lang in ("swe", "sv", "eng", "en"):
            if lang in value:
                return flat(value[lang])
        return flat(next(iter(value.values()), ""))
    if isinstance(value, list):
        return flat(value[0]) if value else ""
    return str(value)


def lista(varde) -> list:
    if isinstance(varde, dict):
        ut = []
        for v in varde.values():
            ut.extend(v if isinstance(v, list) else [v])
        return [s for s in ut if s]
    if isinstance(varde, list):
        return [s for s in varde if s]
    return [varde] if varde else []


def tolka_datum(s) -> str | None:
    """eForms-datum har tidszonsuffix: '2030-05-31+02:00' eller '2027-01-12Z'.
    Returnerar ISO-datum (str) - vår lagringsform."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:10]).date().isoformat()
    except (TypeError, ValueError):
        return None


def rensa_ted_titel(titel: str | None) -> str | None:
    """TED bygger titeln som 'Sverige – {CPV-beskrivning} – {upphandlarens rubrik}'.
    Prefixet är boilerplate. Landsvakten gör funktionen idempotent.

    Fälla upptäckt i drift: CPV-beskrivningar kan själva innehålla ' – ' efter
    avstavning ('Bygg- – och anläggningsarbeten'), och en naiv delning klipper
    då mitt i ordet ('och anläggningsarbeten – Projektledare...'). Delar som
    slutar med bindestreck fogas därför ihop med nästa, och en gemen-vakt
    släpper ytterligare ledande delar om resultatet ändå börjar med liten
    bokstav."""
    if not titel or not titel.startswith("Sverige – "):
        return titel
    delar = []
    for d in titel.split(" – "):
        if delar and delar[-1].endswith("-"):
            delar[-1] = delar[-1] + " " + d
        else:
            delar.append(d)
    if len(delar) >= 3:
        kandidat = " – ".join(delar[2:]).strip()
    elif len(delar) == 2:
        kandidat = delar[1].strip()
    else:
        return titel
    while kandidat and kandidat[0].islower() and " – " in kandidat:
        kandidat = kandidat.split(" – ", 1)[1].strip()
    return kandidat or titel


NUTS_LAN = {
    "SE110": "Stockholm", "SE121": "Uppsala", "SE122": "Södermanland",
    "SE123": "Östergötland", "SE124": "Örebro", "SE125": "Västmanland",
    "SE211": "Jönköping", "SE212": "Kronoberg", "SE213": "Kalmar",
    "SE214": "Gotland", "SE221": "Blekinge", "SE224": "Skåne",
    "SE231": "Halland", "SE232": "Västra Götaland", "SE311": "Värmland",
    "SE312": "Dalarna", "SE313": "Gävleborg", "SE321": "Västernorrland",
    "SE322": "Jämtland", "SE331": "Västerbotten", "SE332": "Norrbotten",
}

PROCEDUR_NAMN = {
    "open": "Öppet förfarande", "restricted": "Selektivt förfarande",
    "neg-w-call": "Förhandlat med föregående annonsering",
    "neg-wo-call": "Förhandlat utan föregående annonsering",
    "comp-dial": "Konkurrenspräglad dialog",
    "innovation": "Innovationspartnerskap",
    "oth-single": "Annat enstegsförfarande",
    "oth-mult": "Annat flerstegsförfarande",
}


def nuts_till_lan(kod: str | None) -> str | None:
    if not kod:
        return None
    return NUTS_LAN.get(kod.strip().upper()[:5])
