"""SQLite-anslutning och CLI-kommandon (init-db, seed-demo)."""
import sqlite3

import click
from flask import current_app, g
from flask.cli import with_appcontext


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
        g.db.execute("PRAGMA busy_timeout = 30000")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    with current_app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode("utf8"))


@click.command("init-db")
def init_db_command():
    """Nollställ databasen och skapa tabellerna."""
    init_db()
    click.echo("Databasen initierad.")


@click.command("seed-demo")
def seed_demo_command():
    """Ladda demodata (exempelsignaler från mockuperna)."""
    from .ingest import demo
    from .ingest.bas import kor_kalla

    from .yrken import tagga_om_allt

    demo.ladda_organisationer(get_db())
    resultat = kor_kalla("demo", demo.hamta)
    taggar = tagga_om_allt(get_db())
    click.echo(f"Demodata laddad: {resultat} · yrkestaggar: {taggar}")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(seed_demo_command)
    app.cli.add_command(skapa_anvandare_command)
    app.cli.add_command(tagga_om_command)
    app.cli.add_command(migrera_command)
    app.cli.add_command(koppla_dubbletter_command)
    app.cli.add_command(hamta_inkop_command)
    app.cli.add_command(hamta_inkop_alla_command)
    app.cli.add_command(kategorisera_om_command)
    app.cli.add_command(berika_underlag_command)
    app.cli.add_command(sondera_meetingplus_command)
    app.cli.add_command(hamta_nyckeltal_command)
    app.cli.add_command(hamta_planer_command)
    app.cli.add_command(kor_kallor_command)
    app.cli.add_command(laga_ted_titlar_command)
    app.cli.add_command(importera_organisationer_command)
    app.cli.add_command(importera_nyckelpersoner_command)
    from .digest import skicka_digest_command
    app.cli.add_command(skicka_digest_command)


@click.command("skapa-anvandare")
@click.argument("epost")
@click.option("--namn", default=None)
@click.option("--admin", is_flag=True, help="Ge administratörsbehörighet.")
@click.option("--roll", default=None,
              type=click.Choice(["trial", "kompis", "anvandare", "admin"]))
@click.password_option("--losenord", confirmation_prompt=True)
def skapa_anvandare_command(epost, namn, admin, roll, losenord):
    """Skapa en användare: flask skapa-anvandare EPOST [--admin|--roll ROLL]"""
    from .vyer.auth import skapa_anvandare
    roll = roll or ("admin" if admin else "anvandare")
    skapa_anvandare(get_db(), epost, losenord, namn, roll)
    click.echo(f"Användare {epost} skapad med roll {roll}.")


@click.command("berika-underlag")
@click.option("--max", "max_antal", default=800, show_default=True,
              help="Hur många annonser som berikas i körningen.")
@with_appcontext
def berika_underlag_command(max_antal):
    """Hämta dokumentlänkar för aktuella TED-annonser som saknar dem."""
    from .ingest.ted import berika_underlag
    r = berika_underlag(get_db(), max_antal)
    click.echo(f"Prövade {r['prövade']}: {r['lankar']} nya underlagslänkar, "
               f"{r['adresser']} nya kontaktadresser, "
               f"{r['utan_lank']} utan nytt hos källan, {r['fel']} fel.")


@click.command("kategorisera-om")
@with_appcontext
def kategorisera_om_command():
    """Sätt bransch (via gissad CPV) och yrken på signaler som saknar
    dem. Körs efter att nya källtyper lagts till."""
    from .kategorier import backfylla_signalkategorier
    cpv, yrken = backfylla_signalkategorier(get_db())
    click.echo(f"{cpv} signaler fick bransch, {yrken} yrkestaggar satta.")


@click.command("hamta-inkop-alla")
@with_appcontext
def hamta_inkop_alla_command():
    """Kör alla kända reskontrakällor (halvårsvis via cron)."""
    from .ingest.reskontra import kor_alla
    for namn, status, info in kor_alla():
        click.echo(f"{namn}: {status} - {info}")


@click.command("sondera-meetingplus")
@with_appcontext
def sondera_meetingplus_command():
    """Leta nya MeetingPlus-portaler bland kommunernas subdomäner.
    Skriver träffar till tabellen meetingplus_instans, som
    plankällan läser tillsammans med de inbyggda instanserna."""
    import re as _re
    from concurrent.futures import ThreadPoolExecutor
    from .ingest.gemensamt import http_session
    from .ingest.planer import MEETINGPLUS

    def doman(namn, webbplats):
        if webbplats:
            m = _re.search(r"https?://(?:www\.)?([\w.-]+\.se)", webbplats)
            if m:
                return m.group(1)
        n = namn.lower()
        for skrap in (" kommun", "s kommun", " stad", "s stad", " stads"):
            n = n.replace(skrap, "")
        n = (n.replace("å", "a").replace("ä", "a").replace("ö", "o")
              .replace(" ", "").replace("-", ""))
        return f"{n}.se" if n.isalnum() else None

    def testa(par):
        namn, dom = par
        for prefix in ("meetingsplus", "meetingspublic", "moten",
                       "motesportal"):
            url = f"https://{prefix}.{dom}"
            try:
                r = http_session().get(url + "/overview", timeout=8)
                if r.status_code == 200 and "/committees/" in r.text:
                    namnder = len(set(_re.findall(
                        r"/committees/([^/\"?]+)/", r.text)))
                    return (namn, url, namnder)
            except Exception:
                pass
        return None

    db = get_db()
    kanda = {u for _, u in MEETINGPLUS}
    orger = []
    for r in db.execute("SELECT namn, webbplats FROM organisation "
                        "WHERE kontotyp IN ('kommun','region')"):
        d = doman(r["namn"], r["webbplats"])
        if d:
            orger.append((r["namn"], d))
    with ThreadPoolExecutor(max_workers=12) as pool:
        traffar = [t for t in pool.map(testa, orger) if t]
    nya = 0
    for namn, url, namnder in traffar:
        if url in kanda:
            continue
        db.execute(
            "INSERT OR IGNORE INTO meetingplus_instans "
            "(organisationsnamn, bas_url, namnder) VALUES (?,?,?)",
            (namn, url, namnder))
        nya += 1
        click.echo(f"NY: {namn} - {url} ({namnder} nämnder)")
    db.commit()
    click.echo(f"Sondering klar: {len(traffar)} instanser, {nya} nya.")


@click.command("hamta-inkop")
@click.option("--url", required=True,
              help="Adress till CSV/JSON enligt reskontra-specen")
@click.option("--kopare", default=None,
              help="Orgnr som antas när filen saknar kopare-kolumn "
                   "(t.ex. Umeås filer)")
@click.option("--ar", default=None, type=int,
              help="År som antas när filen saknar datumkolumn "
                   "(t.ex. Göteborgs månadsfiler)")
def hamta_inkop_command(url, kopare, ar):
    """Importera en kommuns öppna leverantörsreskontra till
    inköpsmönstret på organisationssidorna."""
    from .ingest.reskontra import importera_url
    r = importera_url(url, kopare_standard=kopare, ar_standard=ar)
    click.echo(f"{r['rader']} fakturarader -> {r['leverantorer']} "
               f"leverantörsposter för {r['organisationer']} "
               f"organisationer.")
    if r["okanda_kopare"]:
        click.echo("Okända köpar-orgnr (hoppade): "
                   + ", ".join(r["okanda_kopare"][:8]))


@click.command("hamta-planer")
def hamta_planer_command():
    """Hämta kommunala upphandlingsplaner (signaltypen planerad)."""
    from .ingest.planer import kor
    for r in kor():
        rad = (f"{r['kalla']}: {r['status']} - {r['nya']} nya, "
               f"{r['uppdaterade']} uppdaterade, "
               f"{r['dubbletter']} oförändrade")
        if r.get("fel"):
            rad += f" ({r['fel']})"
        click.echo(rad)


@click.command("hamta-nyckeltal")
def hamta_nyckeltal_command():
    """Hämta kommun/region-nyckeltal (invånare m.m.) från Kolada."""
    from .ingest.kolada import kor
    r = kor()
    click.echo(f"{r['matchade']} organisationer matchade mot Kolada, "
               f"{r['varden']} nyckeltal skrivna.")


@click.command("koppla-dubbletter")
def koppla_dubbletter_command():
    """Extrahera upphandlings-id ur detaljer och koppla dubbletter
    (samma organisation + samma referens, t.ex. TED + nationell annons)."""
    from .matchning import koppla_dubbletter
    r = koppla_dubbletter(get_db())
    click.echo(f"{r['taggade']} signaler fick upphandlings-id, "
               f"{r['kopplade']} dubbletter kopplade.")


@click.command("tagga-om")
def tagga_om_command():
    """Tagga om alla signaler och avtal med yrken (skapar tabeller vid behov).
    Kör efter ändringar i yrkestaxonomin — och som migrering vid uppgradering."""
    from .yrken import tagga_om_allt
    click.echo(f"Omtaggning klar: {tagga_om_allt(get_db())}")


MIGRERING_SQL = """
CREATE TABLE IF NOT EXISTS besok (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    sokvag   TEXT NOT NULL,
    tidpunkt TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_besok_tid ON besok(tidpunkt);

CREATE TABLE IF NOT EXISTS inbjudan (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    epost        TEXT NOT NULL,
    roll         TEXT NOT NULL DEFAULT 'anvandare',
    giltig_till  TEXT,
    token        TEXT NOT NULL UNIQUE,
    skapad_av    INTEGER REFERENCES anvandare(id),
    skapad       TEXT NOT NULL DEFAULT (datetime('now')),
    anvand       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS utskickslogg (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    anvandare_id INTEGER REFERENCES anvandare(id) ON DELETE CASCADE,
    epost        TEXT NOT NULL,
    typ          TEXT NOT NULL,
    amne         TEXT,
    skickad      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS losenordsaterstallning (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    anvandare_id INTEGER NOT NULL REFERENCES anvandare(id) ON DELETE CASCADE,
    token        TEXT NOT NULL UNIQUE,
    skapad       TEXT NOT NULL DEFAULT (datetime('now')),
    anvand       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bevakning (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    anvandare_id INTEGER NOT NULL REFERENCES anvandare(id) ON DELETE CASCADE,
    namn TEXT NOT NULL, params TEXT NOT NULL,
    frekvens TEXT NOT NULL DEFAULT 'daglig' CHECK (frekvens IN ('daglig','veckovis')),
    aktiv INTEGER NOT NULL DEFAULT 1, senast_kord TEXT,
    skapad TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS sparad_signal (
    anvandare_id INTEGER NOT NULL REFERENCES anvandare(id) ON DELETE CASCADE,
    signal_id INTEGER NOT NULL REFERENCES signal(id) ON DELETE CASCADE,
    skapad TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (anvandare_id, signal_id));
CREATE TABLE IF NOT EXISTS sparad_avtal (
    anvandare_id INTEGER NOT NULL REFERENCES anvandare(id) ON DELETE CASCADE,
    avtal_id INTEGER NOT NULL REFERENCES avtal(id) ON DELETE CASCADE,
    skapad TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (anvandare_id, avtal_id));
CREATE TABLE IF NOT EXISTS lista (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    anvandare_id INTEGER NOT NULL REFERENCES anvandare(id) ON DELETE CASCADE,
    namn TEXT NOT NULL, skapad TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS lista_post (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lista_id INTEGER NOT NULL REFERENCES lista(id) ON DELETE CASCADE,
    signal_id INTEGER REFERENCES signal(id) ON DELETE CASCADE,
    avtal_id INTEGER REFERENCES avtal(id) ON DELETE CASCADE,
    skapad TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((signal_id IS NULL) != (avtal_id IS NULL)));
CREATE TABLE IF NOT EXISTS nyckelperson (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id INTEGER NOT NULL REFERENCES organisation(id) ON DELETE CASCADE,
    namn TEXT NOT NULL, titel TEXT, epost TEXT, telefon TEXT,
    kalla TEXT, uppdaterad TEXT);
CREATE INDEX IF NOT EXISTS idx_nyckelperson_org ON nyckelperson(organisation_id);
CREATE TABLE IF NOT EXISTS klick (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    anvandare_id INTEGER NOT NULL REFERENCES anvandare(id) ON DELETE CASCADE,
    signal_id INTEGER NOT NULL REFERENCES signal(id) ON DELETE CASCADE,
    tidpunkt TEXT NOT NULL DEFAULT (datetime('now')));
CREATE INDEX IF NOT EXISTS idx_klick_signal ON klick(signal_id);
CREATE TABLE IF NOT EXISTS kontoforfragan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    namn TEXT NOT NULL, epost TEXT NOT NULL, foretag TEXT, meddelande TEXT,
    status TEXT NOT NULL DEFAULT 'ny' CHECK (status IN ('ny','hanterad','avvisad')),
    skapad TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS utskick (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    typ TEXT NOT NULL CHECK (typ IN ('marknadsforing','driftmeddelande')),
    amne TEXT NOT NULL, brodtext TEXT NOT NULL, mottagarfilter TEXT,
    antal_mottagare INTEGER NOT NULL DEFAULT 0,
    skickad_av INTEGER REFERENCES anvandare(id),
    skapad TEXT NOT NULL DEFAULT (datetime('now')));
"""


def _lagg_till_kolumn(db, tabell, kolumn, deftyp):
    finns = [r["name"] for r in
             db.execute(f"PRAGMA table_info({tabell})").fetchall()]
    if kolumn not in finns:
        db.execute(f"ALTER TABLE {tabell} ADD COLUMN {kolumn} {deftyp}")


@click.command("migrera")
def migrera_command():
    """Skapa nya tabeller/kolumner på en befintlig databas (raderar inget)."""
    db = get_db()
    db.executescript(MIGRERING_SQL)
    _lagg_till_kolumn(db, "organisation", "alias", "TEXT")
    _lagg_till_kolumn(db, "organisation", "epost", "TEXT")
    _lagg_till_kolumn(db, "nyckelperson", "rollgrupp", "TEXT")
    _lagg_till_kolumn(db, "nyckelperson", "harledd", "INTEGER DEFAULT 0")
    _lagg_till_kolumn(db, "nyckelperson", "kalla_lank", "TEXT")
    _lagg_till_kolumn(db, "signal", "korning_id", "INTEGER")
    _lagg_till_kolumn(db, "signal", "url", "TEXT")
    _lagg_till_kolumn(db, "signal", "publicerad", "TEXT")
    _lagg_till_kolumn(db, "signal", "detaljer", "TEXT")
    _lagg_till_kolumn(db, "avtal", "detaljer", "TEXT")
    _lagg_till_kolumn(db, "anvandare", "foretag", "TEXT")
    _lagg_till_kolumn(db, "anvandare", "giltig_till", "TEXT")
    _lagg_till_kolumn(db, "anvandare", "stripe_kund", "TEXT")
    _lagg_till_kolumn(db, "anvandare", "stripe_prenumeration", "TEXT")
    _lagg_till_kolumn(db, "anvandare", "stripe_trial_slut", "TEXT")
    _lagg_till_kolumn(db, "anvandare", "mork_lage", "INTEGER NOT NULL DEFAULT 0")
    _lagg_till_kolumn(db, "anvandare", "tur_visad", "INTEGER NOT NULL DEFAULT 1")
    # Signaltypen 'planerad' kräver ombyggd CHECK - SQLite kan inte ändra
    # den på plats, så tabellen byggs om en gång (radbevarande).
    ddl = db.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                     "AND name='signal'").fetchone()
    if ddl and "'planerad'" not in ddl[0]:
        db.executescript("""
PRAGMA foreign_keys=OFF;
CREATE TABLE signal_ny (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    typ              TEXT NOT NULL CHECK (typ IN ('upphandling','avtalsutgang','planerad')),
    organisation_id  INTEGER NOT NULL REFERENCES organisation(id),
    avtal_id         INTEGER REFERENCES avtal(id),
    titel            TEXT NOT NULL,
    sammanfattning   TEXT,
    varde            INTEGER,
    varde_sort       TEXT CHECK (varde_sort IN
                     ('anbud','ramavtalstak','uppskattning')),
    cpv              TEXT,
    lan              TEXT,
    kalla            TEXT NOT NULL,
    extern_id        TEXT NOT NULL UNIQUE,
    deadline         TEXT,
    prioritet        TEXT DEFAULT 'normal' CHECK (prioritet IN ('normal','hog')),
    korning_id       INTEGER REFERENCES korningshistorik(id),
    url              TEXT,
    publicerad       TEXT,
    detaljer         TEXT,
    upphandlings_id  TEXT,
    dublett_av       INTEGER REFERENCES signal(id),
    upptackt         TEXT NOT NULL DEFAULT (date('now'))
);
INSERT INTO signal_ny (id, typ, organisation_id, avtal_id, titel,
    sammanfattning, varde, varde_sort, cpv, lan, kalla, extern_id, deadline,
    prioritet, korning_id, url, publicerad, detaljer, upphandlings_id,
    dublett_av, upptackt)
  SELECT id, typ, organisation_id, avtal_id, titel, sammanfattning, varde,
    varde_sort, cpv, lan, kalla, extern_id, deadline, prioritet, korning_id,
    url, publicerad, detaljer, upphandlings_id, dublett_av, upptackt
  FROM signal;
DROP TABLE signal;
ALTER TABLE signal_ny RENAME TO signal;
CREATE INDEX idx_signal_typ ON signal(typ);
CREATE INDEX idx_signal_deadline ON signal(deadline);
PRAGMA foreign_keys=ON;
""")
    # inkopsmonster fick per-fil-nyckel innan första skarpa importen -
    # tabellen kan tryggt byggas om (aggregat, återskapas ur källfilerna).
    har_url = db.execute("SELECT COUNT(*) FROM pragma_table_info("
                         "'inkopsmonster') WHERE name='kalla_url'").fetchone()
    if har_url and har_url[0] == 0:
        db.executescript("DROP TABLE IF EXISTS inkopsmonster;")
    db.executescript(
        "CREATE TABLE IF NOT EXISTS inkopsmonster ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "organisation_id INTEGER NOT NULL REFERENCES organisation(id), "
        "leverantor TEXT NOT NULL, leverantor_orgnr TEXT, "
        "ar INTEGER NOT NULL, belopp REAL NOT NULL DEFAULT 0, "
        "antal INTEGER NOT NULL DEFAULT 0, "
        "antal_avtal INTEGER NOT NULL DEFAULT 0, "
        "antal_grund INTEGER NOT NULL DEFAULT 0, "
        "kalla_url TEXT NOT NULL DEFAULT '', "
        "hamtad TEXT NOT NULL DEFAULT (date('now')), "
        "UNIQUE (organisation_id, leverantor, ar, kalla_url)); "
        "CREATE INDEX IF NOT EXISTS idx_inkop_org_ar "
        "ON inkopsmonster(organisation_id, ar);")
    db.executescript(
        "CREATE INDEX IF NOT EXISTS idx_signal_org "
        "ON signal(organisation_id);"
        "CREATE INDEX IF NOT EXISTS idx_avtal_org "
        "ON avtal(organisation_id);")
    db.executescript(
        "CREATE TABLE IF NOT EXISTS bevakad_leverantor ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "anvandare_id INTEGER NOT NULL REFERENCES anvandare(id), "
        "visningsnamn TEXT NOT NULL, namn_norm TEXT NOT NULL, "
        "orgnr TEXT, slug TEXT NOT NULL, "
        "skapad TEXT NOT NULL DEFAULT (date('now')), "
        "UNIQUE (anvandare_id, namn_norm));"
        "CREATE TABLE IF NOT EXISTS meetingplus_instans ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "organisationsnamn TEXT NOT NULL, bas_url TEXT NOT NULL UNIQUE, "
        "namnder INTEGER DEFAULT 0, aktiv INTEGER NOT NULL DEFAULT 1, "
        "upptackt TEXT NOT NULL DEFAULT (date('now')));"
        "CREATE TABLE IF NOT EXISTS leverantorsnotis ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "anvandare_id INTEGER NOT NULL REFERENCES anvandare(id), "
        "nyckel TEXT NOT NULL, "
        "skickad TEXT NOT NULL DEFAULT (date('now')), "
        "UNIQUE (anvandare_id, nyckel));")
    db.executescript(
        "CREATE TABLE IF NOT EXISTS nyckeltal ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "organisation_id INTEGER NOT NULL REFERENCES organisation(id), "
        "slag TEXT NOT NULL, etikett TEXT NOT NULL, "
        "ar INTEGER NOT NULL, varde REAL NOT NULL, "
        "hamtad TEXT NOT NULL DEFAULT (date('now')), "
        "UNIQUE (organisation_id, slag));")
    _lagg_till_kolumn(db, "signal", "upphandlings_id", "TEXT")
    _lagg_till_kolumn(db, "signal", "dublett_av", "INTEGER REFERENCES signal(id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_signal_uppid "
               "ON signal(organisation_id, upphandlings_id)")
    db.execute("UPDATE anvandare SET roll='anvandare' WHERE roll='fri'")
    from .kategorier import backfylla_kategorier
    n = backfylla_kategorier(db)
    if n:
        click.echo(f"Kategorimappning: {n} centralavtal fick bransch.")
    from .matchning import koppla_dubbletter
    r = koppla_dubbletter(db)
    if r["taggade"] or r["kopplade"]:
        click.echo(f"Dubblettfacit: {r['taggade']} signaler fick "
                   f"upphandlings-id, {r['kopplade']} dubbletter kopplade.")
    _lagg_till_kolumn(db, "avtal", "url", "TEXT")
    _lagg_till_kolumn(db, "anvandare", "trial_start", "TEXT")
    _lagg_till_kolumn(db, "anvandare", "samtycke_marknadsforing",
                      "INTEGER NOT NULL DEFAULT 0")
    # Nya roller kräver ombyggd CHECK — SQLite kan inte ändra constraints.
    tabellsql = db.execute(
        "SELECT sql FROM sqlite_master WHERE name='anvandare'"
    ).fetchone()["sql"]
    if "'trial'" not in tabellsql:
        db.executescript("""
            CREATE TABLE anvandare_ny (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                epost TEXT NOT NULL UNIQUE, namn TEXT,
                losenord_hash TEXT NOT NULL,
                roll TEXT NOT NULL DEFAULT 'anvandare'
                    CHECK (roll IN ('trial','kompis','fri','anvandare','admin')),
                aktiv INTEGER NOT NULL DEFAULT 1,
                trial_start TEXT,
                samtycke_marknadsforing INTEGER NOT NULL DEFAULT 0,
                skapad TEXT NOT NULL DEFAULT (datetime('now')));
            INSERT INTO anvandare_ny (id, epost, namn, losenord_hash, roll,
                aktiv, trial_start, samtycke_marknadsforing, skapad)
              SELECT id, epost, namn, losenord_hash, roll, aktiv,
                trial_start, samtycke_marknadsforing, skapad FROM anvandare;
            DROP TABLE anvandare;
            ALTER TABLE anvandare_ny RENAME TO anvandare;
        """)
    for gammalt, nytt in (("Project rescue", "Projekträddning"),
                          ("Management consulting", "Managementkonsult")):
        for tabell, nyckel in (("signal_yrke", "signal_id"),
                               ("avtal_yrke", "avtal_id")):
            db.execute(f"UPDATE OR IGNORE {tabell} SET yrke=? WHERE yrke=?",
                       (nytt, gammalt))
            db.execute(f"DELETE FROM {tabell} WHERE yrke=?", (gammalt,))
    db.commit()
    from .yrken import tagga_om_allt
    tagga_om_allt(db)
    click.echo("Migrering klar.")


@click.command("importera-organisationer")
@click.argument("fil", type=click.Path(exists=True))
def importera_organisationer_command(fil):
    """Importera organisationer från CSV med kolumnerna
    namn;orgnr;kontotyp;lan;webbplats (semikolonseparerad, UTF-8).
    Befintliga orgnr uppdateras, nya läggs till."""
    import csv
    db = get_db()
    nya = uppdaterade = 0
    with open(fil, encoding="utf-8-sig", newline="") as f:
        for rad in csv.DictReader(f, delimiter=";"):
            orgnr = (rad.get("orgnr") or "").strip() or None
            rad["orgnr"] = orgnr
            if orgnr:
                fanns = db.execute("SELECT id FROM organisation WHERE orgnr=?",
                                   (orgnr,)).fetchone()
            else:
                fanns = db.execute(
                    "SELECT id FROM organisation WHERE orgnr IS NULL "
                    "AND namn=?", (rad["namn"],)).fetchone()
            if fanns:
                db.execute(
                    "UPDATE organisation SET namn=?, kontotyp=?, lan=?, "
                    "webbplats=?, alias=?, epost=?, orgnr=? WHERE id=?",
                    (rad["namn"], rad["kontotyp"], rad.get("lan") or None,
                     rad.get("webbplats") or None, rad.get("alias") or None,
                     rad.get("epost") or None, rad["orgnr"], fanns["id"]))
                uppdaterade += 1
            else:
                db.execute(
                    "INSERT INTO organisation (namn, orgnr, kontotyp, lan, "
                    "webbplats, alias, epost) VALUES (?,?,?,?,?,?,?)",
                    (rad["namn"], rad["orgnr"], rad["kontotyp"],
                     rad.get("lan") or None, rad.get("webbplats") or None,
                     rad.get("alias") or None, rad.get("epost") or None))
                nya += 1
    db.commit()
    click.echo(f"Organisationer: {nya} nya, {uppdaterade} uppdaterade.")


@click.command("importera-nyckelpersoner")
@click.argument("fil", type=click.Path(exists=True))
def importera_nyckelpersoner_command(fil):
    """Importera nyckelpersoner från CSV med kolumnerna
    orgnr;namn;titel;epost;telefon (semikolonseparerad, UTF-8).
    Ersätter organisationens befintliga personer vid omimport."""
    import csv
    db = get_db()
    antal = saknade = 0
    rensade: set = set()
    with open(fil, encoding="utf-8-sig", newline="") as f:
        for rad in csv.DictReader(f, delimiter=";"):
            orgnr = (rad.get("orgnr") or "").strip()
            org = None
            if orgnr:
                org = db.execute("SELECT id FROM organisation WHERE orgnr=?",
                                 (orgnr,)).fetchone()
            if not org and rad.get("organisation"):
                org = db.execute(
                    "SELECT id FROM organisation WHERE namn=? OR alias=?",
                    (rad["organisation"].strip(),
                     rad["organisation"].strip())).fetchone()
            if not org:
                saknade += 1
                continue
            if org["id"] not in rensade:
                db.execute("DELETE FROM nyckelperson WHERE organisation_id=?",
                           (org["id"],))
                rensade.add(org["id"])
            db.execute(
                "INSERT INTO nyckelperson (organisation_id, namn, titel, "
                "epost, telefon, rollgrupp, harledd, kalla, kalla_lank, "
                "uppdaterad) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (org["id"], rad["namn"], rad.get("titel") or None,
                 rad.get("epost") or None, rad.get("telefon") or None,
                 rad.get("rollgrupp") or None,
                 1 if rad.get("harledd", "").lower() in ("ja", "1") else 0,
                 rad.get("kalla") or "nodpunkt-import",
                 rad.get("kalla_lank") or None,
                 rad.get("verifierad") or None))
            antal += 1
    db.commit()
    click.echo(f"Nyckelpersoner: {antal} importerade, "
               f"{saknade} rader utan matchande orgnr.")


@click.command("kor-kallor")
@click.option("--torr", is_flag=True, help="Torrkörning - inget sparas.")
@click.option("--sedan", default=None,
              help="Backfill från datum (YYYYMMDD), gäller TED-källorna.")
@click.option("--max-sidor", "max_sidor", default=None, type=int,
              help="Höj sidtaket för TED/Kommers vid backfill.")
@click.option("--bara", default=None,
              type=click.Choice(["ted", "ted_tilldelningar", "kommers",
                                 "e_avrop", "adda", "avropa",
                                 "hbv", "sinfra", "avtalsutgangar"]),
              help="Kör bara en källa.")
def kor_kallor_command(torr, sedan, max_sidor, bara):
    """Kör alla källor i ordning med felisolering. Daglig cron 06:30."""
    from .ingest import (adda, avropa, avtalsutgangar, e_avrop, hbv,
                         kommers, sinfra, ted, ted_tilldelningar)

    kallor = [
        ("ted", lambda: ted.kor(torr=torr, sedan=sedan, max_sidor=max_sidor)),
        ("ted_tilldelningar",
         lambda: ted_tilldelningar.kor(torr=torr, sedan=sedan,
                                       max_sidor=max_sidor)),
        # Vid backfill (--sedan) stängs stopp-vid-känt av så äldre sidor nås.
        ("kommers", lambda: kommers.kor(torr=torr, max_sidor=max_sidor,
                                        stopp=sedan is None)),
        ("e_avrop", lambda: e_avrop.kor(torr=torr)),
        ("adda", lambda: adda.kor(torr=torr)),
        ("avropa", lambda: avropa.kor(torr=torr)),
        ("hbv", lambda: hbv.kor(torr=torr)),
        ("sinfra", lambda: sinfra.kor(torr=torr)),
        # Sist: gör dagens nya avtal till utgångssignaler direkt.
        ("avtalsutgangar", lambda: avtalsutgangar.kor(torr=torr)),
    ]
    for namn, fn in kallor:
        if bara and namn != bara:
            continue
        try:
            r = fn()
            click.echo(f"{namn}: {r['status']} - {r['nya']} nya, "
                       f"{r['dubbletter']} dubbletter"
                       + (" [TORR]" if torr else ""))
        except Exception as e:
            # kor_kalla fångar det mesta själv; detta är bältet till hängslena.
            click.echo(f"{namn}: KRASCH - {type(e).__name__}: {e}", err=True)


@click.command("laga-ted-titlar")
def laga_ted_titlar_command():
    """Ta bort TED-poster vars titlar klipptes fel (börjar med gemen bokstav),
    inklusive tillhörande utgångssignaler. Nästa 'flask kor-kallor'
    återskapar dem med den lagade rensaren."""
    db = get_db()
    trasiga_avtal = db.execute(
        "SELECT id, extern_id FROM avtal WHERE kalla='ted-tilldelning' "
        "AND titel GLOB '[a-zåäö]*'").fetchall()
    for a in trasiga_avtal:
        db.execute("DELETE FROM signal WHERE extern_id=?",
                   (f"utgang:{a['extern_id']}",))
        db.execute("DELETE FROM avtal WHERE id=?", (a["id"],))
    n = db.execute("SELECT COUNT(*) c FROM signal WHERE kalla='ted' "
                   "AND titel GLOB '[a-zåäö]*'").fetchone()["c"]
    db.execute("DELETE FROM signal WHERE kalla='ted' AND titel GLOB '[a-zåäö]*'")
    db.commit()
    click.echo(f"Rensade {len(trasiga_avtal)} avtal och {n} signaler med "
               f"trasiga titlar. Kör nu: flask kor-kallor")
