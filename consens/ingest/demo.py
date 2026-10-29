"""Demokälla: exempeldatat från mockuperna, så VPS:en visar något direkt.

Ersätts av riktiga källor (ted.py, kommers.py) — men behåll gärna modulen
som testfixtur för ramverket.
"""

ORGANISATIONER = [
    ("Gävle kommun", "212000-2338", "kommun", "Gävleborg", "https://gavle.se"),
    ("Region Skåne", "232100-0255", "region", "Skåne", "https://skane.se"),
    ("Trafikverket", "202100-6297", "myndighet", "Flera län",
     "https://trafikverket.se"),
    ("Trollhättans stad", "212000-1546", "kommun", "Västra Götaland",
     "https://trollhattan.se"),
    ("AB Gavlegårdarna", "556487-5937", "kommunalt_bolag", "Gävleborg", None),
    ("Västtrafik AB", "556558-5873", "regionalt_bolag", "Västra Götaland",
     None),
    ("Adda Inköpscentral AB", "556819-4798", "ovrig", "Stockholm",
     "https://adda.se"),
    ("Kammarkollegiet, Statens inköpscentral", "202100-0829", "myndighet",
     "Stockholm", "https://avropa.se"),
]

AVTAL = [
    # (org-orgnr, titel, leverantör, lev-orgnr, värde, sort, anbud,
    #  start, slut, cpv, källa, extern_id)
    ("232100-0255", "Medicinteknisk service", "Getinge Sverige AB",
     "556509-9514", 84_200_000, "anbud", 3, "2023-04-01", "2027-01-31",
     "50400000", "ted-tilldelning", "ted-tilldelning:449310-2023"),
    ("212000-2338", "IT-drift och support", "Atea Sverige AB",
     "556448-0282", 41_500_000, "anbud", 3, "2023-03-01", "2027-02-28",
     "72500000", "ted-tilldelning", "ted-tilldelning:38102-2023"),
    ("212000-2338", "Företagshälsovård", "Avonova Hälsa AB",
     "556500-6759", 12_800_000, "ramavtalstak", 2, "2023-06-01",
     "2027-05-31", "85147000", "kommers-tilldelning",
     "kommers-tilldelning:18455"),
    ("212000-1546", "Skolmåltidslivsmedel", "Martin & Servera AB",
     "556233-2451", 18_700_000, "ramavtalstak", 2, "2024-09-01",
     "2027-08-31", "15000000", "kommers-tilldelning",
     "kommers-tilldelning:19882"),
    ("212000-2338", "Utskrifts- och kopieringstjänster", "Ricoh Sverige AB",
     "556228-8851", 7_200_000, "ramavtalstak", 4, "2023-11-01",
     "2027-10-31", "79800000", "kommers-tilldelning",
     "kommers-tilldelning:17903"),
    ("202100-0829", "Programvaror och programvarutjänster",
     "Atea, Advania, Dustin m.fl.", None, None, None, 12, "2023-04-01",
     "2027-03-31", "48000000", "avropa", "avropa:23.3-10456-22"),
]

SIGNALER = [
    # Upphandlingar
    dict(typ="upphandling", org="212000-2338",
         titel="Nybyggnation av förskola, Sätra",
         sammanfattning="Totalentreprenad.", varde=96_000_000,
         varde_sort="uppskattning", cpv="45214100", lan="Gävleborg",
         kalla="kommers", extern_id="kommers:20614", deadline="2026-09-18"),
    dict(typ="upphandling", org="212000-2338",
         titel="Vinterväghållning 2026–2030, område Norr",
         sammanfattning="Ramavtal.", varde=34_000_000,
         varde_sort="uppskattning", cpv="90620000", lan="Gävleborg",
         kalla="kommers", extern_id="kommers:20655", deadline="2026-08-29"),
    dict(typ="upphandling", org="232100-0255",
         titel="Ramavtal konsulttjänster: projektledning och förändringsledning",
         sammanfattning="Ramavtal för verksamhetsnära konsultstöd.",
         varde=24_000_000, varde_sort="uppskattning", cpv="79421000",
         lan="Skåne", kalla="kommers", extern_id="kommers:20701",
         deadline="2026-09-30"),
    dict(typ="upphandling", org="202100-6297",
         titel="Drift och underhåll av vägbelysning, region Mitt",
         sammanfattning="Öppet förfarande, LUF.", varde=142_000_000,
         varde_sort="uppskattning", cpv="50232100", lan="Flera län",
         kalla="ted", extern_id="ted:512044-2026", deadline="2026-10-02"),
]


def ladda_organisationer(db):
    for namn, orgnr, kontotyp, lan, webb in ORGANISATIONER:
        db.execute(
            "INSERT OR IGNORE INTO organisation "
            "(namn, orgnr, kontotyp, lan, webbplats) VALUES (?,?,?,?,?)",
            (namn, orgnr, kontotyp, lan, webb),
        )
    for (orgnr, titel, lev, lev_orgnr, varde, sort, anbud, start, slut,
         cpv, kalla, eid) in AVTAL:
        org = db.execute(
            "SELECT id FROM organisation WHERE orgnr=?", (orgnr,)
        ).fetchone()
        db.execute(
            "INSERT OR IGNORE INTO avtal (organisation_id, titel, "
            "leverantor, leverantor_orgnr, varde, varde_sort, antal_anbud, "
            "startdatum, slutdatum, cpv, kalla, extern_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (org["id"], titel, lev, lev_orgnr, varde, sort, anbud,
             start, slut, cpv, kalla, eid),
        )
    db.commit()


def hamta():
    """Källfunktion för kor_kalla: upphandlingar + avtalsutgångar ur avtalen."""
    from ..db import get_db
    db = get_db()

    rader = []
    for s in SIGNALER:
        org = db.execute(
            "SELECT id FROM organisation WHERE orgnr=?", (s["org"],)
        ).fetchone()
        rad = {k: v for k, v in s.items() if k != "org"}
        rad["organisation_id"] = org["id"]
        rader.append(rad)

    # Avtalsutgångar genereras ur avtalstabellen — motorn i miniformat.
    for a in db.execute(
        "SELECT a.*, o.lan AS org_lan, o.namn AS org_namn "
        "FROM avtal a JOIN organisation o ON o.id=a.organisation_id"
    ).fetchall():
        rader.append(dict(
            typ="avtalsutgang",
            organisation_id=a["organisation_id"],
            avtal_id=a["id"],
            titel=f"{a['org_namn']} — {a['titel']} löper ut",
            sammanfattning=(f"Avtal med {a['leverantor']}."
                            if a["leverantor"] else None),
            varde=a["varde"], varde_sort=a["varde_sort"],
            cpv=a["cpv"], lan=a["org_lan"], kalla=a["kalla"],
            extern_id=f"utgang:{a['extern_id']}",
            deadline=a["slutdatum"],
            prioritet="hog" if (a["varde"] or 0) > 50_000_000 else "normal",
        ))
    return rader
