"""KommersAnnons - efterannonser under EU-tröskeln. KOMPLETTERAR TED.

Mallen avgörs av sidtiteln: eForms-varianten ('Notice') är över tröskeln och
finns redan via TED - hoppas. 'Tilldelningsmeddelande' är den nationella
blanketten under tröskeln: LUCKAN.

Fällorna som redan är betalda för:
  - Listsidan är 1,4 MB varav 1,33 MB CPV-rullgardin - select strippas först.
  - Sidfoten klipps på '©': annars får sista etiketten alltid värdet
    '© 2026 - Antirio' och ser ifylld ut.
  - 'Nationellt organisationsnummer' finns TVÅ gånger (köpare + vinnare) -
    bara sekventiell etikettläsning skiljer dem åt.
  - 'Officellt namn' är deras stavfel, inte vårt.
  - Avbrutna upphandlingar ('avbr' täcker avbryts/avbrutits/avbruten) är
    inga avtal och hoppas.
  - Deras belopp har fyra decimaler: '2161940,0000'.
"""
import html
import json
import re
import time

from ..db import get_db
from ..matchning import bygg_namnindex, hamta_eller_skapa, normalisera_orgnr
from .bas import kor_avtalskalla
from .gemensamt import http_session

BAS = "https://www.kommersannons.se"
LISTA_URL = f"{BAS}/Notices/AwardNotices"
DETALJ = f"{BAS}/Notices/AwardNotice/{{}}"
PAUS = 0.7
MAX_SIDOR = 4
STOPP_EFTER_KANDA = 60

ETIKETTER = [
    ("kop_namn", "Officellt namn"),
    ("kop_orgnr", "Nationellt organisationsnummer"),
    ("kop_tel", "Telefon"),
    ("kop_epost", "E-postadress"),
    ("gemensam", "Gemensam upphandling"),
    ("kop_typ", "Typ av upphandlande myndighet eller enhet"),
    ("roll", "Organisationsroll"),
    ("verksamhet", "Huvudsaklig verksamhet"),
    ("via_inkopscentral", "Kontrakt tilldelas av en inköpscentral"),
    ("pa_uppdrag", "Upphandlingen genomförs på uppdrag av annan myndighet eller enhet"),
    ("titel", "Titel"),
    ("referens", "Referensnummer"),
    ("beskrivning", "Beskrivning"),
    ("cpv", "Huvudsaklig CPV-kod"),
    ("kontraktstyp", "Typ av kontrakt"),
    ("uppskattat_varde", "Uppskattat värde"),
    ("delar", "Information om delar"),
    ("cpv_extra", "Ytterligare CPV-koder"),
    ("nuts", "Plats för utförande (NUTS-kod)"),
    ("period_start", "Avtalsperiodens start"),
    ("period_slut", "Avtalsperiodens slut"),
    ("optioner", "Optioner används"),
    ("optionsbeskrivning", "Beskrivning av optioner"),
    ("meddelande", "Meddelande"),
    ("antal_anbud", "Antal mottagna anbud"),
    ("anbud_eu", "Antal mottagna anbud från anbudsgivare från andra EU-medlemsstater"),
    ("anbud_utanfor", "Antal anbud mottagna från anbudsgivare från länder utanför EU"),
    ("anbud_sme", "Antal anbud som mottagits från små och medelstora företag"),
    ("lagsta_anbud", "Lägsta anbud"),
    ("hogsta_anbud", "Högsta anbud"),
    ("underleverantor", "Kommer delar av kontraktet uppfyllas av underleverantörer?"),
    ("direkttilldelning", "Motiv för direkttilldelning"),
    ("direkt_beskrivning", "Ytterligare beskrivning direkttilldelning"),
    ("dis_avslut", "Avslutande av dynamiskt inköpssystem"),
    ("tidigare_id", "Tidigare publikations-id"),
    ("beslutsdatum", "Beslutsdatum"),
    ("tilldelningsgrund", "Tilldelningsgrund"),
    ("vinnare", "Officiellt namn anbudsgivare"),
    ("vinnare_orgnr", "Nationellt organisationsnummer"),
    ("vinnare_kontakt", "Kontaktperson"),
    ("vinnare_epost", "E-post"),
    ("vinnare_tel", "Kontaktperson telefon"),
    ("kontraktsdatum", "Kontraktsdatum"),
    ("accepterad", "Accepterad"),
    ("rang", "Rang"),
    ("upphandlingsnummer", "Nationellt upphandlingsnummer"),
    ("publicerad", "Datum då annonsen skickades för publicering"),
    ("ted_nummer", "Ted annonsnummer"),
]


def _ren_text(raw: str) -> str:
    utan = re.sub(r"<select.*?</select>", " ", raw, flags=re.S | re.I)
    utan = re.sub(r"<script.*?</script>", " ", utan, flags=re.S | re.I)
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", utan)))
    return text.split("©")[0].strip()


def _falt(text: str) -> dict:
    """Sekventiell etikettläsning: varje etikett söks EFTER den föregående."""
    funna, pos = [], 0
    for nyckel, etikett in ETIKETTER:
        i = text.find(etikett, pos)
        if i < 0:
            continue
        funna.append((nyckel, i, i + len(etikett)))
        pos = i + len(etikett)
    ut = {}
    for k, (nyckel, _start, slut_etikett) in enumerate(funna):
        nasta = funna[k + 1][1] if k + 1 < len(funna) else len(text)
        ut[nyckel] = text[slut_etikett:nasta].strip(" :")
    return ut


def _datum(s):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s or "")
    return m.group(1) if m else None


def _belopp(s):
    m = re.search(r"(\d[\d\s]*)(?:[,.]\d+)?", (s or "").replace("\xa0", " "))
    if not m:
        return None
    siffror = m.group(1).replace(" ", "")
    return int(siffror) if siffror.isdigit() else None


def _heltal(s):
    m = re.search(r"(\d+)", s or "")
    return int(m.group(1)) if m else None


def _hamta_ids(session, max_sidor):
    r = session.get(LISTA_URL, timeout=60)
    r.raise_for_status()
    ids = list(dict.fromkeys(re.findall(r"/Notices/AwardNotice/(\d+)", r.text)))
    token = re.search(
        r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', r.text)
    for sida in range(1, max_sidor):
        data = {"PageIndex": str(sida)}
        if token:
            data["__RequestVerificationToken"] = token.group(1)
        rr = session.post(LISTA_URL, data=data, timeout=60)
        if rr.status_code != 200:
            break
        nya = re.findall(r"/Notices/AwardNotice/(\d+)", rr.text)
        if not nya:
            break
        ids.extend(nya)
        time.sleep(1)
    return list(dict.fromkeys(ids))


def tolka_detaljsida(raw: str) -> dict | None:
    """Returnerar fältdict för strukturerade tilldelningar, None för övrigt.
    Bruten ut för testbarhet mot sparade sidor."""
    titel_tagg = re.search(r"<title>(.*?)</title>", raw, re.S)
    if not titel_tagg or not titel_tagg.group(1).strip().startswith(
            "Tilldelningsmeddelande"):
        return None
    f = _falt(_ren_text(raw))
    text_att_prova = (f.get("meddelande", "") + " "
                      + f.get("beskrivning", "")).lower()
    if "avbr" in text_att_prova:
        return None
    return f


def hamta(max_sidor: int = MAX_SIDOR, stopp: bool = True):
    db = get_db()
    session = http_session()
    ids = _hamta_ids(session, max_sidor)
    exakt, normaliserat, per_orgnr = bygg_namnindex(db)

    kanda_i_rad = 0
    for annons_id in ids:
        extern_id = f"kommers-tilldelning:{annons_id}"
        if db.execute("SELECT 1 FROM avtal WHERE extern_id=?",
                      (extern_id,)).fetchone():
            kanda_i_rad += 1
            if stopp and kanda_i_rad >= STOPP_EFTER_KANDA:
                break
            continue
        kanda_i_rad = 0

        try:
            raw = session.get(DETALJ.format(annons_id), timeout=45).text
        except Exception:
            continue
        finally:
            time.sleep(PAUS)

        f = tolka_detaljsida(raw)
        if f is None:
            continue

        org, _, _ = hamta_eller_skapa(
            db, f.get("kop_namn"), f.get("kop_orgnr"),
            exakt, normaliserat, per_orgnr)
        if org is None:
            continue

        varde = varde_sort = None
        if (b := _belopp(f.get("uppskattat_varde", ""))):
            varde, varde_sort = b, "uppskattning"
        else:
            lag = _belopp(f.get("lagsta_anbud", ""))
            hog = _belopp(f.get("hogsta_anbud", ""))
            if lag and (not hog or lag == hog):
                varde, varde_sort = lag, "anbud"

        cpv_m = re.search(r"(\d{8})", f.get("cpv", ""))
        # Hela den strukturerade annonsen i råform - allt Kommers visar.
        etikettnamn = dict(ETIKETTER)
        rajson = {etikettnamn[k]: v for k, v in f.items()
                  if v and k in etikettnamn}
        yield dict(
            organisation_id=org["id"],
            titel=(f.get("titel") or "namnlöst avtal")[:500],
            leverantor=(f.get("vinnare") or "").strip()[:500] or None,
            leverantor_orgnr=normalisera_orgnr(f.get("vinnare_orgnr")),
            varde=varde, varde_sort=varde_sort,
            antal_anbud=_heltal(f.get("antal_anbud")),
            startdatum=_datum(f.get("period_start", "")),
            slutdatum=_datum(f.get("period_slut", "")),
            cpv=cpv_m.group(1) if cpv_m else None,
            kalla="kommers-tilldelning",
            extern_id=extern_id,
            url=DETALJ.format(annons_id),
            detaljer=json.dumps(rajson, ensure_ascii=False) if rajson else None,
        )


def kor(torr=False, max_sidor=None, stopp=True):
    return kor_avtalskalla(
        "kommers", lambda: hamta(max_sidor or MAX_SIDOR, stopp=stopp),
        torr=torr)
