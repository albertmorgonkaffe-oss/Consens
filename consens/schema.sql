-- Consens datamodell v0.1
DROP TABLE IF EXISTS signal;
DROP TABLE IF EXISTS avtal;
DROP TABLE IF EXISTS organisation;
DROP TABLE IF EXISTS korningshistorik;

-- Köparregistret. Kontotyp sätts här, inte i källdata (finns ej i eForms).
CREATE TABLE organisation (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    namn      TEXT NOT NULL,
    orgnr     TEXT UNIQUE,               -- normaliserat NNNNNN-NNNN
    kontotyp  TEXT NOT NULL CHECK (kontotyp IN
              ('kommun','region','myndighet','kommunalt_bolag',
               'regionalt_bolag','ovrig')),
    lan       TEXT,                      -- 'Flera län' tillåts
    webbplats TEXT,
    alias     TEXT,                      -- alternativt namn, t.ex. 'Borås kommun' för Borås stad
    epost     TEXT
);

-- Kända avtal: tillståndet. Byggs från tilldelningar + avtalsregister.
CREATE TABLE avtal (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id  INTEGER NOT NULL REFERENCES organisation(id),
    titel            TEXT NOT NULL,
    leverantor       TEXT,
    leverantor_orgnr TEXT,
    varde            INTEGER,            -- kronor
    varde_sort       TEXT CHECK (varde_sort IN
                     ('anbud','ramavtalstak','uppskattning')),
    antal_anbud      INTEGER,
    startdatum       TEXT,               -- ISO-datum
    slutdatum        TEXT,               -- ISO-datum; kan ha förlängts via option
    cpv              TEXT,               -- huvud-CPV, 8 siffror
    kalla            TEXT NOT NULL,
    extern_id        TEXT NOT NULL UNIQUE, -- källprefix, t.ex. 'ted-tilldelning:449310-2023'
    url              TEXT,                 -- länk till annonsen hos källan
    detaljer         TEXT                  -- JSON: all rådata från källan
);

-- Signaler: händelserna i Flödet. En avtalsutgång pekar på sitt avtal.
CREATE TABLE signal (
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
    deadline         TEXT,               -- sista anbudsdag resp. avtalets slutdatum
    prioritet        TEXT DEFAULT 'normal' CHECK (prioritet IN ('normal','hog')),
    korning_id       INTEGER REFERENCES korningshistorik(id),
    url              TEXT,               -- länk till annonsen hos källan
    publicerad       TEXT,               -- källans publiceringsdatum (backfill)
    detaljer         TEXT,               -- JSON: all rådata från källan
    upphandlings_id  TEXT,               -- normaliserad referens (dubblettfacit)
    dublett_av       INTEGER REFERENCES signal(id),
    upptackt         TEXT NOT NULL DEFAULT (date('now'))
);

CREATE INDEX idx_signal_typ ON signal(typ);
CREATE INDEX idx_signal_deadline ON signal(deadline);
CREATE INDEX idx_signal_org ON signal(organisation_id);
CREATE INDEX idx_avtal_org ON avtal(organisation_id);
CREATE INDEX idx_avtal_slutdatum ON avtal(slutdatum);
CREATE INDEX idx_avtal_leverantor ON avtal(leverantor);

-- Körningshistorik: varje hämtning loggas, lyft från Nodpunkts ramverk.
CREATE TABLE korningshistorik (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    kalla            TEXT NOT NULL,
    startad          TEXT NOT NULL,
    avslutad         TEXT,
    antal_nya        INTEGER DEFAULT 0,
    antal_dubbletter INTEGER DEFAULT 0,
    status           TEXT DEFAULT 'pagar' CHECK (status IN ('pagar','ok','fel')),
    fel              TEXT
);

-- Användare och behörighet. Roller: admin (allt inkl. /admin) och anvandare.
DROP TABLE IF EXISTS anvandare;
CREATE TABLE anvandare (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    epost         TEXT NOT NULL UNIQUE,
    namn          TEXT,
    losenord_hash TEXT NOT NULL,
    roll          TEXT NOT NULL DEFAULT 'anvandare'
                  CHECK (roll IN ('trial','kompis','anvandare','admin')),
    aktiv         INTEGER NOT NULL DEFAULT 1,
    trial_start   TEXT,                  -- sätts när roll=trial; 7 dagar
    foretag       TEXT,
    giltig_till   TEXT,                  -- ISO-datum; spärr efter (ej admin)
    mork_lage     INTEGER NOT NULL DEFAULT 0,
    tur_visad     INTEGER NOT NULL DEFAULT 0,
    samtycke_marknadsforing INTEGER NOT NULL DEFAULT 0,
    stripe_kund   TEXT,               -- Stripe customer-id (cus_...)
    stripe_prenumeration TEXT,        -- Stripe subscription-id (sub_...)
    stripe_trial_slut TEXT,           -- ISO-datum, för påminnelsemejlet
    skapad        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Yrkestaggar: vilken kompetens som köps, oberoende av bransch.
DROP TABLE IF EXISTS signal_yrke;
CREATE TABLE signal_yrke (
    signal_id INTEGER NOT NULL REFERENCES signal(id) ON DELETE CASCADE,
    yrke      TEXT NOT NULL,
    PRIMARY KEY (signal_id, yrke)
);
CREATE INDEX idx_signal_yrke ON signal_yrke(yrke);

DROP TABLE IF EXISTS avtal_yrke;
CREATE TABLE avtal_yrke (
    avtal_id INTEGER NOT NULL REFERENCES avtal(id) ON DELETE CASCADE,
    yrke     TEXT NOT NULL,
    PRIMARY KEY (avtal_id, yrke)
);
CREATE INDEX idx_avtal_yrke ON avtal_yrke(yrke);

-- Sparade filter med e-postbevakning (digest).
DROP TABLE IF EXISTS bevakning;
CREATE TABLE bevakning (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    anvandare_id INTEGER NOT NULL REFERENCES anvandare(id) ON DELETE CASCADE,
    namn         TEXT NOT NULL,
    params       TEXT NOT NULL,            -- sparad querystring från Flödet
    frekvens     TEXT NOT NULL DEFAULT 'daglig'
                 CHECK (frekvens IN ('daglig','veckovis')),
    aktiv        INTEGER NOT NULL DEFAULT 1,
    senast_kord  TEXT,
    skapad       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sparade enskilda objekt (stjärnmärkning).
DROP TABLE IF EXISTS sparad_signal;
CREATE TABLE sparad_signal (
    anvandare_id INTEGER NOT NULL REFERENCES anvandare(id) ON DELETE CASCADE,
    signal_id    INTEGER NOT NULL REFERENCES signal(id) ON DELETE CASCADE,
    skapad       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (anvandare_id, signal_id)
);
DROP TABLE IF EXISTS sparad_avtal;
CREATE TABLE sparad_avtal (
    anvandare_id INTEGER NOT NULL REFERENCES anvandare(id) ON DELETE CASCADE,
    avtal_id     INTEGER NOT NULL REFERENCES avtal(id) ON DELETE CASCADE,
    skapad       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (anvandare_id, avtal_id)
);

-- Egna listor med export.
DROP TABLE IF EXISTS lista_post;
DROP TABLE IF EXISTS lista;
CREATE TABLE lista (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    anvandare_id INTEGER NOT NULL REFERENCES anvandare(id) ON DELETE CASCADE,
    namn         TEXT NOT NULL,
    skapad       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE lista_post (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    lista_id  INTEGER NOT NULL REFERENCES lista(id) ON DELETE CASCADE,
    signal_id INTEGER REFERENCES signal(id) ON DELETE CASCADE,
    avtal_id  INTEGER REFERENCES avtal(id) ON DELETE CASCADE,
    skapad    TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((signal_id IS NULL) != (avtal_id IS NULL))
);

-- Nyckelpersoner hos organisationerna (importeras från Nodpunkt).
DROP TABLE IF EXISTS nyckelperson;
CREATE TABLE nyckelperson (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id INTEGER NOT NULL REFERENCES organisation(id) ON DELETE CASCADE,
    namn            TEXT NOT NULL,
    titel           TEXT,
    epost           TEXT,
    telefon         TEXT,
    rollgrupp       TEXT,
    harledd         INTEGER DEFAULT 0,
    kalla           TEXT,
    kalla_lank      TEXT,
    uppdaterad      TEXT
);
CREATE INDEX idx_nyckelperson_org ON nyckelperson(organisation_id);


-- Klickloggning för statistik (inloggade klick på signaler).
DROP TABLE IF EXISTS klick;
CREATE TABLE klick (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    anvandare_id INTEGER NOT NULL REFERENCES anvandare(id) ON DELETE CASCADE,
    signal_id    INTEGER NOT NULL REFERENCES signal(id) ON DELETE CASCADE,
    tidpunkt     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_klick_signal ON klick(signal_id);

-- Kontoförfrågningar från publika ansökningsformuläret.
DROP TABLE IF EXISTS kontoforfragan;
CREATE TABLE kontoforfragan (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    namn        TEXT NOT NULL,
    epost       TEXT NOT NULL,
    foretag     TEXT,
    meddelande  TEXT,
    status      TEXT NOT NULL DEFAULT 'ny' CHECK (status IN ('ny','hanterad','avvisad')),
    skapad      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Utskickshistorik.
DROP TABLE IF EXISTS utskick;
CREATE TABLE utskick (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    typ              TEXT NOT NULL CHECK (typ IN ('marknadsforing','driftmeddelande')),
    amne             TEXT NOT NULL,
    brodtext         TEXT NOT NULL,
    mottagarfilter   TEXT,
    antal_mottagare  INTEGER NOT NULL DEFAULT 0,
    skickad_av       INTEGER REFERENCES anvandare(id),
    skapad           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS losenordsaterstallning (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    anvandare_id INTEGER NOT NULL REFERENCES anvandare(id) ON DELETE CASCADE,
    token        TEXT NOT NULL UNIQUE,
    skapad       TEXT NOT NULL DEFAULT (datetime('now')),
    anvand       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS inbjudan (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    epost        TEXT NOT NULL,
    roll         TEXT NOT NULL DEFAULT 'anvandare',
    giltig_till  TEXT,                 -- kontots sluttid (valfritt)
    token        TEXT NOT NULL UNIQUE,
    skapad_av    INTEGER REFERENCES anvandare(id),
    skapad       TEXT NOT NULL DEFAULT (datetime('now')),
    anvand       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS utskickslogg (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    anvandare_id INTEGER REFERENCES anvandare(id) ON DELETE CASCADE,
    epost        TEXT NOT NULL,
    typ          TEXT NOT NULL,         -- digest/aterstallning/inbjudan/admin
    amne         TEXT,
    skickad      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS besok (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    sokvag   TEXT NOT NULL,
    tidpunkt TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_besok_tid ON besok(tidpunkt);

CREATE TABLE IF NOT EXISTS inkopsmonster (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id  INTEGER NOT NULL REFERENCES organisation(id),
    leverantor       TEXT NOT NULL,
    leverantor_orgnr TEXT,
    ar               INTEGER NOT NULL,
    belopp           REAL NOT NULL DEFAULT 0,   -- summa exkl moms
    antal            INTEGER NOT NULL DEFAULT 0, -- antal fakturor
    antal_avtal      INTEGER NOT NULL DEFAULT 0, -- grund R/U (via avtal)
    antal_grund      INTEGER NOT NULL DEFAULT 0, -- fakturor med grund-uppgift
    kalla_url        TEXT NOT NULL DEFAULT '',   -- filen posten kom ur
    hamtad           TEXT NOT NULL DEFAULT (date('now')),
    UNIQUE (organisation_id, leverantor, ar, kalla_url)
);
CREATE INDEX IF NOT EXISTS idx_inkop_org_ar ON inkopsmonster(organisation_id, ar);

CREATE TABLE IF NOT EXISTS nyckeltal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organisation_id INTEGER NOT NULL REFERENCES organisation(id),
    slag            TEXT NOT NULL,     -- Kolada-KPI, t.ex. N01951
    etikett         TEXT NOT NULL,     -- KPI-titeln från Koladas API
    ar              INTEGER NOT NULL,
    varde           REAL NOT NULL,
    hamtad          TEXT NOT NULL DEFAULT (date('now')),
    UNIQUE (organisation_id, slag)
);

CREATE TABLE IF NOT EXISTS bevakad_leverantor (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    anvandare_id  INTEGER NOT NULL REFERENCES anvandare(id),
    visningsnamn  TEXT NOT NULL,
    namn_norm     TEXT NOT NULL,
    orgnr         TEXT,
    slug          TEXT NOT NULL,
    skapad        TEXT NOT NULL DEFAULT (date('now')),
    UNIQUE (anvandare_id, namn_norm)
);

CREATE TABLE IF NOT EXISTS leverantorsnotis (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    anvandare_id  INTEGER NOT NULL REFERENCES anvandare(id),
    nyckel        TEXT NOT NULL,      -- t.ex. lu:<avtal_id> / ln:<avtal_id>
    skickad       TEXT NOT NULL DEFAULT (date('now')),
    UNIQUE (anvandare_id, nyckel)
);

CREATE TABLE IF NOT EXISTS meetingplus_instans (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    organisationsnamn TEXT NOT NULL,
    bas_url           TEXT NOT NULL UNIQUE,
    namnder           INTEGER DEFAULT 0,
    aktiv             INTEGER NOT NULL DEFAULT 1,
    upptackt          TEXT NOT NULL DEFAULT (date('now'))
);
