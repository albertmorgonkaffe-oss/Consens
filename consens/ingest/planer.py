"""Kommunala upphandlingsplaner - signaltypen 'planerad'.

Det tidigaste larmet i affärscykeln: kommunen har beslutat upphandla
men inget är annonserat än. Poster upsertas per plan: nya blir signaler
via kor_kalla (dedup, yrkestaggning, historik), och befintliga vars
period flyttats uppdateras i ett eget svep. Planer anger sällan exakta
datum - deadline sätts mitt i angiven period (för sortering) och
visningen avrundar alltid till kvartal."""
import json
import re
from datetime import date, timedelta

from ..db import get_db
from ..matchning import bygg_namnindex, hamta_eller_skapa
from .bas import kor_kalla
from .gemensamt import TIMEOUT, http_session
from bs4 import BeautifulSoup

TROLLHATTAN_URL = ("https://www.trollhattan.se/startsida/jobbochforetagande/"
                   "upphandling/planerade-upphandlingar/")
SKOVDE_URL = ("https://www.skovde.se/naringsliv-foretag-forening/upphandling/"
              "kommunens-upphandlingar/upphandlingsplan/")

# En post i Skövdes plan: "Titel, orgdel. Annonsering: 2025-03-01,
# preliminär avtalsstart: 2025-06-01." (även "Annonseras:"; avtalsstart
# kan saknas eller stå som egen mening)
_SKOVDE_POST = re.compile(
    r"^(?P<titel>.+?),\s*(?P<orgdel>[^.]*?)\.?\s*"
    r"Annonser(?:as|ing):\s*(?P<annons>\d{4}-\d{2}-\d{2})"
    r"(?:[.,]\s*[Pp]reliminär avtalsstart:\s*"
    r"(?P<start>\d{4}-\d{2}-\d{2}))?", re.M)
_KOMMUN_I_TEXT = re.compile(r"([A-ZÅÄÖ][a-zåäöé-]+s?)\s+kommun")
STHLM_URL = "https://upphandling.stockholm/planerade-upphandlingar/"
# MeetingPlus (Formpipe) driver mötesportaler hos flera kommuner med
# identisk struktur: /overview listar möten, mötessidan har ärendena som
# numrerade rubriker i HTML. En parser per plattform - inte per kommun.
# Instanser funna genom sondering av kommunernas subdomäner
# (meetingsplus./meetingspublic./moten. + /overview). Nya hittas med
# samma metod - lägg bara till en rad här.
MEETINGPLUS = (
    ("Stockholms stad", "https://meetingspublic.stockholm.se"),
    ("Region Västmanland", "https://meetingsplus.regionvastmanland.se"),
    ("Lunds kommun", "https://moten.lund.se"),
    ("Danderyds kommun", "https://meetingsplus.danderyd.se"),
    ("Gävle kommun", "https://meetingsplus.gavle.se"),
    ("Ånge kommun", "https://moten.ange.se"),
)
# Portalerna listar hundratals möten, men bara HÅLLNA möten har
# publicerad dagordning - kommande möten är oftast tomma sidor. Vi
# prioriterar därför nyligen hållna, plus några få närmast kommande
# (dagordningen publiceras ca en vecka före).
MAX_HALLNA = 30
MAX_KOMMANDE = 8
# Rubriker som betyder att något ska byggas, köpas eller upphandlas.
_BESLUTSORD = (
    "upphandling", "upphandla", "genomförandebeslut", "inriktningsbeslut",
    "investering", "entreprenad", "ramavtal", "anskaffning", "nybyggnad",
    "om- och tillbyggnad", "ombyggnad", "tillbyggnad", "renovering",
    "markanvisning", "exploateringsavtal", "avtal om",
)
# Rubriker som ser intressanta ut men är politiskt prat, inte inköp.
_BRUSORD = (
    "interpellation", "motion", "fråga till", "enkel fråga", "svar på fråga",
    "fyllnadsval", "val av", "anmälan av", "redovisning av",
    "medborgarförslag", "valärende", "entledigande", "kungörelse",
    "protokolljustering", "pm:", "skrivelse", "rapport", "yttrande",
    "remiss", "serveringstillstånd", "inkomna", "delårs", "årsredovisning",
    "revisions", "verksamhetsberättelse", "sammanträdestider",
    "delegationsordning", "attestant", "förordnande", "nämndinitiativ",
)
# Byggnader och anläggningar: kommunen skriver ofta bara VAD det är
# ("Nytt tillagningskök för äldre"), inte att det ska upphandlas.
# Objektord + byggverb/ny-prefix = beslutsärende med affär i sig.
_OBJEKTORD = (
    "förskola", "skola", "gymnasium", "idrottshall", "sporthall", "simhall",
    "badhus", "äldreboende", "omsorgsboende", "säbo", "gruppbostad",
    "servicebostad", "bibliotek", "tillagningskök", "mottagningskök",
    "fritidsgård", "idrottsplats", "konstgräs", "lekplats", "park",
    "gata", "väg", "cykelbana", "gc-väg", "torg", "kaj", "brygga",
    "belysning", "vattenverk", "reningsverk", "avloppsledning",
    "vattenledning", "bostäder", "trygghetsboende", "hall", "arena",
    "återvinningscentral", "brandstation", "kontorslokaler", "lokaler",
)
_BYGGORD = (
    "uppförande", "anläggande", "byggnation", "upprustning", "utbyggnad",
    "utveckling av", "iordningställande", "modernisering", "utbyte",
    "förnyelse", "sanering", "detaljplan för", "projektering",
)
_NY_PREFIX = re.compile(r"^(ny|nytt|nya|om|till|ut)\b", re.I)
UPPSALA_URL = ("https://www.uppsala.se/foretag-och-naringsliv/"
               "leverantor-och-utforare/upphandling2/upphandlingsplan/")
MRP_TROLLHATTAN_URL = ("https://www.trollhattan.se/startsida/"
                       "kommun-och-politik/varderingar-vision-och-mal/"
                       "mal--och-resursplan-mrp/")
ADDA_PLAN_URL = ("https://www.adda.se/upphandling-och-ramavtal/"
                 "planerade-och-pagaende-upphandlingar/upphandlingsplan2026/")
_MANADER = {m: i + 1 for i, m in enumerate(
    ("januari", "februari", "mars", "april", "maj", "juni", "juli",
     "augusti", "september", "oktober", "november", "december"))}
RS_URL = ("https://www.regionstockholm.se/om-region-stockholm/"
          "inkop-och-upphandling/aktuellt-inom-upphandling-och-avtal/"
          "pa-gang-inom-upphandling/")

_KVARTALSDAG = {1: "02-15", 2: "05-15", 3: "08-15", 4: "11-15"}


def tolka_period(text: str):
    """'2026, kvartal 2' / 'Kvartal 1 2027' / '2026' -> (iso-datum, klartext).
    Obestämda ('Annonseras senare', 'Ej klart') -> (None, klartext)."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return None, ""
    ar = re.search(r"\b(20\d{2})\b", text)
    kvartal = re.search(r"kvartal\s*(\d)|q\s*(\d)", text, re.I)
    if ar and kvartal:
        q = int(kvartal.group(1) or kvartal.group(2))
        if 1 <= q <= 4:
            return f"{ar.group(1)}-{_KVARTALSDAG[q]}", text
    if ar:
        return f"{ar.group(1)}-07-01", text
    return None, text


def tolka_trollhattan(html_text: str):
    """Rader ur planens HTML-tabell: (titel, periodtext)."""
    soup = BeautifulSoup(html_text, "html.parser")
    ut = []
    for tabell in soup.find_all("table"):
        for tr in tabell.find_all("tr"):
            celler = [c.get_text(" ", strip=True)
                      for c in tr.find_all(["td", "th"])]
            if len(celler) < 2:
                continue
            titel, period = celler[0].strip(), celler[1].strip()
            if (not titel or titel.lower().startswith("upphandling")
                    or len(titel) > 300):
                continue  # rubrikrad eller skräp
            ut.append((titel, period))
    return ut


def _slug(titel: str) -> str:
    s = re.sub(r"[^a-z0-9åäö]+", "-", titel.lower()).strip("-")
    return s[:120]


def tolka_skovde(html_text: str):
    """Poster ur Skövdes löptextplan: (titel, orgnamn, annonsdatum,
    avtalsstart). Orgdelen pekar ut vilken Skaraborgskommun posten
    gäller; saknas kommunnamn är det Skövdes egen."""
    soup = BeautifulSoup(html_text, "html.parser")
    text = soup.get_text("\n")
    ut = []
    for m in _SKOVDE_POST.finditer(text):
        titel = re.sub(r"\s+", " ", m.group("titel")).strip()
        if not titel or len(titel) > 300:
            continue
        orgdel = m.group("orgdel") or ""
        km = _KOMMUN_I_TEXT.findall(orgdel)
        if km:
            namn = km[-1]
            org_namn = (namn if namn.endswith("s") else namn) + " kommun"
        else:
            org_namn = "Skövde kommun"
        ut.append((titel, org_namn, m.group("annons"), m.group("start")))
    return ut


_WEBBLASAR_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "*/*;q=0.8"),
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.7",
}


def _svar(url: str, timeout):
    """Vissa kommunsajter (t.ex. Skövdes) svarar 404 till okända
    user-agents - prova då en gång som vanlig webbläsare."""
    session = http_session()
    resp = session.get(url, timeout=timeout)
    if resp.status_code in (403, 404, 406):
        resp = session.get(url, timeout=timeout, headers=_WEBBLASAR_UA)
    resp.raise_for_status()
    return resp


def _hamta(url: str) -> str:
    return _svar(url, TIMEOUT).text


def _hamta_bytes(url: str) -> bytes:
    return _svar(url, max(TIMEOUT, 60)).content


def _hitta_lank(html_text: str, bas_url: str, *nyckelord,
                andelse: str | None = None) -> str | None:
    """Första <a> vars href/text innehåller alla nyckelord (och ev.
    slutar på en filändelse). Absolut URL."""
    from urllib.parse import urljoin
    soup = BeautifulSoup(html_text, "html.parser")
    for a in soup.find_all("a", href=True):
        haystack = (a["href"] + " " + a.get_text(" ", strip=True)).lower()
        if all(n in haystack for n in nyckelord):
            if andelse and not a["href"].lower().split("?")[0].endswith(
                    andelse):
                continue
            return urljoin(bas_url, a["href"])
    return None


def _las_xlsx(innehall: bytes) -> dict[str, list[list]]:
    """Alla blad (försättsblad är vanliga - rubrikerna kan bo var som
    helst i arbetsboken)."""
    import io

    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(innehall), read_only=True,
                       data_only=True)
    blad = {ws.title: [list(rad) for rad in ws.iter_rows(values_only=True)]
            for ws in wb.worksheets}
    wb.close()
    return blad


def _kolumnkarta(rader: list[list], spec: dict) -> tuple[dict, int]:
    """Hitta rubrikraden (bland de första 30) och mappa spec-fält ->
    kolumnindex. Fälten prövas i spec-ordning och en kolumn kan bara
    vinnas en gång - lägg specifika fält (avtalsstart) före generella
    (start) så delsträngar inte kapar fel kolumn."""
    for i, rad in enumerate(rader[:30]):
        celler = [str(c or "").strip().lower() for c in rad]
        karta, tagna = {}, set()
        for falt, kandidater in spec.items():
            for j, cell in enumerate(celler):
                if j not in tagna and any(k in cell for k in kandidater):
                    karta[falt] = j
                    tagna.add(j)
                    break
        if len(karta) >= min(3, len(spec)):
            return karta, i
    return {}, -1


def _hitta_blad(blad: dict, spec: dict):
    """Första bladet där kolumnkartan slår in."""
    for namn, rader in blad.items():
        karta, rubrikrad = _kolumnkarta(rader, spec)
        if "namn" in karta and "start" in karta:
            return rader, karta, rubrikrad
    return None, {}, -1


def _till_datum(varde):
    """Excelcell (datetime/date/text) -> iso-datum eller None."""
    if varde is None:
        return None
    if hasattr(varde, "isoformat"):
        return varde.isoformat()[:10]
    text = str(varde).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    return tolka_period(text)[0]


def _poster_trollhattan(db, index):
    org, _, _ = hamta_eller_skapa(db, "Trollhättans Stad", None, *index)
    if org is None:
        return []
    poster = []
    sedda: dict[str, int] = {}
    for titel, periodtext in tolka_trollhattan(_hamta(TROLLHATTAN_URL)):
        deadline, klartext = tolka_period(periodtext)
        slug = _slug(titel)
        sedda[slug] = sedda.get(slug, 0) + 1
        if sedda[slug] > 1:
            slug = f"{slug}-{sedda[slug]}"
        poster.append(dict(
            typ="planerad", organisation_id=org["id"], titel=titel[:500],
            sammanfattning=("Enligt kommunens upphandlingsplan planeras "
                            f"annonsering: {klartext}." if klartext else
                            "Upptagen i kommunens upphandlingsplan."),
            kalla="plan-trollhattan",
            extern_id=f"plan:trollhattan:{slug}",
            deadline=deadline, url=TROLLHATTAN_URL,
            detaljer=json.dumps(
                {"Planerad annonsering": klartext or "Ej angiven",
                 "Källa": "Trollhättans Stads upphandlingsplan"},
                ensure_ascii=False)))
    return poster


def _poster_skovde(db, index):
    poster = []
    sedda: dict[str, int] = {}
    for titel, org_namn, annons, start in tolka_skovde(_hamta(SKOVDE_URL)):
        org, *_ = hamta_eller_skapa(db, org_namn, None, *index)
        if org is None:
            continue
        slug = _slug(f"{org_namn}-{titel}")
        sedda[slug] = sedda.get(slug, 0) + 1
        if sedda[slug] > 1:
            slug = f"{slug}-{sedda[slug]}"
        det = {"Planerad annonsering": annons,
               "Källa": "Skövde kommuns upphandlingsplan "
                        "(Skaraborgssamverkan)"}
        if start:
            det["Preliminär avtalsstart"] = start
        poster.append(dict(
            typ="planerad", organisation_id=org["id"], titel=titel[:500],
            sammanfattning=("Enligt upphandlingsplanen planeras "
                            f"annonsering {annons}"
                            + (f" med preliminär avtalsstart {start}"
                               if start else "") + "."),
            kalla="plan-skovde",
            extern_id=f"plan:skovde:{slug}",
            deadline=annons, url=SKOVDE_URL,
            detaljer=json.dumps(det, ensure_ascii=False)))
    return poster


def _poster_region_sthlm(db, index):
    """Region Stockholms upphandlingsplan: xlsx nås i två steg
    (plansida -> dokumentsida -> fil). Kolumnerna mappas tolerant;
    datumet är när upphandlingsARBETET startar, inte annonsdatumet -
    vilket sammanfattningen är ärlig med."""
    steg1 = _hamta(RS_URL)
    doksida_url = _hitta_lank(steg1, RS_URL, "upphandlingsplan")
    if not doksida_url:
        raise ValueError("hittade ingen länk till upphandlingsplanen")
    fil_url = _hitta_lank(_hamta(doksida_url), doksida_url, "",
                          andelse=".xlsx")
    if not fil_url:
        raise ValueError("hittade ingen xlsx på dokumentsidan")
    blad = _las_xlsx(_hamta_bytes(fil_url))
    rader, karta, rubrikrad = _hitta_blad(blad, dict(
        avtalsstart=("avtalsstart",),
        namn=("upphandlingens namn", "namn"),
        kategori=("kategorinivå 1",),
        myndighet=("upphandlande myndighet", "myndighet"),
        start=("startdatum", "upphandlingens start")))
    if rader is None:
        raise ValueError(f"okända kolumner i {fil_url} "
                         f"(blad: {sorted(blad)})")
    fallback, *_ = hamta_eller_skapa(db, "Region Stockholm", None, *index)
    poster = []
    sedda: dict[str, int] = {}
    for rad in rader[rubrikrad + 1:]:
        def cell(falt):
            j = karta.get(falt)
            return (str(rad[j]).strip()
                    if j is not None and j < len(rad) and rad[j] is not None
                    else "")
        titel = cell("namn")
        if not titel or len(titel) > 300:
            continue
        deadline = _till_datum(rad[karta["start"]]
                               if karta["start"] < len(rad) else None)
        org = fallback
        if cell("myndighet"):
            org, *_ = hamta_eller_skapa(db, cell("myndighet"), None, *index)
            org = org or fallback
        if org is None:
            continue
        slug = _slug(f"{cell('myndighet') or 'rs'}-{titel}")
        sedda[slug] = sedda.get(slug, 0) + 1
        if sedda[slug] > 1:
            slug = f"{slug}-{sedda[slug]}"
        det = {"Upphandlingsarbetet startar": deadline or "Ej angivet",
               "Källa": "Region Stockholms upphandlingsplan 2026-2029"}
        if cell("kategori"):
            det["Kategori"] = cell("kategori")
        if cell("avtalsstart"):
            det["Planerad avtalsstart"] = (
                _till_datum(rad[karta["avtalsstart"]]) or
                cell("avtalsstart"))
        poster.append(dict(
            typ="planerad", organisation_id=org["id"], titel=titel[:500],
            sammanfattning=("Region Stockholm planerar att påbörja "
                            "upphandlingsarbetet"
                            + (f" {deadline[:7]}" if deadline else "")
                            + ". Annonsering sker senare i processen."),
            kalla="plan-region-stockholm",
            extern_id=f"plan:rs:{slug}",
            deadline=deadline, url=RS_URL,
            detaljer=json.dumps(det, ensure_ascii=False)))
    return poster


def _poster_stockholm(db, index):
    """Stockholms stad: en xlsx per kategori (17 st) direktlänkade på
    plansidan. Kolumnnamnen varierar - tolerant karta per fil, filer
    som inte går att tolka hoppas (och räknas) i stället för att fälla
    källan."""
    from urllib.parse import unquote, urljoin
    soup = BeautifulSoup(_hamta(STHLM_URL), "html.parser")
    fil_urlar = []
    for a in soup.find_all("a", href=True):
        if a["href"].lower().split("?")[0].endswith(".xlsx"):
            fil_urlar.append(urljoin(STHLM_URL, a["href"]))
    if not fil_urlar:
        raise ValueError("hittade inga xlsx-länkar på plansidan")
    org, *_ = hamta_eller_skapa(db, "Stockholms stad", None, *index)
    if org is None:
        return []
    # Stockholms rubriker: Inköpskategori | Beskrivning | Planerad start
    # av upphandling | Avtalsstart | Uppskattat värde kr | Ansvarig ...
    # Posterna saknar namnkolumn - de bor i Beskrivning. Ordningen är
    # viktig: start måste vinna "Planerad start av upphandling" innan
    # namn-kandidaten "upphandling" hinner kapa den.
    spec = dict(
        avtalsstart=("avtalsstart", "avtal börjar", "börjar gälla"),
        start=("planerad start", "start av upphandling", "annonser",
               "påbörja", "start"),
        namn=("beskrivning", "upphandling", "avtalsnamn", "namn",
              "benämning", "objekt"),
        varde=("uppskattat värde", "värde kr"),
        kategori=("inköpskategori", "kategori", "område"))
    poster = []
    sedda: dict[str, int] = {}
    fel_skal = []
    for fil_url in fil_urlar:
        filnamn_kort = fil_url.split("/")[-1].split("?")[0][:60]
        try:
            blad = _las_xlsx(_hamta_bytes(fil_url))
        except Exception as e:
            fel_skal.append(f"{filnamn_kort}: {type(e).__name__}")
            continue
        rader, karta, rubrikrad = _hitta_blad(blad, spec)
        if rader is None:
            forsta = next((r for b in blad.values() for r in b[:30]
                           if sum(1 for c in r if c) >= 3), [])
            fel_skal.append(f"{filnamn_kort}: rubriker? "
                            f"{[str(c)[:28] for c in forsta[:8]]}")
            continue
        filnamn = unquote(fil_url.split("/")[-1].split("?")[0])
        kategori = (filnamn.rsplit(".", 1)[0].split("--")[-1]
                    .replace("-", " ").strip().capitalize()
                    if "--" in filnamn else None)
        for rad in rader[rubrikrad + 1:]:
            def cell(falt):
                j = karta.get(falt)
                return (str(rad[j]).strip()
                        if j is not None and j < len(rad)
                        and rad[j] is not None else "")
            titel = cell("namn")
            if not titel:
                continue
            if len(titel) > 300:
                titel = titel[:297] + "…"
            deadline = _till_datum(rad[karta["start"]]
                                   if karta["start"] < len(rad) else None)
            varde = None
            if cell("varde"):
                siffror = re.sub(r"[^\d]", "", cell("varde"))
                if siffror.isdigit() and 4 <= len(siffror) <= 12:
                    varde = int(siffror)
            slug = _slug(f"sthlm-{kategori or filnamn}-{titel[:120]}")
            sedda[slug] = sedda.get(slug, 0) + 1
            if sedda[slug] > 1:
                slug = f"{slug}-{sedda[slug]}"
            det = {"Upphandlingsarbetet startar": deadline or "Ej angivet",
                   "Källa": "Stockholms stads upphandlingsplan"}
            if kategori:
                det["Kategori"] = kategori
            if cell("avtalsstart"):
                det["Planerad avtalsstart"] = (
                    _till_datum(rad[karta["avtalsstart"]])
                    or cell("avtalsstart"))
            post = dict(
                typ="planerad", organisation_id=org["id"],
                titel=titel[:500],
                sammanfattning=("Stockholms stad planerar att påbörja "
                                "upphandlingsarbetet"
                                + (f" {deadline[:7]}" if deadline else "")
                                + ". Annonsering sker senare i "
                                "processen."),
                kalla="plan-stockholm",
                extern_id=f"plan:sthlm:{slug}",
                deadline=deadline, url=STHLM_URL,
                detaljer=json.dumps(det, ensure_ascii=False))
            if varde:
                post["varde"] = varde
                post["varde_sort"] = "uppskattning"
            poster.append(post)
    if not poster and fel_skal:
        raise ValueError(f"alla {len(fel_skal)} filer otolkbara: "
                         + " | ".join(fel_skal[:3]))
    return poster


_ADDA_SEKTIONER = (
    ("annonseras", "upphandlingar som annonseras"),
    ("utredning", "avtalsområden under utredning"),
    ("nasta_plan", "planerade att ingå i upphandlingsplan"),
)
_ADDA_ETIKETTER = ("Ramavtal", "Kategori", "Beskrivning", "Avtalsstart",
                   "Planerad avtalsstart")


def _adda_ur_text(html_text: str):
    """Textbaserad reserv: Addas sida renderas utan riktiga
    table-element, men textflödet är strikt: etikettraden "Ramavtal"
    följs av titeln, "Avtalsstart" av månad och år. Kategorirubriker
    är korta rader mellan posterna."""
    soup = BeautifulSoup(html_text, "html.parser")
    rader = [r.strip() for r in soup.get_text("\n").split("\n")
             if r.strip()]
    ut = []
    sektion, kategori = None, None
    i = 0
    while i < len(rader):
        rad = rader[i]
        lag = rad.lower()
        ny_sektion = next((namn for namn, fras in _ADDA_SEKTIONER
                           if fras in lag), None)
        if ny_sektion:
            sektion, kategori = ny_sektion, None
            i += 1
            continue
        if lag.startswith("har du frågor") or lag.startswith(
                "kontaktuppgifter"):
            sektion = None
        if sektion is None:
            i += 1
            continue
        if rad == "Kategori" and i + 1 < len(rader):
            nasta = rader[i + 1]
            if nasta not in _ADDA_ETIKETTER and len(nasta) < 60:
                kategori = nasta
                i += 2
                continue
        if rad == "Ramavtal" and i + 1 < len(rader):
            titel = rader[i + 1]
            if (titel in _ADDA_ETIKETTER or len(titel) > 150
                    or titel.lower() in _MANADER):
                i += 1
                continue
            manad, ar = None, None
            for framat in rader[i + 2:i + 7]:
                if framat.lower() in _MANADER:
                    manad = framat.lower()
                elif re.fullmatch(r"20\d{2}", framat):
                    ar = framat
                elif framat == "Ramavtal":
                    break
            start_text = (f"{manad} {ar}" if manad and ar else (ar or ""))
            ut.append((titel, sektion, kategori, start_text))
            i += 2
            continue
        # Kategorirubrik i annonserings/plan-sektionerna: kort rad
        # direkt före nästa etikettblock
        if (rad not in _ADDA_ETIKETTER and len(rad) < 60
                and not rad.endswith(".")
                and lag not in _MANADER
                and not re.fullmatch(r"20\d{2}", rad)
                and i + 1 < len(rader) and rader[i + 1] == "Ramavtal"):
            kategori = rad
        i += 1
    return ut


def tolka_adda_plan(html_text: str):
    """Addas upphandlingsplan: tre h2-sektioner (annonseras i år, under
    utredning, nästa års plan) med kategorirubriker och tabellrader.
    Ger (titel, sektion, kategori, avtalsstart_text)."""
    soup = BeautifulSoup(html_text, "html.parser")
    ut = []
    sektion = None
    kategori = None
    for el in soup.find_all(["h2", "h3", "strong", "table", "p"]):
        text = el.get_text(" ", strip=True)
        if el.name == "h2":
            lag = text.lower()
            if "annonseras" in lag:
                sektion = "annonseras"
            elif "utredning" in lag:
                sektion = "utredning"
            elif "planerade att ingå" in lag or "2027" in lag:
                sektion = "nasta_plan"
            else:
                sektion = None
            kategori = None
            continue
        if sektion is None or el.name != "table":
            # Kategorirubriker är korta textstumpar mellan tabellerna
            if (el.name in ("h3", "strong", "p") and text
                    and len(text) < 60 and "avtalsstart" not in text.lower()
                    and text not in ("Ramavtal", "Beskrivning", "Kategori")):
                kategori = text
            continue
        for tr in el.find_all("tr"):
            celler = [c.get_text(" ", strip=True)
                      for c in tr.find_all(["td", "th"])]
            if not celler:
                continue
            lank = tr.find("a")
            titel = lank.get_text(" ", strip=True) if lank else ""
            _ETIKETTER = ("Ramavtal", "Kategori", "Beskrivning",
                          "Avtalsstart", "Planerad avtalsstart")
            if not titel:
                # Radtitel utan länk: cellen efter "Ramavtal"-etiketten,
                # annars cell som slutar på årtal (DIS-poster)
                if "Ramavtal" in celler:
                    i = celler.index("Ramavtal")
                    for c in celler[i + 1:]:
                        if (c and c not in _ETIKETTER and len(c) < 120
                                and c.lower() not in _MANADER
                                and not re.fullmatch(r"20\d{2}", c)):
                            titel = c
                            break
                if not titel:
                    for c in celler:
                        if (re.search(r"\b20\d{2}$", c) and len(c) < 120
                                and c.lower() not in _MANADER):
                            titel = c
                            break
            if not titel or titel in ("Ramavtal", "Kategori"):
                continue
            manad = next((c.lower() for c in celler
                          if c.lower() in _MANADER), None)
            ar = next((c for c in celler
                       if re.fullmatch(r"20\d{2}", c)), None)
            kat = kategori
            if sektion == "utredning":
                # Utredningstabellen bär kategorin per rad
                for c in celler:
                    if (len(c) < 50 and c != titel
                            and not c[0:1].islower()
                            and c.lower() not in _MANADER
                            and not re.fullmatch(r"20\d{2}", c)
                            and c not in _ETIKETTER
                            and len(c.split()) <= 5):
                        kat = c
                        break
            start_text = (f"{manad} {ar}" if manad and ar
                          else (ar or ""))
            ut.append((titel, sektion, kat, start_text))
    if not ut:
        ut = _adda_ur_text(html_text)
    return ut


def _poster_adda(db, index):
    from .adda import KOPARE
    org, *_ = hamta_eller_skapa(db, KOPARE, None, *index)
    if org is None:
        return []
    poster = []
    sedda: dict[str, int] = {}
    for titel, sektion, kategori, start_text in tolka_adda_plan(
            _hamta(ADDA_PLAN_URL)):
        if sektion == "annonseras":
            deadline = "2026-07-01"
            sam = ("Adda Inköpscentral planerar annonsering under 2026"
                   + (f", med avtalsstart {start_text}" if start_text
                      else "") + ". Nationellt ramavtal.")
        elif sektion == "nasta_plan":
            deadline = "2027-07-01"
            sam = ("Planerad att ingå i Addas upphandlingsplan 2027"
                   + (f", med avtalsstart {start_text}" if start_text
                      else "") + ". Nationellt ramavtal.")
        else:
            deadline = None
            sam = ("Avtalsområde under utredning hos Adda Inköpscentral: "
                   "behovs- och marknadsanalys pågår.")
        slug = _slug(titel)
        sedda[slug] = sedda.get(slug, 0) + 1
        if sedda[slug] > 1:
            slug = f"{slug}-{sedda[slug]}"
        det = {"Källa": "Addas upphandlingsplan 2026"}
        if kategori:
            det["Kategori"] = kategori
        if start_text:
            det["Planerad avtalsstart"] = start_text
        poster.append(dict(
            typ="planerad", organisation_id=org["id"], titel=titel[:500],
            sammanfattning=sam, kalla="plan-adda",
            extern_id=f"plan:adda:{slug}",
            deadline=deadline, url=ADDA_PLAN_URL,
            detaljer=json.dumps(det, ensure_ascii=False)))
    return poster


_INVESTERING = re.compile(
    r"^(?P<titel>.{4,150}?):\s*(?P<mnkr>\d+(?:[.,]\d+)?)\s*"
    r"m(?:iljoner|nkr)", re.I)


def tolka_investeringssida(html_text: str):
    """Investeringsprojekt ur en budget/MRP-sidas HTML: rader på formen
    'Projektnamn: NN miljoner kronor' efter en investeringsrubrik.
    Ger (titel, belopp_kr, periodtext)."""
    soup = BeautifulSoup(html_text, "html.parser")
    text = soup.get_text("\n")
    period = ""
    pm = re.search(r"[Ii]nvesteringsplanen[^.]*\.", text)
    if pm:
        ar = re.findall(r"20\d{2}", pm.group(0))
        if ar:
            period = f"{min(ar)}\u2013{max(ar)}"
    ut = []
    aktiv = False
    for rad in (r.strip() for r in text.split("\n")):
        if not rad:
            continue
        lag = rad.lower()
        if "större investeringar" in lag or "investeringsprojekt" in lag:
            aktiv = True
            continue
        if aktiv and (rad.startswith("#") or lag.startswith(
                ("kommunfullmäktige", "läs mål", "kontakt"))):
            aktiv = False
        if not aktiv:
            continue
        m = _INVESTERING.match(rad)
        if m:
            belopp = int(float(m.group("mnkr").replace(",", ".")) * 1e6)
            ut.append((m.group("titel").strip(), belopp, period))
    return ut


def _poster_budget_trollhattan(db, index):
    """Trollhättans MRP-sida listar periodens större investeringar med
    belopp direkt i HTML - tidigaste signalen i hela kedjan (beslutad
    budget, före upphandlingsplanen)."""
    org, *_ = hamta_eller_skapa(db, "Trollhättans Stad", None, *index)
    if org is None:
        return []
    poster = []
    sedda: dict[str, int] = {}
    for titel, belopp, period in tolka_investeringssida(
            _hamta(MRP_TROLLHATTAN_URL)):
        slug = _slug(titel)
        sedda[slug] = sedda.get(slug, 0) + 1
        if sedda[slug] > 1:
            slug = f"{slug}-{sedda[slug]}"
        det = {"Investeringsbelopp": f"{belopp // 1000000} mnkr",
               "Källa": "Trollhättans Stads mål- och resursplan (MRP)"}
        if period:
            det["Planperiod"] = period
        poster.append(dict(
            typ="planerad", organisation_id=org["id"], titel=titel[:500],
            sammanfattning=(
                f"Upptagen i kommunens investeringsplan"
                + (f" {period}" if period else "")
                + f" med {belopp // 1000000} miljoner kronor. Beslutad "
                "budget, upphandling följer senare i processen."),
            kalla="budget-trollhattan",
            extern_id=f"budget:trollhattan:{slug}",
            deadline=None, url=MRP_TROLLHATTAN_URL,
            varde=belopp, varde_sort="uppskattning",
            detaljer=json.dumps(det, ensure_ascii=False)))
    return poster


def tolka_meetingplus_moten(html_text: str, bas: str):
    """Mötesindexets länkar -> [(datum, nämnd, mötes-url)]."""
    soup = BeautifulSoup(html_text, "html.parser")
    ut, sedda = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"/committees/([^/]+)/([^/?#]+)", href)
        if not m:
            continue
        datum = re.search(r"(20\d{2}-\d{2}-\d{2})", m.group(2))
        if not datum or href in sedda:
            continue
        sedda.add(href)
        url = href if href.startswith("http") else bas + href
        namnd = m.group(1).replace("-", " ").capitalize()
        ut.append((datum.group(1), namnd, url))
    return ut


def tolka_meetingplus_arenden(html_text: str):
    """Mötessidans ärendelista -> [(nummer, rubrik)]. Rubrikerna ligger
    i klartext i HTML, så ingen PDF-tolkning behövs."""
    soup = BeautifulSoup(html_text, "html.parser")
    ut, sedda = [], set()
    for a in soup.find_all("a", href=True):
        if not a["href"].startswith("#id_"):
            continue
        text = " ".join(a.get_text(" ", strip=True).split())
        m = re.match(r"^(\d{1,3})\s+(.{8,300})$", text)
        if not m:
            continue
        rubrik = m.group(2).strip()
        if rubrik.lower() in sedda:
            continue
        sedda.add(rubrik.lower())
        ut.append((int(m.group(1)), rubrik))
    return ut


def ar_beslutsarende(rubrik: str) -> bool:
    """Nyckelordsfilter: ska något byggas/köpas? Brusord vinner alltid,
    så motioner, remisser och rapporter sållas bort även när de nämner
    upphandling. Personärenden fastnar aldrig i nyckelorden."""
    lag = rubrik.lower()
    if any(ord_ in lag for ord_ in _BRUSORD):
        return False
    if any(ord_ in lag for ord_ in _BESLUTSORD):
        return True
    # Objekt som ska byggas eller rustas: "Nytt tillagningskök för äldre"
    if any(ord_ in lag for ord_ in _OBJEKTORD):
        return bool(_NY_PREFIX.match(lag)) or any(
            ord_ in lag for ord_ in _BYGGORD)
    return False


def _poster_meetingplus(db, index):
    idag = date.today().isoformat()
    granspunkt = (date.today() - timedelta(days=180)).isoformat()
    poster = []
    statistik = []
    instanser = list(MEETINGPLUS)
    try:  # instanser funna av sondera-meetingplus
        kanda = {u for _, u in MEETINGPLUS}
        for r in db.execute("SELECT organisationsnamn, bas_url FROM "
                            "meetingplus_instans WHERE aktiv=1"):
            if r["bas_url"] not in kanda:
                instanser.append((r["organisationsnamn"], r["bas_url"]))
    except Exception:
        pass
    for kommunnamn, bas in instanser:
        org, *_ = hamta_eller_skapa(db, kommunnamn, None, *index)
        if org is None:
            continue
        try:
            alla_moten = tolka_meetingplus_moten(
                _hamta(bas + "/overview"), bas)
        except Exception as e:  # en portal nere ska inte fälla de andra
            statistik.append(f"{kommunnamn}: {type(e).__name__}")
            continue
        hallna = sorted((m for m in alla_moten
                         if granspunkt <= m[0] <= idag), reverse=True)
        kommande = sorted(m for m in alla_moten if m[0] > idag)
        moten = hallna[:MAX_HALLNA] + kommande[:MAX_KOMMANDE]
        sedda: dict[str, int] = {}
        arenden_tot = fel_moten = 0
        for datum, namnd, moteslank in moten:
            try:
                arenden = tolka_meetingplus_arenden(_hamta(moteslank))
            except Exception:
                fel_moten += 1
                continue
            arenden_tot += len(arenden)
            for nummer, rubrik in arenden:
                if not ar_beslutsarende(rubrik):
                    continue
                slug = _slug(f"{datum}-{nummer}-{rubrik[:60]}")
                sedda[slug] = sedda.get(slug, 0) + 1
                if sedda[slug] > 1:
                    slug = f"{slug}-{sedda[slug]}"
                kommande = datum >= idag
                det = {"Nämnd": namnd, "Sammanträde": datum,
                       "Ärendenummer": str(nummer),
                       "Källa": f"{kommunnamn}s möteshandlingar"}
                poster.append(dict(
                    typ="planerad", organisation_id=org["id"],
                    titel=rubrik[:500],
                    sammanfattning=(
                        f"Ärende {nummer} {'på dagordningen för' if kommande else 'behandlat av'} "
                        f"{namnd} {datum}. Politiskt beslutsärende - "
                        "eventuell upphandling följer senare i processen."),
                    kalla="protokoll-meetingplus",
                    extern_id=f"mp:{_slug(kommunnamn)}:{slug}",
                    deadline=None, url=moteslank,
                    detaljer=json.dumps(det, ensure_ascii=False)))
        statistik.append(f"{kommunnamn}: {len(moten)} av "
                         f"{len(alla_moten)} möten"
                         + (f" ({fel_moten} fel)" if fel_moten else "")
                         + f", {arenden_tot} ärenden")
    if statistik:
        print("  meetingplus - " + " | ".join(statistik))
    return poster


_KVARTAL_RAD = re.compile(r"^(20\d{2})\s*(?:Q|kvartal\s*)([1-4])", re.I)


def tolka_uppsala(html_text: str):
    """Uppsalas upphandlingsplan: tabeller med Period | Upphandling |
    Avtal, där perioden är kvartal ('2026 Q2'). Ger (titel, deadline,
    period, avtalstyp)."""
    soup = BeautifulSoup(html_text, "html.parser")
    ut = []
    for tabell in soup.find_all("table"):
        for tr in tabell.find_all("tr"):
            celler = [c.get_text(" ", strip=True)
                      for c in tr.find_all(["td", "th"])]
            celler = [c for c in celler if c]
            if len(celler) < 2:
                continue
            m = _KVARTAL_RAD.match(celler[0])
            if not m:
                continue
            titel = celler[1]
            if not titel or len(titel) > 300 or titel.lower() in (
                    "upphandling", "avtal"):
                continue
            ar, kvartal = int(m.group(1)), int(m.group(2))
            # Kvartalets första dag som ungefärlig starttidpunkt
            deadline = f"{ar}-{(kvartal - 1) * 3 + 1:02d}-01"
            avtalstyp = celler[2] if len(celler) > 2 else ""
            ut.append((titel, deadline, f"{ar} Q{kvartal}", avtalstyp))
    return ut


def _poster_uppsala(db, index):
    org, *_ = hamta_eller_skapa(db, "Uppsala kommun", None, *index)
    if org is None:
        return []
    poster = []
    sedda: dict[str, int] = {}
    for titel, deadline, period, avtalstyp in tolka_uppsala(
            _hamta(UPPSALA_URL)):
        slug = _slug(titel)
        sedda[slug] = sedda.get(slug, 0) + 1
        if sedda[slug] > 1:
            slug = f"{slug}-{sedda[slug]}"
        det = {"Planerad period": period,
               "Källa": "Uppsala kommuns upphandlingsplan"}
        if avtalstyp:
            det["Avtalsform"] = avtalstyp
        poster.append(dict(
            typ="planerad", organisation_id=org["id"], titel=titel[:500],
            sammanfattning=(
                f"Uppsala kommun planerar att upphandla under {period}"
                + (f" ({avtalstyp.lower()})" if avtalstyp else "")
                + ". Annonsering sker senare i processen."),
            kalla="plan-uppsala", extern_id=f"plan:uppsala:{slug}",
            deadline=deadline, url=UPPSALA_URL,
            detaljer=json.dumps(det, ensure_ascii=False)))
    return poster


KALLOR = (("plan-trollhattan", _poster_trollhattan),
          ("plan-skovde", _poster_skovde),
          ("plan-region-stockholm", _poster_region_sthlm),
          ("plan-stockholm", _poster_stockholm),
          ("plan-adda", _poster_adda),
          ("budget-trollhattan", _poster_budget_trollhattan),
          ("plan-uppsala", _poster_uppsala),
          ("protokoll-meetingplus", _poster_meetingplus))


def kor(torr=False) -> list[dict]:
    """Kör alla plankällor. Varje källa: uppdateringssvep för flyttade
    perioder, sedan kor_kalla för nya poster (dedup, yrken, historik)."""
    db = get_db()
    index = bygg_namnindex(db)
    resultat = []
    for kallnamn, bygg in KALLOR:
        try:
            poster = bygg(db, index)
        except Exception as e:
            # Logga även misslyckanden, annars ser adminöversikten en
            # trasig källa som "senast ok" i evighet.
            from datetime import datetime as _dt
            nu = _dt.now().isoformat(timespec="seconds")
            db.execute(
                "INSERT INTO korningshistorik (kalla, startad, avslutad, "
                "status, fel) VALUES (?,?,?,'fel',?)",
                (kallnamn, nu, nu, f"{type(e).__name__}: {e}"[:500]))
            db.commit()
            resultat.append(dict(kalla=kallnamn, nya=0, dubbletter=0,
                                 uppdaterade=0, status="fel",
                                 fel=f"{type(e).__name__}: {e}"))
            continue
        uppdaterade = 0
        if not torr:
            for p in poster:
                bef = db.execute(
                    "SELECT id, deadline FROM signal WHERE extern_id=?",
                    (p["extern_id"],)).fetchone()
                if bef and (bef["deadline"] or "") != (p["deadline"] or ""):
                    db.execute(
                        "UPDATE signal SET deadline=?, sammanfattning=?, "
                        "detaljer=? WHERE id=?",
                        (p["deadline"], p["sammanfattning"],
                         p["detaljer"], bef["id"]))
                    uppdaterade += 1
            db.commit()
        r = kor_kalla(kallnamn, lambda p=poster: iter(p), torr=torr)
        r["uppdaterade"] = uppdaterade
        resultat.append(r)
    return resultat
