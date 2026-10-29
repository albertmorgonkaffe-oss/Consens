"""Kategorimappning för centralavtal utan CPV.

Inköpscentralernas kataloger (Adda, Avropa, HBV, Sinfra) publicerar inga
CPV-koder. Branschfiltret bygger helt på CPV-prefix, så centralavtalen
föll utanför Bransch/Tjänst-filtren. Lösningen: gissa en representativ
CPV-kod ur avtalstiteln med nyckelord. Koden behöver inte vara juridiskt
exakt - den ska bara landa i rätt bransch enligt cpv.BRANSCHER.

Regler: nyckelorden prövas i ordning (först vinner), matchning på
gemener och delsträng. Varje kod i tabellen MÅSTE ge en bransch via
bransch_for_cpv - testerna verifierar det.
"""

# (nyckelord-tupel, representativ CPV) - ordningen är prioritetsordning.
# Specifika ord före generella: "tvätt" måste slå innan "maskin".
NYCKELORD_CPV = (
    # Energi och VA (09/65/31)
    (("fjärrvärme", "fjärrkyla", "värmeverk", "kraftvärme", "panna",
      "pannor", "biobränsle", "förnybara bränslen", "drivmedel", "bränsle",
      "flygaska", "energi"), "09300000"),
    (("elnät", "elmätare", "transformator", "högspänning", "lågspänning",
      "ställverk", "reservkraft", "laddstation", "frekvensomriktare",
      "belysning", "elmateri", "kabel", "fiber"), "31600000"),
    (("vatten", "avlopp", "va-", " va ", "reningsverk", "slam", "brunn",
      "pump", "ledningsnät", "ledningsunderhåll", "rörnät", "dricksvatten",
      "nödvatten", "flödesmätare", "vattenmätare", "kalk"), "65110000"),
    # Avfall och återvinning (905)
    (("avfall", "återvinning", "sopor", "renhållning", "slamtömning",
      "container", "avfallskärl", "komprimator", "balpress"), "90500000"),
    # Städ och FM (909/907)
    (("städ", "rengöring", "fönsterputs", "sanering", "sotning",
      "cirkulationstvätt", "tvätteri", "textilservice", "arbetskläder",
      "entrémattor", "mattor", "avfuktning", "skadedjur", "lokalvård",
      "storköksutrustning"), "90910000"),
    # Fastighet och drift (70/50700) - vitvaror/tvättstugor hör hemma här
    (("tvättstuge", "tvättmaskin", "vitvaror", "torktumlare", "torkskåp",
      "hiss", "fastighetsdrift", "fastighetsförvaltning", "fastighetsnära",
      "lås", "passagesystem", "dörr"), "50700000"),
    (("fastighet", "lokalvård"), "70000000"),
    # IT och telekom (48/72/302/325)
    (("it-tjänst", "it-drift", "it-konsult", "programvara", "licens",
      "molntjänst", "digital", "system", "e-tjänst", "webb"), "72000000"),
    (("dator", "surfplatt", "skrivare", "skärm", "it-produkt",
      "av-produkt", "av-utrustning"), "30200000"),
    (("telefoni", "mobiltelefon", "växel", "kommunikation"), "32500000"),
    # Medicinteknik och läkemedel (33)
    (("läkemedel", "medicin", "sjukvårdsmaterial", "förbrukningsartiklar",
      "hjälpmedel", "inkontinens", "hygienprodukt", "handskar"),
     "33000000"),
    # Livsmedel och måltid (15/55)
    (("livsmedel", "mejeri", "kött", "frukt", "grönsak", "dryck",
      "kaffe", "bröd", "bageri", "fisk", "glass", "specialkost",
      "torrvaror", "chark", "charkuteri"), "15000000"),
    (("måltid", "catering", "restaurang", "kost", "hotell", "konferens"),
     "55000000"),
    # Kontor och inredning (301/391/228/798)
    (("möbler", "inredning"), "39100000"),
    (("kontorsmaterial", "kontorspapper", "papper och plast"), "30190000"),
    (("trycksak", "tryckeri", "profilprodukt"), "22800000"),
    # Transport och fordon (34/60)
    (("fordon", "personbil", "lastbil", "buss", "släpvagn", "däck",
      "fordonsvåg", "arbetsmaskin"), "34100000"),
    (("transport", "flyttjänst", "budtjänst", "persontransport",
      "skolskjuts", "färdtjänst"), "60000000"),
    # Säkerhet och bevakning (797)
    (("bevakning", "väktare", "larmcentral", "säkerhetstjänst",
      "områdessäkerhet", "trygghetslarm"), "79710000"),
    # Bygg och anläggning (45) - inkl VVS, schakt, entreprenad, maskinhyra
    (("bygg", "entreprenad", "anläggning", "asfalt", "beläggning", "mark",
      "schakt", "gräv", "vvs", "ventilation", "rör och böjar", "målning",
      "tak", "golv", "hyra av maskin", "lagerhall", "betong",
      "sprängning", "bergsprängning", "fasad", "fönsterbyte", "fönster",
      "rivning", "murverk", "murning", "stomme", "grundläggning",
      "plåtslageri", "snickeri", "byggnation", "ombyggnad", "nybyggnad",
      "tillbyggnad", "renovering", "isolering", "eldfast", "infodring",
      "brandskydd", "dränering", "trapphus", "putsning"), "45000000"),
    # Transport och fordon: kommunernas vardagsord
    (("bilpool", "poolbil", "fordonsleasing", "personbil", "miljöbil",
      "lastbil", "skolskjuts", "taxi", "färdtjänst", "bussgods",
      "fordonstvätt", "däck", "reservdel", "verkstadstjänst",
      "transporttjänst", "flyttjänst", "budtjänst"), "34100000"),
    # Kontor, inredning och förbrukning
    (("blommor", "växter", "kontorsmaterial", "möbler", "inredning",
      "skyltar", "beredskapsmaterial", "profilprodukter", "trycksaker",
      "kopieringspapper", "emballage"), "39100000"),
    # Konsulttjänster: utredningar och projektering
    (("utredning", "akustik", "buller", "miljökonsult", "projektering",
      "arkitekt", "besiktning", "kontrollansvarig", "geoteknik",
      "trafikutredning", "analysstöd", "revision", "juridiska tjänster",
      "advokat", "rekrytering", "löneväxling", "administration av"),
     "71300000"),
    # Park och grönyta (77)
    (("park", "grönyte", "trädgård", "skog", "plantering",
      "naturvård"), "77000000"),
    # Konsulttjänster (71/73/794)
    (("teknikkonsult", "miljökonsult", "arkitekt", "projektering",
      "besiktning", "geoteknik", "termografering", "materialprovning",
      "analystjänst", "laboratorie", "provning", "avfallskonsult",
      "konsult"), "71300000"),
    (("bemanning", "rekrytering", "managementkonsult", "revision",
      "redovisning", "juridisk", "översätt", "kommunikationstjänst",
      "mediebyrå"), "79400000"),
    # Finans och försäkring (66)
    (("försäkring", "bank", "betaltjänst", "finansiell", "pension",
      "inkasso"), "66000000"),
    # Utbildning (80)
    (("utbildning", "kurs", "fortbildning", "körkort"), "80000000"),
    # Vård och omsorg (85)
    (("vård", "omsorg", "boende", "hemtjänst", "tandvård",
      "företagshälso", "behandlingshem", "hvb", "lss", "missbruk",
      "familjehem", "korttidsvistelse", "personlig assistans",
      "socialpsykiatri", "elevhälsa", "psykolog", "läkarinsats",
      "förstadagsintyg", "rehabilitering", "tolkförmedling"),
     "85000000"),
    # Kultur och fritid (92)
    (("idrott", "lekplats", "gym", "bibliotek", "kultur"), "92000000"),
)


def gissa_cpv(titel: str | None) -> str | None:
    """Representativ CPV-kod ur en avtalstitel, eller None."""
    if not titel:
        return None
    t = titel.lower()
    for ord_, cpv in NYCKELORD_CPV:
        if any(o in t for o in ord_):
            return cpv
    return None


def backfylla_signalkategorier(db) -> tuple[int, int]:
    """Sätt gissad CPV och yrkestaggar på signaler som saknar dem.
    Gäller planer, budgetar och protokoll, vars källor inte publicerar
    CPV. Returnerar (antal cpv satta, antal yrkestaggar)."""
    from .yrken import tagga_yrken
    cpv_satta = yrken_satta = 0
    rader = db.execute(
        "SELECT id, titel, cpv FROM signal WHERE cpv IS NULL").fetchall()
    for rad in rader:
        gissad = gissa_cpv(rad["titel"])
        if not gissad:
            continue
        db.execute("UPDATE signal SET cpv=? WHERE id=?",
                   (gissad, rad["id"]))
        cpv_satta += 1
        if cpv_satta % 50 == 0:   # släpp skrivlåset regelbundet
            db.commit()
    db.commit()
    # Yrkestaggar för signaler som saknar dem helt
    utan_yrke = db.execute(
        "SELECT s.id, s.titel, s.cpv FROM signal s "
        "LEFT JOIN signal_yrke sy ON sy.signal_id = s.id "
        "WHERE sy.signal_id IS NULL").fetchall()
    for rad in utan_yrke:
        for yrke in tagga_yrken(rad["titel"], rad["cpv"]):
            db.execute("INSERT OR IGNORE INTO signal_yrke (signal_id, "
                       "yrke) VALUES (?,?)", (rad["id"], yrke))
            yrken_satta += 1
            if yrken_satta % 50 == 0:
                db.commit()
    db.commit()
    return cpv_satta, yrken_satta


def backfylla_kategorier(db) -> int:
    """Sätt gissad CPV på befintliga centralavtal och deras
    utgångssignaler. Idempotent - rör bara rader utan CPV. Returnerar
    antal uppdaterade avtal."""
    from .yrken import tagga_yrken
    antal = 0
    rader = db.execute(
        "SELECT id, extern_id, titel FROM avtal "
        "WHERE (cpv IS NULL OR cpv = '') "
        "AND kalla IN ('adda', 'avropa', 'hbv', 'sinfra')").fetchall()
    for r in rader:
        cpv = gissa_cpv(r["titel"])
        if not cpv:
            continue
        db.execute("UPDATE avtal SET cpv=? WHERE id=?", (cpv, r["id"]))
        db.execute(
            "UPDATE signal SET cpv=? WHERE extern_id=? "
            "AND (cpv IS NULL OR cpv='')",
            (cpv, f"utgang:{r['extern_id']}"))
        for yrke in tagga_yrken(r["titel"], cpv):
            db.execute("INSERT OR IGNORE INTO avtal_yrke VALUES (?, ?)",
                       (r["id"], yrke))
        antal += 1
    if antal:
        db.commit()
    return antal


# ---------- Förmedlingsavtal (konsultmäklare) ----------
#
# När en organisation upphandlar en mäklare i stället för konsulterna
# själva flyttas vägen in: leverantören når kunden genom mäklarens
# nätverk, inte direkt. Det är avgörande att veta för den som säljer
# konsulttjänster, och det syns inte i annonsen om man inte känner till
# mekanismen. Ordlistan är medvetet snäv: fastighetsmäklare,
# tolkförmedling och betalningsförmedling ska inte fastna.

_FORMEDLING_DIREKT = (
    "konsultmäklare", "konsultmäklartjänst", "konsultförmedling",
    "resurskonsultförmedling", "mäklartjänst för konsult",
)
_FORMEDLING_MEKANISM = ("mäklare", "mäklartjänst", "förmedlingstjänst",
                        "förmedling av", "resursförmedling")
_FORMEDLING_OMRADE = ("konsult", "bemanning", "resurs", "specialist",
                      "ingenjör", "utredning")
_FORMEDLING_UNDANTAG = ("tolk", "annons", "betalning", "bostad",
                        "fastighetsmäklare", "försäkring", "hyres",
                        "energimäklare", "frakt", "resebyrå")


def ar_formedlingsavtal(titel: str | None) -> bool:
    """Är detta ett avtal där en mäklare står mellan köpare och
    utförare?"""
    if not titel:
        return False
    lag = titel.lower()
    if any(o in lag for o in _FORMEDLING_UNDANTAG):
        return False
    if any(o in lag for o in _FORMEDLING_DIREKT):
        return True
    return (any(o in lag for o in _FORMEDLING_MEKANISM)
            and any(o in lag for o in _FORMEDLING_OMRADE))
