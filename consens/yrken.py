"""Yrkestaxonomi och taggning.

Yrke svarar på VILKEN KOMPETENS som köps — tvärs genom branscherna
(en projektledare upphandlas inom bygg, IT och vård). Bransch (cpv.py)
svarar på VAD avtalet handlar om. Två oberoende dimensioner.

Varje yrke definieras som CPV-prefix (där koder finns) + nyckelord som
matchas mot TITELN (inte hela beskrivningen — Nodpunkt-lärdomen om
relevansbedömning: fritextmatchning ska vara snäv och granskningsbar).

Skyddsregel: nyckelordstaggning kräver att signalens CPV ligger i
tjänstespannet (division 50–98) eller saknas. Det stoppar klassikern
"utbildning" som träffar nybyggnation av utbildningslokaler (CPV 45).
CPV-baserad taggning (yrkets egna koder) gäller alltid.

Ändrat i taxonomin? Kör `flask tagga-om` så taggas allt befintligt om.
"""

# Kategori -> yrke -> {cpv: [prefix], ord: [gemena titelnyckelord]}
YRKEN = {
    "Ledning och styrning": {
        "Projektledning": {"cpv": ["79421"], "ord": ["projektled"]},
        "Programledning": {"cpv": [], "ord": ["programled"]},
        "Portföljstyrning": {"cpv": [], "ord": ["portföljstyr", "portfolio management"]},
        "PMO-stöd": {"cpv": [], "ord": ["pmo"]},
        "Förändringsledning": {"cpv": [], "ord": ["förändringsled", "change management"]},
        "Projekträddning": {"cpv": [], "ord": ["project rescue", "projekträddning", "krisprojekt"]},
        "Managementkonsult": {"cpv": ["79411", "79410"], "ord": ["management consult", "managementkonsult", "ledningskonsult"]},
        "Chefsstöd": {"cpv": [], "ord": ["chefsstöd", "chefstöd", "ledningsstöd"]},
        "Interimschef": {"cpv": [], "ord": ["interimschef", "interim chef", "interimsled", "tillförordnad chef"]},
        "Verksamhetsutveckling": {"cpv": [], "ord": ["verksamhetsutveckl"]},
        "Strategirådgivning": {"cpv": [], "ord": ["strategirådgivning", "strategikonsult", "strategisk rådgivning"]},
        "Processutveckling": {"cpv": [], "ord": ["processutveckl", "processled", "lean"]},
        "Utredning och analys": {"cpv": ["79419"], "ord": ["utredningstjänst", "utredare", "utvärderingstjänst", "följeforskning"]},
        "Upphandlingsstöd": {"cpv": [], "ord": ["upphandlingsstöd", "upphandlingskonsult", "inköpskonsult", "inköpsstöd"]},
        "Ledningsgruppsutveckling": {"cpv": [], "ord": ["ledningsgruppsutveckl", "ledningsutveckl"]},
    },
    "IT och digitalisering": {
        "Systemutveckling": {"cpv": ["72212", "72230"], "ord": ["systemutveckl", "applikationsutveckl", "apputveckl", "mjukvaruutveckl"]},
        "Lösningsarkitektur": {"cpv": [], "ord": ["lösningsarkitekt", "solution architect"]},
        "Enterprisearkitektur": {"cpv": [], "ord": ["enterprisearkitekt", "enterprise architect", "verksamhetsarkitekt"]},
        "IT-projektledning": {"cpv": [], "ord": ["it-projektled"]},
        "Kravanalys": {"cpv": [], "ord": ["kravanaly", "kravled", "verksamhetsanalytiker", "business analyst"]},
        "Testledning": {"cpv": ["73000"], "ord": ["testled", "testare", "testautomatiser"]},
        "Systemförvaltning": {"cpv": ["72250", "72267"], "ord": ["systemförvalt", "applikationsförvalt"]},
        "Integrationsspecialist": {"cpv": [], "ord": ["integrationsspecialist", "systemintegrat", "integrationsplattform"]},
        "BI och dataanalys": {"cpv": [], "ord": ["business intelligence", "dataanaly", "datavisualiser", "beslutsstöd"]},
        "Data engineering": {"cpv": [], "ord": ["data engineer", "datalager", "data warehouse", "dataplattform"]},
        "AI och ML": {"cpv": [], "ord": ["artificiell intelligens", " ai-", " ai ", "maskininlärning", "machine learning", "språkmodell"]},
        "Informationssäkerhet": {"cpv": ["72800"], "ord": ["informationssäkerhet", "it-säkerhet", "cybersäkerhet", "ciso", "penetrationstest"]},
        "Dataskydd (GDPR)": {"cpv": [], "ord": ["dataskyddsombud", "dataskydd", "gdpr"]},
        "UX och UI": {"cpv": [], "ord": ["ux", "användarupplevelse", "interaktionsdesign", "tjänstedesign"]},
        "Agil coach": {"cpv": [], "ord": ["agil coach", "scrum master", "agila metoder"]},
        "IT-drift": {"cpv": ["72500", "72700", "72514"], "ord": ["it-drift", "driftstjänst", "infrastrukturtjänst"]},
        "Servicedesk": {"cpv": ["72600"], "ord": ["servicedesk", "helpdesk", "it-support", "användarstöd"]},
        "Webbutveckling": {"cpv": ["72413"], "ord": ["webbutveckl", "webbplats", "webbportal"]},
    },
    "Ekonomi och juridik": {
        "Redovisningskonsult": {"cpv": ["79211"], "ord": ["redovisningstjänst", "redovisningskonsult", "bokföringstjänst"]},
        "Revision": {"cpv": ["79212"], "ord": ["revisionstjänst", "revisorstjänst", "revision av", "lekmannarevis"]},
        "Controller": {"cpv": [], "ord": ["controller"]},
        "Interim ekonomichef": {"cpv": [], "ord": ["interim ekonomichef", "ekonomichef"]},
        "Juridisk rådgivning": {"cpv": ["79100", "79110", "79111"], "ord": ["juridisk", "advokattjänst", "juristtjänst"]},
        "Upphandlingsjurist": {"cpv": [], "ord": ["upphandlingsjurist"]},
        "Skatterådgivning": {"cpv": ["79221"], "ord": ["skatterådgivning", "skattekonsult"]},
        "Försäkringsförmedling": {"cpv": ["66518"], "ord": ["försäkringsförmedl", "försäkringsmäklar"]},
        "Finansiell rådgivning": {"cpv": ["66171"], "ord": ["finansiell rådgivning", "finansrådgivning", "kapitalförvalt"]},
        "Inkasso": {"cpv": ["79940"], "ord": ["inkasso"]},
    },
    "HR och kompetens": {
        "Rekryteringstjänster": {"cpv": ["79600", "79610"], "ord": ["rekryteringstjänst", "rekryteringsstöd", "executive search", "chefsrekryter"]},
        "Interim HR": {"cpv": [], "ord": ["interim hr", "hr-konsult", "hr-stöd"]},
        "Lönekonsult": {"cpv": ["79631"], "ord": ["lönetjänst", "löneadministra", "lönekonsult"]},
        "Företagshälsovård": {"cpv": ["85147"], "ord": ["företagshälsovård", "företagshälsa"]},
        "Omställningsstöd": {"cpv": [], "ord": ["omställningsstöd", "omställningstjänst", "outplacement"]},
        "Ledarskapsutveckling": {"cpv": ["80532"], "ord": ["ledarskapsutveckl", "ledarutveckl", "ledarskapsprogram", "ledarskapsutbildning"]},
        "Utbildning och kurs": {"cpv": ["80500", "80510", "80520", "80530"], "ord": ["utbildningstjänst", "utbildningsinsats", "kurser inom", "kompetensutveckl", "uppdragsutbildning", "utbildning inom", "utbildning i "]},
        "Coachning": {"cpv": [], "ord": ["coachning", "coachingtjänst", "karriärcoach"]},
        "Arbetsmiljökonsult": {"cpv": [], "ord": ["arbetsmiljökonsult", "arbetsmiljöutbildning", "arbetsmiljötjänst"]},
    },
    "Kommunikation och marknad": {
        "Kommunikatör": {"cpv": ["79416"], "ord": ["kommunikationstjänst", "kommunikationsstöd", "kommunikatör", "kommunikationsbyrå"]},
        "Grafisk formgivning": {"cpv": ["79822"], "ord": ["grafisk form", "grafisk design", "grafisk produktion"]},
        "Webbredaktör": {"cpv": [], "ord": ["webbredakt"]},
        "Innehållsproduktion": {"cpv": [], "ord": ["innehållsproduktion", "content", "copywrit", "skribent"]},
        "Film och foto": {"cpv": ["92111"], "ord": ["filmproduktion", "videoproduktion", "fototjänst", "fotograf"]},
        "Översättning": {"cpv": ["79530"], "ord": ["översättningstjänst", "översättning"]},
        "Tolkning": {"cpv": ["79540"], "ord": ["tolktjänst", "tolkförmedl", "tolkning"]},
        "PR-tjänster": {"cpv": ["79342"], "ord": ["pr-tjänst", "pr-byrå", "medierelation"]},
        "Marknadsundersökning": {"cpv": ["79310"], "ord": ["marknadsundersökning", "medborgarundersökning", "enkätundersökning", "attitydundersökning"]},
        "Eventproduktion": {"cpv": ["79952"], "ord": ["eventproduktion", "eventbyrå", "konferenstjänst", "mässtjänst"]},
        "Tryckeritjänster": {"cpv": ["79810"], "ord": ["tryckeritjänst", "trycksak", "tryckning av"]},
    },
    "Bygg, teknik och fastighet": {
        "Byggprojektledning": {"cpv": ["71541"], "ord": ["byggprojektled", "projektledning bygg"]},
        "Byggledning": {"cpv": ["71520"], "ord": ["byggled", "byggplatsuppfölj"]},
        "Projekteringsledning": {"cpv": [], "ord": ["projekteringsled"]},
        "Kontrollansvarig": {"cpv": [], "ord": ["kontrollansvarig"]},
        "Besiktning": {"cpv": ["71630"], "ord": ["besiktning", "entreprenadbesiktning"]},
        "Arkitekt": {"cpv": ["71200", "71220"], "ord": ["arkitekttjänst", "arkitektuppdrag", "arkitektkonsult"]},
        "Landskapsarkitekt": {"cpv": ["71420"], "ord": ["landskapsarkitekt"]},
        "Konstruktör": {"cpv": ["71312"], "ord": ["konstruktör", "byggnadskonstruktion", "konstruktionstjänst"]},
        "VVS-konsult": {"cpv": ["71321"], "ord": ["vvs-konsult", "vvs-projekt", "rörkonsult"]},
        "Elkonsult": {"cpv": ["71323"], "ord": ["elkonsult", "elprojektering", "elkraftkonsult"]},
        "Energikonsult": {"cpv": ["71314"], "ord": ["energikonsult", "energikartläggning", "energideklaration"]},
        "Brandkonsult": {"cpv": [], "ord": ["brandkonsult", "brandskyddsprojekt", "brandingenjör"]},
        "Akustiker": {"cpv": [], "ord": ["akustik"]},
        "Geotekniker": {"cpv": ["71332"], "ord": ["geoteknik", "markundersökning"]},
        "BIM-samordnare": {"cpv": [], "ord": ["bim"]},
        "Lantmäteri": {"cpv": ["71354", "71355"], "ord": ["lantmäteri", "mättjänst", "inmätning"]},
        "Fastighetsförvaltning": {"cpv": ["70330"], "ord": ["fastighetsförvalt", "förvaltningstjänst fastighet"]},
    },
    "Samhälle och miljö": {
        "Samhällsplanering": {"cpv": ["71410"], "ord": ["samhällsplaner", "detaljplan", "översiktsplan", "stadsplaner"]},
        "Trafikplanering": {"cpv": [], "ord": ["trafikplaner", "trafikutredning", "trafikanaly"]},
        "GIS-specialist": {"cpv": [], "ord": ["gis"]},
        "Miljökonsult": {"cpv": ["71313", "90710"], "ord": ["miljökonsult", "miljötjänst", "miljöteknisk"]},
        "Miljöutredning (MKB)": {"cpv": ["71313"], "ord": ["miljökonsekvens", "mkb", "miljöutredning", "naturvärdesinvent"]},
        "Hållbarhetsstrateg": {"cpv": [], "ord": ["hållbarhetsstrateg", "hållbarhetskonsult", "hållbarhetsredovis"]},
        "Klimatrådgivning": {"cpv": [], "ord": ["klimatrådgiv", "klimatanpassning", "klimatutredning", "klimatinsats"]},
        "Kulturmiljö och arkeologi": {"cpv": ["71351914"], "ord": ["arkeologi", "kulturmiljö", "byggnadsantikvarie"]},
        "Avfallsrådgivning": {"cpv": [], "ord": ["avfallsrådgiv", "avfallsutredning", "avfallsplan"]},
    },
    "Vård och omsorg": {
        "Hyrläkare": {"cpv": ["79625"], "ord": ["hyrläkare", "bemanning läkare", "läkarbemanning", "inhyrning av läkare"]},
        "Hyrsjuksköterska": {"cpv": ["79624"], "ord": ["hyrsjuksköterska", "sjuksköterskebemanning", "bemanning sjuksköterska"]},
        "Psykolog": {"cpv": ["85121270"], "ord": ["psykolog"]},
        "Fysioterapeut": {"cpv": ["85142100"], "ord": ["fysioterap", "sjukgymnast"]},
        "Arbetsterapeut": {"cpv": [], "ord": ["arbetsterap"]},
        "Logoped": {"cpv": [], "ord": ["logoped"]},
        "Tandvårdspersonal": {"cpv": ["85130"], "ord": ["tandläkare", "tandhygienist", "tandvårdsbemanning"]},
        "Socionom": {"cpv": [], "ord": ["socionom", "socialsekreterar"]},
        "Behandlingspersonal": {"cpv": ["85311"], "ord": ["hvb", "behandlingshem", "behandlingsfamilj", "stödboende", "skyddat boende"]},
        "Personlig assistans": {"cpv": ["85312"], "ord": ["personlig assistans"]},
        "Elevhälsa": {"cpv": [], "ord": ["elevhälsa", "skolsköterska", "skolläkare", "skolkurator", "skolpsykolog"]},
    },
    "Utbildning (bemanning)": {
        "Lärarvikarier": {"cpv": [], "ord": ["lärarvikari", "vikarieförmedl", "lärarbemanning"]},
        "SFI-utbildning": {"cpv": ["80580"], "ord": ["sfi", "svenska för invandrare"]},
        "Yrkesutbildare": {"cpv": ["80530"], "ord": ["yrkesutbild", "yrkesförar", "yh-utbildning"]},
        "Studiehandledning": {"cpv": [], "ord": ["studiehandled", "modersmålsundervisning", "modersmålslärar"]},
    },
    "Säkerhet": {
        "Bevakningstjänster": {"cpv": ["79713", "79710"], "ord": ["bevakningstjänst", "väktartjänst", "ordningsvakt", "ronderande"]},
        "Säkerhetskonsult": {"cpv": [], "ord": ["säkerhetskonsult", "säkerhetsrådgiv"]},
        "Säkerhetsskyddsanalys": {"cpv": [], "ord": ["säkerhetsskydd"]},
        "Trygghetsvärdar": {"cpv": [], "ord": ["trygghetsvärd", "trygghetsskapande"]},
        "Larmtekniker": {"cpv": ["79711"], "ord": ["larmtjänst", "larmcentral", "larminstallation", "inbrottslarm"]},
        "Krisberedskap": {"cpv": [], "ord": ["krisberedskap", "kontinuitetsplan", "krisledning", "civilt försvar"]},
    },
    "Drift och service (bemanning)": {
        "Lokalvårdspersonal": {"cpv": ["90910", "90919"], "ord": ["lokalvård", "städtjänst", "städning av", "fönsterputs"]},
        "Måltidspersonal": {"cpv": ["55320", "55520"], "ord": ["måltidspersonal", "kockbemanning", "kökspersonal", "måltidstjänst"]},
        "Fastighetsskötsel": {"cpv": ["70333"], "ord": ["fastighetsskötsel", "fastighetsservice", "fastighetsdrift"]},
        "Vaktmästeri": {"cpv": [], "ord": ["vaktmästeri", "vaktmästartjänst"]},
        "Parkarbete": {"cpv": ["77310", "77313"], "ord": ["parkskötsel", "grönyteskötsel", "parkarbete", "trädvård"]},
        "Chaufförstjänster": {"cpv": ["60170"], "ord": ["chaufför", "förartjänst", "skolskjuts", "färdtjänst"]},
    },
}

# Nyckelordstaggning tillåts bara i tjänstespannet (division 50–98) eller
# när CPV saknas. Varor (03–44), bygg (45) och programvara (48) skyddas.
_TJANSTEDIVISIONER = range(50, 99)


def _ar_tjanst(cpv: str | None) -> bool:
    if not cpv or not cpv[:2].isdigit():
        return True  # saknad CPV: tillåt (titelmatchning är redan snäv)
    return int(cpv[:2]) in _TJANSTEDIVISIONER


def alla_yrken() -> dict:
    """Platt vy: yrke -> definition."""
    return {y: d for kat in YRKEN.values() for y, d in kat.items()}


def tagga_yrken(titel: str | None, cpv: str | None) -> list[str]:
    """Returnerar lista av yrken för en signal/ett avtal."""
    t = f" {(titel or '').lower()} "
    traffar = []
    tjanst = _ar_tjanst(cpv)
    for yrke, d in alla_yrken().items():
        if cpv and any(cpv.startswith(p) for p in d["cpv"]):
            traffar.append(yrke)
            continue
        if tjanst and any(o in t for o in d["ord"]):
            traffar.append(yrke)
    return traffar


def tagga_om_allt(db) -> dict:
    """Töm och tagga om signal_yrke + avtal_yrke. Skapar tabellerna vid behov
    (fungerar som migrering på befintliga databaser)."""
    db.executescript(
        "CREATE TABLE IF NOT EXISTS signal_yrke ("
        " signal_id INTEGER NOT NULL REFERENCES signal(id) ON DELETE CASCADE,"
        " yrke TEXT NOT NULL, PRIMARY KEY (signal_id, yrke));"
        "CREATE INDEX IF NOT EXISTS idx_signal_yrke ON signal_yrke(yrke);"
        "CREATE TABLE IF NOT EXISTS avtal_yrke ("
        " avtal_id INTEGER NOT NULL REFERENCES avtal(id) ON DELETE CASCADE,"
        " yrke TEXT NOT NULL, PRIMARY KEY (avtal_id, yrke));"
        "CREATE INDEX IF NOT EXISTS idx_avtal_yrke ON avtal_yrke(yrke);"
        "DELETE FROM signal_yrke; DELETE FROM avtal_yrke;"
    )
    ns = na = 0
    for s in db.execute("SELECT id, titel, cpv FROM signal").fetchall():
        for y in tagga_yrken(s["titel"], s["cpv"]):
            db.execute("INSERT OR IGNORE INTO signal_yrke VALUES (?,?)",
                       (s["id"], y))
            ns += 1
    for a in db.execute("SELECT id, titel, cpv FROM avtal").fetchall():
        for y in tagga_yrken(a["titel"], a["cpv"]):
            db.execute("INSERT OR IGNORE INTO avtal_yrke VALUES (?,?)",
                       (a["id"], y))
            na += 1
    db.commit()
    return {"signaltaggar": ns, "avtalstaggar": na}
