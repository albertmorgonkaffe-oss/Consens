"""Inloggade huvudvyer: Flödet, Organisationer, Avtal, Bevakningar,
sparade objekt, listor och export."""
import io
import json
from datetime import date
from urllib.parse import parse_qs

from flask import (Blueprint, g, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.datastructures import MultiDict

from ..cpv import BRANSCHER, prefix_for_bransch
from ..db import get_db
from ..yrken import YRKEN

bp = Blueprint("flode", __name__)

KONTOTYPER = {
    "kommun": "Kommun", "region": "Region", "myndighet": "Myndighet",
    "kommunalt_bolag": "Kommunalt bolag",
    "regionalt_bolag": "Regionalt bolag", "ovrig": "Övriga organisationer",
}
LAN = [
    "Blekinge", "Dalarna", "Gotland", "Gävleborg", "Halland", "Jämtland",
    "Jönköping", "Kalmar", "Kronoberg", "Norrbotten", "Skåne", "Stockholm",
    "Södermanland", "Uppsala", "Värmland", "Västerbotten", "Västernorrland",
    "Västmanland", "Västra Götaland", "Örebro", "Östergötland",
]
UTGANGSSPANN = {"3": (0, 3), "6": (3, 6), "12": (6, 12), "18": (12, 18)}
UTGANGSETIKETT = {"3": "Inom 3 månader", "6": "3–6 månader",
                  "12": "6–12 månader", "18": "12–18 månader",
                  "passerad": "Utgångna"}
DEADLINESPANN = {"7": (0, 7), "14": (7, 14), "30": (14, 30), "90": (30, 90)}
DEADLINEETIKETT = {"7": "Inom 1 vecka", "14": "1–2 veckor",
                   "30": "2–4 veckor", "90": "1–3 månader",
                   "passerad": "Passerade"}
FILTERNYCKLAR = ("q", "typ", "bransch", "yrke", "kontotyp", "lan",
                 "utgang", "deadline")
KALLNAMN = {"ted": "TED", "ted-tilldelning": "TED",
            "kommers": "KommersAnnons", "kommers-tilldelning": "KommersAnnons",
            "e-avrop": "e-Avrop", "adda": "Adda", "avropa": "Avropa.se", "hbv": "HBV", "demo": "Demo"}
# Källornas råetiketter putsas vid VISNING - datat lagras orört.
# "Officellt namn" är Kommers eget stavfel; det ska inte vara vårt ansikte.
ETIKETTPUTS = {
    "Officellt namn": "Upphandlande organisation",
    "Officiellt namn anbudsgivare": "Vinnande anbudsgivare",
    "Nationellt organisationsnummer": "Organisationsnummer",
    "Typ av upphandlande myndighet eller enhet": "Typ av organisation",
    "Huvudsaklig CPV-kod": "CPV",
    "Ytterligare CPV-koder": "Ytterligare CPV",
    "Plats för utförande (NUTS-kod)": "Plats (NUTS)",
    "Antal mottagna anbud": "Antal anbud",
    "Antal mottagna anbud från anbudsgivare från andra EU-medlemsstater":
        "Anbud från andra EU-länder",
    "Antal anbud mottagna från anbudsgivare från länder utanför EU":
        "Anbud från länder utanför EU",
    "Antal anbud som mottagits från små och medelstora företag":
        "Anbud från små och medelstora företag",
    "Datum då annonsen skickades för publicering": "Publicerad",
    "Ted annonsnummer": "TED-annonsnummer",
    "Nationellt upphandlingsnummer": "Upphandlingsnummer",
    "Kommer delar av kontraktet uppfyllas av underleverantörer?":
        "Underleverantörer",
    "Upphandlingen genomförs på uppdrag av annan myndighet eller enhet":
        "På uppdrag av annan organisation",
    "Kontrakt tilldelas av en inköpscentral": "Via inköpscentral",
}
DOLJ_ETIKETTER = {"Titel"}  # dubblerar kortets rubrik


def putsa_extra(ra: dict) -> dict:
    return {ETIKETTPUTS.get(k, k): v for k, v in ra.items()
            if k not in DOLJ_ETIKETTER}


def man_kvar(datum):
    if not datum:
        return None
    try:
        d = date.fromisoformat(datum)
    except ValueError:
        return None
    dagar = (d - date.today()).days
    if dagar < 0:
        # Avrundningen får aldrig dölja att datumet passerat: -5 dagar
        # avrundas annars till 0 månader och ser aktivt ut.
        return min(-1, round(dagar / 30.44))
    return round(dagar / 30.44)


def dagar_kvar(datum):
    if not datum:
        return None
    try:
        return (date.fromisoformat(datum) - date.today()).days
    except ValueError:
        return None


def loptid_procent(start, slut):
    try:
        s, e = date.fromisoformat(start), date.fromisoformat(slut)
    except (TypeError, ValueError):
        return 0
    hela = (e - s).days
    if hela <= 0:
        return 100
    return max(0, min(100, round(100 * (date.today() - s).days / hela)))


def hamta_signaler(db, args: MultiDict, nyare_an: str | None = None,
                   bara_aktiva: bool = False):
    """Delad frågelogik för Flödet och e-postdigesten."""
    q = args.get("q", "").strip()
    orger = args.getlist("org")
    typer = args.getlist("typ")
    branscher = args.getlist("bransch")
    lan = args.getlist("lan")
    kontotyper = args.getlist("kontotyp")
    utgang = args.getlist("utgang")
    deadline = args.getlist("deadline")
    yrken_valda = args.getlist("yrke")

    sql = ("SELECT s.*, o.namn AS org_namn, o.kontotyp, o.orgnr, "
           "o.webbplats AS org_webbplats, "
           "(CASE WHEN s.deadline IS NOT NULL AND s.deadline < date('now') "
           " THEN 1 ELSE 0 END) AS passerad, "
           "(SELECT GROUP_CONCAT(yrke, '|') FROM signal_yrke sy "
           " WHERE sy.signal_id = s.id) AS yrken, "
           "a.startdatum, a.slutdatum, a.leverantor, a.leverantor_orgnr, "
           "a.antal_anbud, a.detaljer AS avtal_detaljer "
           "FROM signal s JOIN organisation o ON o.id=s.organisation_id "
           "LEFT JOIN avtal a ON a.id=s.avtal_id WHERE s.dublett_av IS NULL")
    params: list = []

    if orger:
        orgnr = [v for v in orger if not v.startswith("id:")]
        ider = [v[3:] for v in orger if v.startswith("id:")]
        villkor = []
        if orgnr:
            villkor.append(f"o.orgnr IN ({','.join('?' * len(orgnr))})")
            params.extend(orgnr)
        if ider:
            villkor.append(f"o.id IN ({','.join('?' * len(ider))})")
            params.extend(ider)
        sql += f" AND ({' OR '.join(villkor)})"

    if q:
        sql += " AND (s.titel LIKE ? OR s.sammanfattning LIKE ? OR o.namn LIKE ?)"
        params += [f"%{q}%"] * 3
    if typer:
        sql += f" AND s.typ IN ({','.join('?' * len(typer))})"
        params += typer
    if lan:
        sql += f" AND s.lan IN ({','.join('?' * len(lan))})"
        params += lan
    if kontotyper:
        sql += f" AND o.kontotyp IN ({','.join('?' * len(kontotyper))})"
        params += kontotyper
    prefixar = [p for b in branscher for p in prefix_for_bransch(b)]
    if prefixar:
        sql += " AND (" + " OR ".join("s.cpv LIKE ?" for _ in prefixar) + ")"
        params += [p + "%" for p in prefixar]
    if yrken_valda:
        sql += (" AND s.id IN (SELECT signal_id FROM signal_yrke WHERE yrke "
                f"IN ({','.join('?' * len(yrken_valda))}))")
        params += yrken_valda
    if nyare_an:
        sql += " AND s.upptackt > ?"
        params.append(nyare_an)
    if bara_aktiva:
        sql += " AND (s.deadline IS NULL OR s.deadline >= date('now'))"
    # Passerade poster är historik och ligger alltid längst bak; aktiva
    # sorteras på källans publiceringsdatum (backfill hamnar därmed rätt).
    sortera = (args.get("sortera") or "blandat")
    ordningar = {
        # Sortera på när posten kom in hos oss, alltså samma datum som
        # kortet visar. Publiceringsdatum går inte att blanda in: TED:s
        # historik är hämtad i klump, så en post publicerad i maj kan ha
        # kommit in i dag och skulle annars hamna långt ned i flödet.
        "nyast": "passerad, s.upptackt DESC, s.id DESC",
        "aldst": "passerad, s.upptackt ASC, s.id ASC",
        "publicerad": ("passerad, s.publicerad IS NULL, "
                       "s.publicerad DESC, s.upptackt DESC"),
        "deadline": ("passerad, s.deadline IS NULL, s.deadline ASC, "
                     "s.id DESC"),
        "varde": "passerad, s.varde IS NULL, s.varde DESC, s.id DESC",
    }
    # "Blandat" hämtas som nyast och vävs sedan om i Python, se nedan.
    if sortera == "blandat":
        sortera = "nyast"
        vav_om = True
    else:
        vav_om = False
    sql += " ORDER BY " + ordningar.get(sortera, ordningar["nyast"])

    signaler = []
    for s in db.execute(sql, params).fetchall():
        s = dict(s)
        s["man_kvar"] = man_kvar(s["deadline"]) if s["typ"] == "avtalsutgang" else None
        s["dagar_kvar"] = dagar_kvar(s["deadline"]) if s["typ"] == "upphandling" else None
        s["procent"] = loptid_procent(s["startdatum"], s["slutdatum"])
        try:
            s["extra"] = putsa_extra(json.loads(
                s.get("detaljer") or s.get("avtal_detaljer") or "{}"))
        except (TypeError, ValueError):
            s["extra"] = {}
        if utgang and s["typ"] == "avtalsutgang":
            traff = ("passerad" in utgang and s["passerad"]) or (
                not s["passerad"] and s["man_kvar"] is not None and any(
                    lo < s["man_kvar"] <= hi
                    for sp in utgang if (b := UTGANGSSPANN.get(sp))
                    for lo, hi in [b]))
            if not traff:
                continue
        if deadline and s["typ"] == "upphandling":
            traff = ("passerad" in deadline and s["passerad"]) or (
                not s["passerad"] and s["dagar_kvar"] is not None and any(
                    lo <= s["dagar_kvar"] <= hi
                    for sp in deadline if (b := DEADLINESPANN.get(sp))
                    for lo, hi in [b]))
            if not traff:
                continue
        signaler.append(s)
    if vav_om:
        signaler = _vav(signaler)
    return signaler


def registrator_epost(webbplats: str | None) -> str | None:
    """Gissa registratorsadressen ur organisationens webbplats.

    Svenska myndigheter använder nästan undantagslöst registrator@
    på sin egen domän. Gissningen märks ut i gränssnittet så att
    användaren kan kontrollera den innan mejlet skickas."""
    import re as _re
    if not webbplats:
        return None
    m = _re.search(r"https?://(?:www\.)?([\w.-]+\.\w{2,})", webbplats)
    if not m:
        return None
    doman = m.group(1).lower()
    if doman.count(".") > 2:      # djupa subdomäner blir opålitliga
        return None
    return f"registrator@{doman}"


def begaran_text(signal) -> str:
    """Färdig begäran om utlämnande av allmän handling.

    Förfrågningsunderlaget är en allmän handling och lämnas ut på
    begäran, men få vet hur man formulerar sig. Consens vet köpare,
    referens och titel, så texten kan skrivas färdig och användaren
    behöver bara skicka den."""
    ref = signal.get("extern_id") or ""
    ref = ref.split(":")[-1] if ":" in ref else ref
    titel = signal.get("titel") or ""
    org = signal.get("org_namn") or "er organisation"
    rader = [
        "Hej,",
        "",
        f"Jag begär att med stöd av tryckfrihetsförordningen få ta del "
        f"av handlingarna i följande upphandling hos {org}:",
        "",
        f"Upphandling: {titel}",
    ]
    if ref:
        rader.append(f"Referens hos er: {ref}")
    if signal.get("url"):
        rader.append(f"Annons: {signal['url']}")
    rader += [
        "",
        "Jag önskar särskilt förfrågningsunderlaget med "
        "kravspecifikation, utvärderingsmodell och avtalsvillkor, samt "
        "eventuella frågor och svar under anbudstiden.",
        "",
        "Handlingarna tas gärna emot i elektronisk form till denna "
        "adress. Om någon del omfattas av sekretess ber jag om ett "
        "skriftligt beslut med besvärshänvisning för den delen, och att "
        "övriga handlingar lämnas ut.",
        "",
        "Med vänlig hälsning",
    ]
    return "\n".join(rader)


def _vav(signaler: list) -> list:
    """Väv ihop flödet så toppen visar bredd i stället för en enda
    källas dagsskörd. En kommun som publicerar 130 planer samma dag ska
    inte äga de första tio sidorna. Vi växlar mellan signaltyper och
    släpper igenom högst två poster i rad från samma organisation, utan
    att rubba den grova tidsordningen (posterna kommer redan sorterade
    nyast först)."""
    if len(signaler) < 6:
        return signaler
    hinkar: dict = {}
    for s in signaler:
        hinkar.setdefault((s["passerad"], s["typ"]), []).append(s)
    ordning = sorted(hinkar, key=lambda n: (n[0], -len(hinkar[n])))
    ut, senaste_org, i_rad = [], None, 0
    while any(hinkar[n] for n in ordning):
        lade_till = False
        for nyckel in ordning:
            hink = hinkar[nyckel]
            if not hink:
                continue
            val = 0
            # Undvik tre i rad från samma organisation om alternativ finns
            if hink[0]["organisation_id"] == senaste_org and i_rad >= 2:
                val = next((j for j, s in enumerate(hink[:12])
                            if s["organisation_id"] != senaste_org), 0)
            s = hink.pop(val)
            if s["organisation_id"] == senaste_org:
                i_rad += 1
            else:
                senaste_org, i_rad = s["organisation_id"], 1
            ut.append(s)
            lade_till = True
        if not lade_till:
            break
    return ut


def beskriv_params(params_str: str) -> list[str]:
    """Querystring -> läsbara chips för bevakningskortet."""
    d = parse_qs(params_str)
    chips = []
    if d.get("q"):
        chips.append(f"”{d['q'][0]}”")
    for t in d.get("typ", []):
        chips.append("Avtalsutgång" if t == "avtalsutgang" else "Upphandling")
    chips += d.get("bransch", [])
    chips += d.get("yrke", [])
    chips += [KONTOTYPER.get(k, k) for k in d.get("kontotyp", [])]
    chips += d.get("lan", [])
    chips += [f"Utgång: {UTGANGSETIKETT.get(u, u)}" for u in d.get("utgang", [])]
    chips += [f"Deadline: {DEADLINEETIKETT.get(u, u)}" for u in d.get("deadline", [])]
    return chips


def _sparade_signal_ids(db):
    return {r["signal_id"] for r in db.execute(
        "SELECT signal_id FROM sparad_signal WHERE anvandare_id=?",
        (g.anvandare["id"],)).fetchall()}


def _sparade_avtal_ids(db):
    return {r["avtal_id"] for r in db.execute(
        "SELECT avtal_id FROM sparad_avtal WHERE anvandare_id=?",
        (g.anvandare["id"],)).fetchall()}


def _mina_listor(db):
    return db.execute("SELECT * FROM lista WHERE anvandare_id=? ORDER BY namn",
                      (g.anvandare["id"],)).fetchall()


PER_SIDA = 50


def _tal(n):
    """Svensk tusentalsformattering: 12 345."""
    return "{:,}".format(int(n)).replace(",", " ")


# Dagens fakta i flödet. Två sorters poster: dynamiska som räknas ur
# databasen (alltid färska, aldrig påhittade) och statiska produkttips
# (sanna påståenden om funktioner). En post väljs per dag, samma hela
# dagen, och kan kryssas bort för dagen i webbläsaren.
def _fakta_nya_idag(db):
    r = db.execute(
        "SELECT SUM(typ='upphandling') AS u, SUM(typ='avtalsutgang') AS a, "
        "SUM(typ='planerad') AS p FROM signal "
        "WHERE dublett_av IS NULL AND date(upptackt) = date('now')").fetchone()
    if not r or not any([r["u"], r["a"], r["p"]]):
        return None
    delar = []
    if r["u"]:
        delar.append(f"{_tal(r['u'])} upphandlingar")
    if r["a"]:
        delar.append(f"{_tal(r['a'])} avtalsutgångar")
    if r["p"]:
        delar.append(f"{_tal(r['p'])} planerade upphandlingar")
    if len(delar) > 1:
        text = ", ".join(delar[:-1]) + " och " + delar[-1]
    else:
        text = delar[0]
    return f"I dag har vi lagt in {text} i flödet."


def _fakta_totalt(db):
    r = db.execute(
        "SELECT COUNT(*) AS n, SUM(typ='upphandling') AS u, "
        "SUM(typ='avtalsutgang') AS a, SUM(typ='planerad') AS p "
        "FROM signal WHERE dublett_av IS NULL").fetchone()
    if not r or not r["n"]:
        return None
    return (f"Just nu ligger {_tal(r['n'])} signaler i Consens: "
            f"{_tal(r['u'] or 0)} upphandlingar, {_tal(r['a'] or 0)} "
            f"avtalsutgångar och {_tal(r['p'] or 0)} planerade.")


def _fakta_storsta_avtal(db):
    r = db.execute(
        "SELECT a.titel, a.varde, o.namn FROM avtal a "
        "JOIN organisation o ON o.id = a.organisation_id "
        "WHERE a.varde IS NOT NULL ORDER BY a.varde DESC LIMIT 1").fetchone()
    if not r:
        return None
    return (f"Det största avtalet i registret är {r['titel']} hos "
            f"{r['namn']}, värt {_tal(r['varde'] / 1_000_000)} mnkr.")


def _fakta_utgangar_12man(db):
    n = db.execute(
        "SELECT COUNT(*) AS n FROM avtal WHERE slutdatum >= date('now') "
        "AND slutdatum < date('now', '+12 months')").fetchone()["n"]
    if not n or n < 2:
        return None
    return (f"{_tal(n)} avtal löper ut inom de närmaste tolv månaderna. "
            "Varje utgång är en affär som öppnar sig.")


def _fakta_enanbud(db):
    r = db.execute(
        "SELECT COUNT(*) AS n, SUM(antal_anbud = 1) AS ett FROM avtal "
        "WHERE antal_anbud IS NOT NULL").fetchone()
    if not r or not r["n"]:
        return None
    return (f"{round(100 * r['ett'] / r['n'])} procent av de tilldelade "
            "avtalen i registret avgjordes med ett enda anbud. Ofta räcker "
            "det med att vara med för att ha en god chans.")


def _fakta_snitt_anbud(db):
    r = db.execute(
        "SELECT AVG(antal_anbud) AS snitt FROM avtal "
        "WHERE antal_anbud IS NOT NULL").fetchone()
    if not r or r["snitt"] is None:
        return None
    return (f"Snittet i registret är {str(round(r['snitt'], 1)).replace('.', ',')} "
            "anbud per tilldelat avtal.")


def _fakta_leverantorer(db):
    n = db.execute("SELECT COUNT(DISTINCT leverantor_orgnr) AS n FROM avtal "
                   "WHERE leverantor_orgnr IS NOT NULL").fetchone()["n"]
    if not n or n < 2:
        return None
    return (f"Leverantörsregistret rymmer {_tal(n)} företag som vunnit "
            "offentliga kontrakt. Alla har en egen profil.")


def _fakta_organisationer(db):
    n = db.execute("SELECT COUNT(*) AS n FROM organisation").fetchone()["n"]
    if not n or n < 2:
        return None
    return (f"Vi bevakar {_tal(n)} offentliga organisationer, från kommuner "
            "och regioner till myndigheter och offentliga bolag.")


def _fakta_langsiktig_plan(db):
    r = db.execute(
        "SELECT s.titel, s.deadline, o.namn FROM signal s "
        "JOIN organisation o ON o.id = s.organisation_id "
        "WHERE s.typ = 'planerad' AND s.dublett_av IS NULL "
        "AND s.deadline > date('now') "
        "ORDER BY s.deadline DESC LIMIT 1").fetchone()
    if not r:
        return None
    ar = r["deadline"][:4]
    return (f"Den mest långsiktiga planen i flödet just nu siktar på {ar}: "
            f"{r['titel']} hos {r['namn']}. Kravbilden går fortfarande att påverka.")


_FAKTA_DYNAMISKA = [
    _fakta_nya_idag, _fakta_totalt, _fakta_storsta_avtal,
    _fakta_utgangar_12man, _fakta_enanbud, _fakta_snitt_anbud,
    _fakta_leverantorer, _fakta_organisationer, _fakta_langsiktig_plan,
]

_FAKTA_TIPS = [
    "Spara ett filter som bevakning så landar nya träffar i morgondigesten.",
    "Mejlknappen i detaljpanelen skriver en färdig begäran om att få ut förfrågningsunderlaget.",
    "Följ en leverantör med knappen Följ på profilen, så säger vi till när deras avtal närmar sig utgång.",
    "Varje organisationssida har ett årshjul som visar avtalsutgångarna 24 månader framåt.",
    "Under Insikter ser du konkurrensläget per bransch, svagast konkurrens först.",
    "Leverantörsprofiler och branscher kan laddas ner som färdiga rapporter i Word.",
    "Sorteringen Blandat urval väver ihop signaltyperna och släpper aldrig fler än två i rad från samma organisation.",
    "Knappen på en organisationssida låter dig bevaka allt organisationen gör.",
    "Bokmärket uppe till höger på ett kort sparar signalen så du hittar tillbaka.",
    "Många TED-annonser har direktlänk till förfrågningsunderlaget i detaljpanelen.",
]


def dagens_fakta(db):
    """En post per dag ur banken. Dynamiska fakta varvas med tips genom
    att listorna flätas; faller en dynamisk post bort (tom databas)
    provas nästa i ordningen."""
    from ..cache import hamta as cache_hamta

    bank = []
    for i in range(max(len(_FAKTA_DYNAMISKA), len(_FAKTA_TIPS))):
        if i < len(_FAKTA_DYNAMISKA):
            bank.append(_FAKTA_DYNAMISKA[i])
        if i < len(_FAKTA_TIPS):
            bank.append(_FAKTA_TIPS[i])

    idag = date.today()

    def bygg():
        start = idag.toordinal() % len(bank)
        for hopp in range(len(bank)):
            post = bank[(start + hopp) % len(bank)]
            text = post if isinstance(post, str) else post(db)
            if text:
                return text
        return None

    return cache_hamta(f"dagens-fakta:{idag.isoformat()}", bygg,
                       livslangd=86400)


@bp.route("/flode")
def flodet():
    from urllib.parse import urlencode
    db = get_db()
    typer = request.args.getlist("typ")
    visa_deadline = not typer or "upphandling" in typer
    visa_utgang = not typer or "avtalsutgang" in typer
    aktiva = sum(1 for k in FILTERNYCKLAR if request.args.get(k))
    filterantal = {k: len([v for v in request.args.getlist(k) if v])
                   for k in FILTERNYCKLAR}
    filterantal["q"] = 1 if request.args.get("q", "").strip() else 0

    alla = hamta_signaler(db, request.args)
    sida = max(1, request.args.get("sida", 1, type=int))
    antal_sidor = max(1, -(-len(alla) // PER_SIDA))
    sida = min(sida, antal_sidor)
    bas_qs = urlencode([(k, v) for k, v in request.args.items(multi=True)
                        if k != "sida"])

    return render_template(
        "flode.html",
        signaler=alla[(sida - 1) * PER_SIDA: sida * PER_SIDA],
        inkopskoppling=_inkopskoppling(
            db, alla[(sida - 1) * PER_SIDA: sida * PER_SIDA]),
        totalt=len(alla), sida=sida, antal_sidor=antal_sidor,
        bas_qs=bas_qs,
        branscher=BRANSCHER, yrkeskategorier=YRKEN,
        kontotyper=KONTOTYPER, alla_lan=LAN, valda=request.args,
        visa_deadline=visa_deadline, visa_utgang=visa_utgang,
        deadline_etiketter=DEADLINEETIKETT, utgang_etiketter=UTGANGSETIKETT,
        filterantal=filterantal, har_filter=aktiva > 0,
        sparade=_sparade_signal_ids(db),
        querystring=request.query_string.decode(),
        fakta=dagens_fakta(db),
        mina_bevakningar=db.execute(
            "SELECT id, namn, params FROM bevakning WHERE anvandare_id=? "
            "ORDER BY namn", (g.anvandare["id"],)).fetchall(),
    )


ORG_PER_SIDA = 100

# Samma "aktuellt"-definitioner som organisationssidan använder, så att
# listans siffror alltid stämmer med det man ser när man klickar in.
# Räknarna byggs som EN aggregerad genomgång av signal respektive avtal
# (LEFT JOIN nedan) - inte som delfrågor per organisationsrad, vilket
# gjorde "visa alla" kvadratiskt långsam.
_ORG_JOIN = (
    "LEFT JOIN (SELECT organisation_id, COUNT(*) AS n FROM signal "
    "WHERE typ='upphandling' AND dublett_av IS NULL "
    "AND (deadline IS NULL OR deadline >= date('now')) "
    "GROUP BY organisation_id) u ON u.organisation_id = o.id "
    "LEFT JOIN (SELECT organisation_id, COUNT(*) AS n FROM avtal "
    "WHERE slutdatum IS NULL OR slutdatum >= date('now') "
    "GROUP BY organisation_id) av ON av.organisation_id = o.id "
    "LEFT JOIN (SELECT organisation_id, COUNT(*) AS n FROM signal "
    "WHERE typ='planerad' AND dublett_av IS NULL "
    "AND (deadline IS NULL OR deadline >= date('now')) "
    "GROUP BY organisation_id) pl ON pl.organisation_id = o.id "
    "LEFT JOIN (SELECT organisation_id, COUNT(*) AS n FROM inkopsmonster "
    "GROUP BY organisation_id) ik ON ik.organisation_id = o.id "
    "LEFT JOIN (SELECT organisation_id, varde FROM nyckeltal "
    "WHERE slag='N01951') inv ON inv.organisation_id = o.id")


def _organisations_sql(args):
    q = args.get("q", "").strip()
    kontotyper = args.getlist("kontotyp")
    lan = args.getlist("lan")
    endast = args.get("endast", "")

    sql = ("SELECT o.*, COALESCE(u.n, 0) AS ute, "
           "COALESCE(av.n, 0) AS utgangar, "
           "COALESCE(pl.n, 0) AS planerade, "
           "COALESCE(ik.n, 0) AS inkopsposter, inv.varde AS invanare "
           f"FROM organisation o {_ORG_JOIN} WHERE 1=1")
    params: list = []
    if q:
        sql += " AND (o.namn LIKE ? OR o.orgnr LIKE ?)"
        params += [f"%{q}%"] * 2
    if kontotyper:
        sql += f" AND o.kontotyp IN ({','.join('?' * len(kontotyper))})"
        params += kontotyper
    if lan:
        sql += f" AND o.lan IN ({','.join('?' * len(lan))})"
        params += lan
    if endast == "upphandlingar":
        sql += " AND COALESCE(u.n, 0) > 0"
    elif endast == "utgangar":
        sql += " AND COALESCE(av.n, 0) > 0"
    elif endast == "nagot":
        sql += " AND (COALESCE(u.n, 0) > 0 OR COALESCE(av.n, 0) > 0)"
    elif endast == "planerade":
        sql += " AND COALESCE(pl.n, 0) > 0"
    elif endast == "inkop":
        sql += " AND COALESCE(ik.n, 0) > 0"
    minst_inv = args.get("invanare", type=int)
    if minst_inv:
        sql += " AND inv.varde >= ?"
        params.append(minst_inv)
    sortera = args.get("sortera") or "namn"
    ordningar = {"namn": "o.namn", "upphandlingar": "ute DESC, o.namn",
                 "utgangar": "utgangar DESC, o.namn",
                 "invanare": "inv.varde IS NULL, inv.varde DESC, o.namn"}
    sql += " ORDER BY " + ordningar.get(sortera, "o.namn")
    return sql, params


@bp.route("/organisationer")
def organisationer():
    from urllib.parse import urlencode
    db = get_db()
    sokt = bool(request.args.get("sok"))
    organisationer, totalt, sida, antal_sidor, bas_qs = [], 0, 1, 1, ""
    if sokt:
        sql, params = _organisations_sql(request.args)
        alla = db.execute(sql, params).fetchall()
        totalt = len(alla)
        sida = max(1, request.args.get("sida", 1, type=int))
        antal_sidor = max(1, -(-totalt // ORG_PER_SIDA))
        sida = min(sida, antal_sidor)
        organisationer = alla[(sida - 1) * ORG_PER_SIDA:
                              sida * ORG_PER_SIDA]
        bas_qs = urlencode([(k, v) for k, v
                            in request.args.items(multi=True)
                            if k != "sida"])
    return render_template(
        "organisationer.html",
        organisationer=organisationer, sokt=sokt, totalt=totalt,
        sida=sida, antal_sidor=antal_sidor, bas_qs=bas_qs,
        kontotyper=KONTOTYPER, alla_lan=LAN, valda=request.args,
    )


@bp.route("/organisationer/export.pdf")
def organisationer_pdf():
    db = get_db()
    sql, params = _organisations_sql(request.args)
    rader = db.execute(sql, params).fetchall()[:2000]
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, _pdf_text("Organisationer - Consens"), ln=1)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, _pdf_text(f"{len(rader)} organisationer, uttag "
                       f"{date.today().isoformat()}"), ln=1)
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 9)
    for b, t in ((80, "Organisation"), (30, "Orgnr"), (30, "Lan"),
                 (25, "Upphandl."), (25, "Utgangar")):
        pdf.cell(b, 7, t, border=1)
    pdf.ln()
    pdf.set_font("Helvetica", "", 9)
    for r in rader:
        pdf.cell(80, 6, _pdf_text((r["namn"] or "")[:48]), border=1)
        pdf.cell(30, 6, _pdf_text(r["orgnr"] or ""), border=1)
        pdf.cell(30, 6, _pdf_text((r["lan"] or "")[:16]), border=1)
        pdf.cell(25, 6, str(r["ute"]), border=1, align="R")
        pdf.cell(25, 6, str(r["utgangar"]), border=1, align="R")
        pdf.ln()
    from flask import Response
    return Response(bytes(pdf.output()), mimetype="application/pdf",
                    headers={"Content-Disposition":
                             "attachment; filename=organisationer.pdf"})


@bp.route("/organisationer/<orgnr>")
def organisation(orgnr):
    db = get_db()
    org = db.execute("SELECT * FROM organisation WHERE orgnr=?",
                     (orgnr,)).fetchone()
    if not org:
        return render_template("404.html"), 404
    return _visa_organisation(db, org)


@bp.route("/organisationer/id/<int:oid>")
def organisation_id(oid):
    db = get_db()
    org = db.execute("SELECT * FROM organisation WHERE id=?",
                     (oid,)).fetchone()
    if not org:
        return render_template("404.html"), 404
    if org["orgnr"]:
        return redirect(url_for("flode.organisation", orgnr=org["orgnr"]))
    return _visa_organisation(db, org)


def _org_data(db, org):
    """Samma aktuellt-definitioner som listans räknare. Passerat/utgånget
    hamnar i egna listor så siffrorna stämmer överallt."""
    idag = date.today().isoformat()
    aktuella, tidigare = [], []
    for u in db.execute(
        "SELECT * FROM signal WHERE organisation_id=? AND typ='upphandling' "
        "AND dublett_av IS NULL ORDER BY deadline IS NULL, deadline", (org["id"],)).fetchall():
        u = dict(u)
        u["extra"] = putsa_extra(json.loads(u.get("detaljer") or "{}"))
        (aktuella if (u["deadline"] is None or u["deadline"] >= idag)
         else tidigare).append(u)
    utgangssignaler = {r["avtal_id"]: r["id"] for r in db.execute(
        "SELECT id, avtal_id FROM signal WHERE organisation_id=? "
        "AND typ='avtalsutgang' AND avtal_id IS NOT NULL",
        (org["id"],)).fetchall()}
    avtal_aktiva, avtal_utgangna = [], []
    for a in db.execute(
        "SELECT * FROM avtal WHERE organisation_id=? ORDER BY slutdatum",
        (org["id"],)
    ).fetchall():
        a = dict(a)
        a["man_kvar"] = man_kvar(a["slutdatum"])
        a["procent"] = loptid_procent(a["startdatum"], a["slutdatum"])
        a["extra"] = putsa_extra(json.loads(a.get("detaljer") or "{}"))
        (avtal_aktiva if (a["slutdatum"] is None or a["slutdatum"] >= idag)
         else avtal_utgangna).append(a)
    nyckelpersoner = db.execute(
        "SELECT * FROM nyckelperson WHERE organisation_id=? ORDER BY namn",
        (org["id"],)).fetchall()
    return dict(upphandlingar=aktuella, tidigare_upphandlingar=tidigare,
                avtal=avtal_aktiva, utgangna_avtal=avtal_utgangna,
                nyckelpersoner=nyckelpersoner,
                utgangssignaler=utgangssignaler,
                arshjul=_arshjul(avtal_aktiva))


def _arshjul(avtal: list, manader: int = 24) -> dict | None:
    """Avtalsutgångar fördelade över de kommande två åren.

    En lista med slutdatum säger inte när organisationen är i marknaden.
    En tidsaxel gör det: varje månad får en stapel med antal avtal och
    samlat värde, så man ser var topparna ligger och när det är tyst."""
    from datetime import date as _date
    idag = _date.today()
    start = idag.replace(day=1)
    rutor = []
    for i in range(manader):
        ar = start.year + (start.month - 1 + i) // 12
        man = (start.month - 1 + i) % 12 + 1
        rutor.append(dict(ar=ar, manad=man, nyckel=f"{ar}-{man:02d}",
                          antal=0, varde=0.0, avtal=[]))
    per_nyckel = {r["nyckel"]: r for r in rutor}
    for a in avtal:
        if not a.get("slutdatum"):
            continue
        ruta = per_nyckel.get(a["slutdatum"][:7])
        if ruta is None:
            continue
        ruta["antal"] += 1
        ruta["varde"] += a.get("varde") or 0
        if len(ruta["avtal"]) < 4:
            ruta["avtal"].append(a["titel"])
    if not any(r["antal"] for r in rutor):
        return None
    hogst = max(r["antal"] for r in rutor)
    for r in rutor:
        r["hojd"] = round(100 * r["antal"] / hogst) if hogst else 0
    return dict(rutor=rutor, hogst=hogst,
                totalt=sum(r["antal"] for r in rutor),
                varde=sum(r["varde"] for r in rutor))


def _inkopskoppling(db, signaler):
    """signal_id -> inköpssummering, för avtalsutgångar vars leverantör
    återfinns i organisationens öppna leverantörsreskontra. Matchar i
    första hand på leverantörens orgnr, i andra hand på normaliserat
    namn. Billig: rör bara organisationer som alls har inköpsdata."""
    import re

    from ..matchning import normalisera_foretagsnamn, normalisera_orgnr
    utg = [s for s in signaler if s["typ"] == "avtalsutgang"
           and (s["extern_id"] or "").startswith("utgang:")]
    if not utg:
        return {}
    med_data = {r[0] for r in db.execute(
        "SELECT DISTINCT organisation_id FROM inkopsmonster")}
    utg = [s for s in utg if s["organisation_id"] in med_data]
    cache = {}

    def orgdata(oid):
        if oid not in cache:
            per_namn, per_onr = {}, {}
            for r in db.execute(
                    "SELECT leverantor, leverantor_orgnr, "
                    "SUM(belopp) AS belopp, SUM(antal) AS antal, "
                    "MIN(ar) AS ar_min, MAX(ar) AS ar_max "
                    "FROM inkopsmonster WHERE organisation_id=? "
                    "GROUP BY leverantor", (oid,)):
                n = normalisera_foretagsnamn(r["leverantor"].lower())
                if n:
                    per_namn.setdefault(n, r)
                onr = normalisera_orgnr(r["leverantor_orgnr"])
                if onr:
                    per_onr.setdefault(onr, r)
            cache[oid] = (per_namn, per_onr)
        return cache[oid]

    ut = {}
    for sig in utg:
        avtal = db.execute(
            "SELECT leverantor, leverantor_orgnr FROM avtal "
            "WHERE extern_id=?", (sig["extern_id"][7:],)).fetchone()
        if not avtal:
            continue
        per_namn, per_onr = orgdata(sig["organisation_id"])
        traff = per_onr.get(normalisera_orgnr(avtal["leverantor_orgnr"]))
        if traff is None and avtal["leverantor"]:
            for namn in re.split(r"[,;]| och ", avtal["leverantor"]):
                n = normalisera_foretagsnamn(namn.strip().lower())
                if n and n in per_namn:
                    traff = per_namn[n]
                    break
        if traff is not None:
            ut[sig["id"]] = dict(
                leverantor=traff["leverantor"], belopp=traff["belopp"],
                antal=traff["antal"], ar_min=traff["ar_min"],
                ar_max=traff["ar_max"])
    return ut


def _inkopsmonster(db, org):
    """Toppleverantörer för organisationens senaste år med inköpsdata,
    eller None om öppna reskontradata saknas."""
    ar = db.execute(
        "SELECT MAX(ar) FROM inkopsmonster WHERE organisation_id=?",
        (org["id"],)).fetchone()[0]
    if not ar:
        return None
    rader = db.execute(
        "SELECT leverantor, SUM(belopp) AS belopp, SUM(antal) AS antal "
        "FROM inkopsmonster WHERE organisation_id=? AND ar=? "
        "GROUP BY leverantor ORDER BY SUM(belopp) DESC LIMIT 8",
        (org["id"], ar)).fetchall()
    tot = db.execute(
        "SELECT SUM(belopp), SUM(antal), SUM(antal_avtal), "
        "SUM(antal_grund), COUNT(DISTINCT leverantor) "
        "FROM inkopsmonster WHERE organisation_id=? AND ar=?",
        (org["id"], ar)).fetchone()
    return dict(ar=ar, rader=rader, belopp=tot[0] or 0,
                antal=tot[1] or 0,
                andel_avtal=round(100 * (tot[2] or 0) / tot[3])
                if tot[3] else None,
                antal_leverantorer=tot[4])


def _visa_organisation(db, org):
    d = _org_data(db, org)
    orgparam = org["orgnr"] or f"id:{org['id']}"
    bevakar = db.execute(
        "SELECT 1 FROM bevakning WHERE anvandare_id=? AND aktiv=1 "
        "AND params=?", (g.anvandare["id"], f"org={orgparam}")).fetchone()
    nyckeltal = db.execute(
        "SELECT etikett, ar, varde FROM nyckeltal "
        "WHERE organisation_id=? ORDER BY slag", (org["id"],)).fetchall()
    return render_template(
        "organisation.html", org=org, kontotyper=KONTOTYPER,
        sparade=_sparade_signal_ids(db),
        sparade_avtal=_sparade_avtal_ids(db),
        inkop=_inkopsmonster(db, org), nyckeltal=nyckeltal,
        orgparam=orgparam, bevakar=bool(bevakar), **d)


@bp.route("/organisationer/id/<int:oid>/rapport.docx")
def organisation_rapport(oid):
    """Köparprofilen som Word-dokument, samma mönster som leverantörs
    och branschrapporterna."""
    from flask import Response, abort

    from ..rapport import bygg_organisationsrapport
    resultat = bygg_organisationsrapport(get_db(), oid)
    if resultat is None:
        abort(404)
    data, filnamn = resultat
    return Response(
        data,
        mimetype=("application/vnd.openxmlformats-officedocument"
                  ".wordprocessingml.document"),
        headers={"Content-Disposition":
                 f'attachment; filename="{filnamn}"'})


@bp.route("/organisationer/id/<int:oid>/export")
def organisation_export(oid):
    from flask import Response
    db = get_db()
    org = db.execute("SELECT * FROM organisation WHERE id=?",
                     (oid,)).fetchone()
    if not org:
        return render_template("404.html"), 404
    d = _org_data(db, org)
    fmt = request.args.get("format", "xlsx")
    delar = request.args.get("del", "allt")
    block = []
    if delar in ("allt", "upphandlingar"):
        block.append(("Aktuella upphandlingar",
                      [("Titel", "Deadline", "Värde", "Källa")]
                      + [(u["titel"], u["deadline"] or "", u["varde"] or "",
                          u["kalla"]) for u in d["upphandlingar"]]))
    if delar in ("allt", "avtal"):
        block.append(("Avtal",
                      [("Titel", "Leverantör", "Start", "Slut", "Källa")]
                      + [(a["titel"], a["leverantor"] or "",
                          a["startdatum"] or "", a["slutdatum"] or "",
                          a["kalla"]) for a in d["avtal"]]))
    namn = (org["namn"] or "organisation").replace(" ", "-")[:40]
    if fmt == "pdf":
        from fpdf import FPDF
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, _pdf_text(org["namn"] or ""), ln=1)
        for rubrik, rader in block:
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(0, 8, _pdf_text(f"{rubrik} ({len(rader) - 1})"), ln=1)
            pdf.set_font("Helvetica", "", 8)
            for rad in rader[1:]:
                pdf.multi_cell(0, 5, _pdf_text(" | ".join(
                    str(c) for c in rad if c != "")))
        return Response(bytes(pdf.output()), mimetype="application/pdf",
                        headers={"Content-Disposition":
                                 f"attachment; filename={namn}.pdf"})
    import io
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for rubrik, rader in block:
        ws = wb.create_sheet(rubrik[:28])
        for rad in rader:
            ws.append(list(rad))
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(),
                    mimetype=("application/vnd.openxmlformats-officedocument"
                              ".spreadsheetml.sheet"),
                    headers={"Content-Disposition":
                             f"attachment; filename={namn}.xlsx"})


@bp.route("/avtal")
def avtalslista():
    db = get_db()
    q = request.args.get("q", "").strip()
    lev = request.args.get("leverantor", "").strip()
    yrken_valda = request.args.getlist("yrke")

    sql = ("SELECT a.*, o.namn AS org_namn, o.orgnr, o.lan AS org_lan, "
           "o.kontotyp FROM avtal a "
           "JOIN organisation o ON o.id=a.organisation_id WHERE 1=1")
    params: list = []
    if q:
        sql += " AND (a.titel LIKE ? OR o.namn LIKE ? OR a.leverantor LIKE ?)"
        params += [f"%{q}%"] * 3
    if lev:
        sql += " AND a.leverantor LIKE ?"
        params.append(f"%{lev}%")
    if yrken_valda:
        sql += (" AND a.id IN (SELECT avtal_id FROM avtal_yrke WHERE yrke "
                f"IN ({','.join('?' * len(yrken_valda))}))")
        params += yrken_valda
    sortera = request.args.get("sortera") or "utgang"
    # Aktiva avtal först i alla lägen - utgångna nedprioriteras i botten
    utg_sist = ("(a.slutdatum IS NOT NULL AND a.slutdatum < date('now')), "
                "a.slutdatum IS NULL, ")
    ordningar = {
        "utgang": utg_sist + "a.slutdatum ASC",
        "varde": utg_sist + "a.varde IS NULL, a.varde DESC",
        "sent": utg_sist + "a.slutdatum DESC",
        "nyast": utg_sist + "a.id DESC",
        "titel": utg_sist + "a.titel COLLATE NOCASE",
    }
    sql += " ORDER BY " + ordningar.get(sortera, ordningar["utgang"])

    avtal = []
    for a in db.execute(sql, params).fetchall():
        a = dict(a)
        a["man_kvar"] = man_kvar(a["slutdatum"])
        a["procent"] = loptid_procent(a["startdatum"], a["slutdatum"])
        avtal.append(a)
    from urllib.parse import urlencode
    sida = max(1, request.args.get("sida", 1, type=int))
    antal_sidor = max(1, -(-len(avtal) // ORG_PER_SIDA))
    sida = min(sida, antal_sidor)
    totalt_avtal = len(avtal)
    avtal = avtal[(sida - 1) * ORG_PER_SIDA: sida * ORG_PER_SIDA]
    avtal_qs = urlencode([(k, v) for k, v in request.args.items(multi=True)
                          if k != "sida"])

    return render_template(
        "avtal.html", avtal=avtal, kontotyper=KONTOTYPER,
        sida=sida, antal_sidor=antal_sidor, totalt_avtal=totalt_avtal,
        avtal_qs=avtal_qs,
        yrkeskategorier=YRKEN, valda=request.args,
        sparade_avtal=_sparade_avtal_ids(db),
    )


def _pdf_text(s) -> str:
    """fpdf2:s kärnfonter är latin-1 - typografiska tecken ersätts."""
    ers = {"–": "-", "—": "-", "”": '"', "“": '"', "’": "'", "…": "..."}
    ut = str(s or "")
    for a, b in ers.items():
        ut = ut.replace(a, b)
    return ut.encode("latin-1", "replace").decode("latin-1")


@bp.route("/signal/<int:signal_id>/pdf")
def signal_pdf(signal_id):
    from fpdf import FPDF
    db = get_db()
    s = db.execute(
        "SELECT s.*, o.namn AS org_namn, o.orgnr, o.lan AS org_lan, "
        "a.leverantor, a.leverantor_orgnr, a.antal_anbud, "
        "a.startdatum, a.slutdatum "
        "FROM signal s JOIN organisation o ON o.id=s.organisation_id "
        "LEFT JOIN avtal a ON a.id=s.avtal_id WHERE s.id=?",
        (signal_id,)).fetchone()
    if not s:
        return render_template("404.html"), 404
    s = dict(s)
    ref = f"C-{signal_id:06d}"
    ar_utgang = s["typ"] == "avtalsutgang"

    pdf = FPDF()
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(14, 17, 22)
    pdf.write(10, "consens")
    pdf.set_text_color(29, 95, 219)
    pdf.write(10, ".")
    pdf.ln(14)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(107, 114, 128)
    pdf.cell(0, 5, _pdf_text(
        f"{'Avtalsutgång' if ar_utgang else 'Upphandling'} · {ref} · "
        f"Källa: {KALLNAMN.get(s['kalla'], s['kalla'])} · "
        f"Upptäckt {s['upptackt']}"),
        new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(14, 17, 22)
    pdf.multi_cell(0, 7, _pdf_text(s["titel"]), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    def rad(etikett, varde):
        if varde in (None, ""):
            return
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(52, 6, _pdf_text(etikett))
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 6, _pdf_text(varde), new_x="LMARGIN", new_y="NEXT")

    rad("Organisation", s["org_namn"] + (f" ({s['orgnr']})" if s["orgnr"] else ""))
    rad("Län", s["lan"] or s["org_lan"])
    if ar_utgang:
        rad("Avtalet löper ut", s["deadline"])
        rad("Avtalsstart", s["startdatum"])
        rad("Nuvarande leverantör", (s["leverantor"] or "")
            + (f" ({s['leverantor_orgnr']})" if s["leverantor_orgnr"] else ""))
        if s["antal_anbud"] is not None:
            rad("Anbud i ursprunglig upphandling", str(s["antal_anbud"]))
    else:
        rad("Sista anbudsdag", s["deadline"])
    if s["varde"]:
        sorter = {"anbud": "vinnande anbud", "ramavtalstak": "ramavtalstak",
                  "uppskattning": "uppskattat värde"}
        rad("Värde", f"{s['varde']:,} SEK".replace(",", " ")
            + f" ({sorter.get(s['varde_sort'], s['varde_sort'] or '')})")
    rad("CPV", s["cpv"])
    yrken = [r["yrke"] for r in db.execute(
        "SELECT yrke FROM signal_yrke WHERE signal_id=?", (signal_id,))]
    rad("Tjänsteområden", ", ".join(yrken))
    try:
        extra = putsa_extra(json.loads(s.get("detaljer") or "{}"))
    except (TypeError, ValueError):
        extra = {}
    if not extra and s.get("avtal_id"):
        rad_a = db.execute("SELECT detaljer FROM avtal WHERE id=?",
                           (s["avtal_id"],)).fetchone()
        try:
            extra = putsa_extra(json.loads(
                (rad_a["detaljer"] if rad_a else None) or "{}"))
        except (TypeError, ValueError):
            extra = {}
    beskrivning = extra.pop("Beskrivning", None)
    if extra:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, "Uppgifter från annonsen", new_x="LMARGIN", new_y="NEXT")
        for k, v in extra.items():
            rad(k, str(v))
    if beskrivning:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 7, "Beskrivning", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 6, _pdf_text(beskrivning), new_x="LMARGIN", new_y="NEXT")
    elif s["sammanfattning"]:
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 6, _pdf_text(s["sammanfattning"]), new_x="LMARGIN", new_y="NEXT")
    if s["url"]:
        pdf.ln(3)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(29, 95, 219)
        pdf.cell(0, 5, _pdf_text("Annonsen hos källan: " + s["url"]),
                 link=s["url"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(-24)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(107, 114, 128)
    pdf.cell(0, 5, _pdf_text(f"Genererad från consens.se · {ref}"))

    import io as _io
    buf = _io.BytesIO(bytes(pdf.output()))
    return send_file(buf, as_attachment=True,
                     download_name=f"consens-{ref.lower()}.pdf",
                     mimetype="application/pdf")


@bp.route("/klick/<int:signal_id>")
def klick(signal_id):
    """Loggar klick för statistiken och skickar vidare till målsidan."""
    db = get_db()
    db.execute("INSERT INTO klick (anvandare_id, signal_id) VALUES (?,?)",
               (g.anvandare["id"], signal_id))
    db.commit()
    nasta = request.args.get("nasta", "")
    if not nasta.startswith("/"):
        nasta = url_for("flode.flodet")
    return redirect(nasta)


# ---------- Spara/avspara enskilda objekt ----------

def _tillbaka():
    nasta = request.form.get("nasta", "")
    if not nasta.startswith("/"):
        nasta = url_for("flode.flodet")
    return redirect(nasta)


@bp.route("/spara/signal/<int:signal_id>", methods=["POST"])
def spara_signal(signal_id):
    db = get_db()
    fanns = db.execute(
        "SELECT 1 FROM sparad_signal WHERE anvandare_id=? AND signal_id=?",
        (g.anvandare["id"], signal_id)).fetchone()
    if fanns:
        db.execute("DELETE FROM sparad_signal WHERE anvandare_id=? AND signal_id=?",
                   (g.anvandare["id"], signal_id))
    else:
        db.execute("INSERT INTO sparad_signal (anvandare_id, signal_id) VALUES (?,?)",
                   (g.anvandare["id"], signal_id))
    db.commit()
    return _tillbaka()


@bp.route("/spara/avtal/<int:avtal_id>", methods=["POST"])
def spara_avtal(avtal_id):
    db = get_db()
    fanns = db.execute(
        "SELECT 1 FROM sparad_avtal WHERE anvandare_id=? AND avtal_id=?",
        (g.anvandare["id"], avtal_id)).fetchone()
    if fanns:
        db.execute("DELETE FROM sparad_avtal WHERE anvandare_id=? AND avtal_id=?",
                   (g.anvandare["id"], avtal_id))
    else:
        db.execute("INSERT INTO sparad_avtal (anvandare_id, avtal_id) VALUES (?,?)",
                   (g.anvandare["id"], avtal_id))
    db.commit()
    return _tillbaka()


# ---------- Bevakningar ----------

@bp.route("/bevakningar")
def bevakningar():
    db = get_db()
    uid = g.anvandare["id"]
    bevakningar = [dict(b, chips=beskriv_params(b["params"]))
                   for b in db.execute(
        "SELECT * FROM bevakning WHERE anvandare_id=? ORDER BY namn",
        (uid,)).fetchall()]

    sparade_upphandlingar = db.execute(
        "SELECT s.*, o.namn AS org_namn, o.orgnr AS org_orgnr FROM sparad_signal sp "
        "JOIN signal s ON s.id=sp.signal_id "
        "JOIN organisation o ON o.id=s.organisation_id "
        "WHERE sp.anvandare_id=? AND s.typ='upphandling' ORDER BY s.deadline",
        (uid,)).fetchall()
    sparade_utgangar = db.execute(
        "SELECT s.*, o.namn AS org_namn FROM sparad_signal sp "
        "JOIN signal s ON s.id=sp.signal_id "
        "JOIN organisation o ON o.id=s.organisation_id "
        "WHERE sp.anvandare_id=? AND s.typ='avtalsutgang' ORDER BY s.deadline",
        (uid,)).fetchall()
    sparade_avtal = db.execute(
        "SELECT a.*, o.namn AS org_namn FROM sparad_avtal sp "
        "JOIN avtal a ON a.id=sp.avtal_id "
        "JOIN organisation o ON o.id=a.organisation_id "
        "WHERE sp.anvandare_id=? ORDER BY a.slutdatum",
        (uid,)).fetchall()

    listor = []
    for l in _mina_listor(db):
        poster = db.execute(
            "SELECT lp.id AS post_id, s.titel AS s_titel, s.typ AS s_typ, "
            "a.titel AS a_titel, "
            "COALESCE(so.namn, ao.namn) AS org_namn "
            "FROM lista_post lp "
            "LEFT JOIN signal s ON s.id=lp.signal_id "
            "LEFT JOIN organisation so ON so.id=s.organisation_id "
            "LEFT JOIN avtal a ON a.id=lp.avtal_id "
            "LEFT JOIN organisation ao ON ao.id=a.organisation_id "
            "WHERE lp.lista_id=? ORDER BY lp.id", (l["id"],)).fetchall()
        listor.append({"lista": l, "poster": poster})

    bevakade_leverantorer = db.execute(
        "SELECT * FROM bevakad_leverantor WHERE anvandare_id=? "
        "ORDER BY visningsnamn", (g.anvandare["id"],)).fetchall()
    return render_template(
        "bevakningar.html", bevakningar=bevakningar,
        bevakade_leverantorer=bevakade_leverantorer,
        sparade_upphandlingar=sparade_upphandlingar,
        sparade_utgangar=sparade_utgangar, sparade_avtal=sparade_avtal,
        listor=listor,
    )


@bp.route("/bevakningar/spara-filter", methods=["POST"])
def bevakning_spara():
    namn = request.form.get("namn", "").strip() or "Namnlös bevakning"
    params = request.form.get("params", "")
    db = get_db()
    db.execute("INSERT INTO bevakning (anvandare_id, namn, params) VALUES (?,?,?)",
               (g.anvandare["id"], namn, params))
    db.commit()
    return redirect(url_for("flode.bevakningar"))


@bp.route("/bevakningar/<int:bid>/andra", methods=["POST"])
def bevakning_andra(bid):
    db = get_db()
    b = db.execute("SELECT * FROM bevakning WHERE id=? AND anvandare_id=?",
                   (bid, g.anvandare["id"])).fetchone()
    if b:
        atgard = request.form.get("atgard")
        if atgard == "ta-bort":
            db.execute("DELETE FROM bevakning WHERE id=?", (bid,))
        elif atgard == "vaxla":
            db.execute("UPDATE bevakning SET aktiv = 1 - aktiv WHERE id=?", (bid,))
        elif atgard in ("daglig", "veckovis"):
            db.execute("UPDATE bevakning SET frekvens=? WHERE id=?", (atgard, bid))
        db.commit()
    return redirect(url_for("flode.bevakningar"))


# ---------- Listor ----------

@bp.route("/listor/skapa", methods=["POST"])
def lista_skapa():
    namn = request.form.get("namn", "").strip()
    if namn:
        db = get_db()
        db.execute("INSERT INTO lista (anvandare_id, namn) VALUES (?,?)",
                   (g.anvandare["id"], namn))
        db.commit()
    return redirect(url_for("flode.bevakningar"))


@bp.route("/listor/<int:lid>/lagg-till", methods=["POST"])
def lista_lagg_till(lid):
    db = get_db()
    l = db.execute("SELECT * FROM lista WHERE id=? AND anvandare_id=?",
                   (lid, g.anvandare["id"])).fetchone()
    if l:
        signal_id = request.form.get("signal_id", type=int)
        avtal_id = request.form.get("avtal_id", type=int)
        if signal_id or avtal_id:
            db.execute("INSERT INTO lista_post (lista_id, signal_id, avtal_id) "
                       "VALUES (?,?,?)", (lid, signal_id, avtal_id))
            db.commit()
    return redirect(url_for("flode.bevakningar"))


@bp.route("/listor/<int:lid>/andra", methods=["POST"])
def lista_andra(lid):
    db = get_db()
    l = db.execute("SELECT * FROM lista WHERE id=? AND anvandare_id=?",
                   (lid, g.anvandare["id"])).fetchone()
    if l:
        atgard = request.form.get("atgard")
        if atgard == "ta-bort":
            db.execute("DELETE FROM lista WHERE id=?", (lid,))
        elif atgard == "ta-bort-post":
            db.execute("DELETE FROM lista_post WHERE id=? AND lista_id=?",
                       (request.form.get("post_id", type=int), lid))
        db.commit()
    return redirect(url_for("flode.bevakningar"))


@bp.route("/listor/<int:lid>/export.xlsx")
def lista_export(lid):
    from openpyxl import Workbook
    db = get_db()
    l = db.execute("SELECT * FROM lista WHERE id=? AND anvandare_id=?",
                   (lid, g.anvandare["id"])).fetchone()
    if not l:
        return render_template("404.html"), 404

    poster = db.execute(
        "SELECT s.typ AS s_typ, s.titel AS s_titel, s.varde AS s_varde, "
        "s.varde_sort AS s_sort, s.deadline AS s_deadline, s.kalla AS s_kalla, "
        "a.titel AS a_titel, a.leverantor, a.varde AS a_varde, "
        "a.varde_sort AS a_sort, a.slutdatum, a.antal_anbud, a.kalla AS a_kalla, "
        "COALESCE(so.namn, ao.namn) AS org_namn, "
        "COALESCE(so.orgnr, ao.orgnr) AS orgnr "
        "FROM lista_post lp "
        "LEFT JOIN signal s ON s.id=lp.signal_id "
        "LEFT JOIN organisation so ON so.id=s.organisation_id "
        "LEFT JOIN avtal a ON a.id=lp.avtal_id "
        "LEFT JOIN organisation ao ON ao.id=a.organisation_id "
        "WHERE lp.lista_id=? ORDER BY lp.id", (lid,)).fetchall()

    wb = Workbook()
    ark = wb.active
    ark.title = "Consens"
    ark.append(["Typ", "Titel", "Organisation", "Orgnr", "Leverantör",
                "Värde (kr)", "Värdets sort", "Slutdatum/Deadline",
                "Antal anbud", "Källa"])
    typnamn = {"upphandling": "Upphandling", "avtalsutgang": "Avtalsutgång"}
    for p in poster:
        if p["s_titel"]:
            ark.append([typnamn.get(p["s_typ"], p["s_typ"]), p["s_titel"],
                        p["org_namn"], p["orgnr"], None, p["s_varde"],
                        p["s_sort"], p["s_deadline"], None, p["s_kalla"]])
        else:
            ark.append(["Avtal", p["a_titel"], p["org_namn"], p["orgnr"],
                        p["leverantor"], p["a_varde"], p["a_sort"],
                        p["slutdatum"], p["antal_anbud"], p["a_kalla"]])
    for kol, bredd in zip("ABCDEFGHIJ", (13, 46, 30, 14, 26, 14, 14, 18, 11, 22)):
        ark.column_dimensions[kol].width = bredd

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    from ..vyer.publikt import slugga
    return send_file(
        buf, as_attachment=True,
        download_name=f"consens-{slugga(l['namn'])}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument"
                 ".spreadsheetml.sheet")
