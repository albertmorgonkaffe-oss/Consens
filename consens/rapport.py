"""Branschrapport som Word-dokument.

Insikter-sidan visar konkurrensläget per bransch i produkten. Samma
underlag, i ett dokument som går att mejla, bifoga en offert eller
lägga ut som marknadsföring: "Städbranschens offentliga marknad".

Rapporten bygger på samma beräkning som Insikter (berakna_insikter)
plus branschspecifika detaljer: största köpare, kommande utgångar och
metodnot. Inget hittas på - saknas underlag skrivs det ut.
"""
import io
import re
from datetime import date, timedelta

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

from .cpv import bransch_for_cpv
from .vyer.insikter import MIN_UNDERLAG, berakna_insikter

MORK = RGBColor(0x1F, 0x29, 0x33)
GRA = RGBColor(0x5A, 0x64, 0x72)

MANADER = ["januari", "februari", "mars", "april", "maj", "juni", "juli",
           "augusti", "september", "oktober", "november", "december"]


def _tal_rapport(n) -> str:
    """Svensk tusentalsformattering: 12 345."""
    return "{:,}".format(int(n)).replace(",", " ")


def _rubrik(doc, text, storlek=16, efter=6):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14)
    p.paragraph_format.space_after = Pt(efter)
    r = p.add_run(text)
    r.bold = True
    r.font.size = Pt(storlek)
    r.font.color.rgb = MORK
    return p


def _brod(doc, text, storlek=10.5, kursiv=False, farg=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(7)
    r = p.add_run(text)
    r.font.size = Pt(storlek)
    r.italic = kursiv
    r.font.color.rgb = farg or MORK
    return p


def _punkt(doc, text):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run(text)
    r.font.size = Pt(10.5)
    return p


def _mnkr(varde) -> str:
    if not varde:
        return "–"
    if varde >= 1e9:
        return f"{varde / 1e9:.1f} mdkr".replace(".", ",")
    return f"{varde / 1e6:.0f} mnkr"


def _branschdata(db, bransch: str) -> dict:
    """Detaljer utöver Insikters aggregat: köpare, värde, utgångar."""
    idag = date.today().isoformat()
    horisont = (date.today() + timedelta(days=548)).isoformat()
    kopare: dict = {}
    varde_tot = 0.0
    utgangar = []
    for rad in db.execute(
            "SELECT a.titel, a.varde, a.slutdatum, a.leverantor, "
            "a.cpv, o.namn AS org_namn FROM avtal a "
            "JOIN organisation o ON o.id = a.organisation_id "
            "WHERE a.cpv IS NOT NULL"):
        if bransch_for_cpv(rad["cpv"]) != bransch:
            continue
        varde_tot += rad["varde"] or 0
        k = kopare.setdefault(rad["org_namn"], dict(n=0, varde=0.0))
        k["n"] += 1
        k["varde"] += rad["varde"] or 0
        if rad["slutdatum"] and idag < rad["slutdatum"] <= horisont:
            utgangar.append(rad)
    utgangar.sort(key=lambda r: r["slutdatum"])
    return dict(
        varde_tot=varde_tot,
        kopare=sorted(kopare.items(), key=lambda x: -x[1]["varde"])[:10],
        antal_kopare=len(kopare),
        utgangar=utgangar[:15],
        utgangar_totalt=len(utgangar))


def bygg_rapport(db, bransch: str) -> tuple[bytes, str] | None:
    """Returnerar (docx-bytes, filnamn) eller None om branschen saknas
    eller har för tunt underlag."""
    insikt = next((i for i in berakna_insikter(db)
                   if i["bransch"] == bransch), None)
    if insikt is None:
        return None
    d = _branschdata(db, bransch)
    idag = date.today()

    doc = Document()
    stil = doc.styles["Normal"]
    stil.font.name = "Calibri"
    stil.font.size = Pt(10.5)

    # Försättsblad
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(bransch)
    r.bold = True
    r.font.size = Pt(26)
    r.font.color.rgb = MORK
    _brod(doc, "Den offentliga marknaden i siffror", 13, farg=GRA)
    _brod(doc, f"Rapport genererad {idag.isoformat()} ur Consens databas "
               f"över svenska upphandlingar, tilldelningar och avtal.",
          9.5, kursiv=True, farg=GRA)

    # Sammanfattning
    _rubrik(doc, "Sammanfattning")
    _punkt(doc, f"{insikt['n']} tilldelade kontrakt i registret, till ett "
                f"samlat värde om {_mnkr(d['varde_tot'])}.")
    _punkt(doc, f"{d['antal_kopare']} offentliga organisationer har "
                f"upphandlat inom branschen.")
    if insikt["snitt_anbud"]:
        rad = (f"I genomsnitt {str(insikt['snitt_anbud']).replace('.', ',')} "
               f"anbud per upphandling")
        if insikt["andel_ett_anbud"] is not None:
            rad += (f", och {insikt['andel_ett_anbud']} procent av "
                    "upphandlingarna avgjordes med ett enda anbud")
        _punkt(doc, rad + f". Bedömning: {insikt['lage'].lower()}.")
    else:
        _punkt(doc, "Antal anbud saknas i källdata för branschen, så "
                    "konkurrensläget kan inte bedömas.")
    if insikt["topp_andel"] is not None:
        _punkt(doc, f"De fem största leverantörerna står för "
                    f"{insikt['topp_andel']} procent av kontraktsvärdet.")
    if insikt["medellangd"]:
        _punkt(doc, f"Genomsnittlig avtalslängd: {insikt['medellangd']} "
                    "månader.")
    _punkt(doc, f"{insikt['utgar']} avtal löper ut inom 18 månader, "
                f"fördelat på {insikt['utgar_orgar']} organisationer.")

    # Konkurrensläge
    _rubrik(doc, "Konkurrensläge")
    if insikt["snitt_anbud"]:
        _brod(doc, f"Branschen bedöms ha {insikt['lage'].lower()}. "
                   "Bedömningen bygger på genomsnittligt antal anbud och "
                   "andelen upphandlingar som avgjorts med ett enda "
                   "anbud: under tre anbud i snitt, eller minst en "
                   "fjärdedel enanbudsupphandlingar, räknas som låg "
                   "konkurrens.")
        if insikt["lage_klass"] == "lag":
            _brod(doc, "För en leverantör betyder låg konkurrens att "
                       "chansen att vinna är högre än normalt. För en "
                       "upphandlande organisation betyder det omvänt att "
                       "villkoren sannolikt kunde bli bättre med fler "
                       "anbudsgivare.")
    else:
        _brod(doc, "Uppgift om antal anbud saknas för branschens "
                   "kontrakt i källdata. Det är vanligt inom områden där "
                   "avrop sker från ramavtal.")

    # Största leverantörer
    if insikt["topp"]:
        _rubrik(doc, "Största leverantörer")
        _brod(doc, "Andel av branschens samlade kontraktsvärde. Ramavtal "
                   "med flera vinnare delas lika mellan bolagen.", 9.5,
              farg=GRA)
        tab = doc.add_table(rows=1, cols=2)
        tab.style = "Light Grid Accent 1"
        tab.rows[0].cells[0].text = "Leverantör"
        tab.rows[0].cells[1].text = "Andel"
        for namn, andel in insikt["topp"]:
            rad = tab.add_row().cells
            rad[0].text = namn[:80]
            rad[1].text = f"{andel} %"

    # Största köpare
    if d["kopare"]:
        _rubrik(doc, "Största köpare")
        tab = doc.add_table(rows=1, cols=3)
        tab.style = "Light Grid Accent 1"
        for i, txt in enumerate(("Organisation", "Kontrakt", "Värde")):
            tab.rows[0].cells[i].text = txt
        for namn, v in d["kopare"]:
            rad = tab.add_row().cells
            rad[0].text = namn[:60]
            rad[1].text = str(v["n"])
            rad[2].text = _mnkr(v["varde"])

    # Kommande utgångar
    if d["utgangar"]:
        _rubrik(doc, "Avtal som löper ut inom 18 månader")
        _brod(doc, f"{d['utgangar_totalt']} avtal totalt; de "
                   f"{len(d['utgangar'])} närmaste visas. Ett avtal som "
                   "löper ut föregås normalt av en ny upphandling.",
              9.5, farg=GRA)
        tab = doc.add_table(rows=1, cols=4)
        tab.style = "Light Grid Accent 1"
        for i, txt in enumerate(("Avtal", "Köpare", "Leverantör",
                                 "Löper ut")):
            tab.rows[0].cells[i].text = txt
        for rad_ in d["utgangar"]:
            rad = tab.add_row().cells
            rad[0].text = (rad_["titel"] or "")[:70]
            rad[1].text = (rad_["org_namn"] or "")[:40]
            delar = [d.strip() for d in re.split(r"[,;]",
                     rad_["leverantor"] or "") if d.strip()]
            rad[2].text = (", ".join(delar) or "–")[:60]
            rad[3].text = rad_["slutdatum"]

    # Metod
    _rubrik(doc, "Om underlaget")
    _brod(doc, "Rapporten bygger på tilldelningsbeslut och avtal som "
               "publicerats i offentliga källor (TED med flera) och som "
               "klassificerats till bransch via kontraktens CPV-koder. "
               f"Branscher med färre än {MIN_UNDERLAG} tilldelningar "
               "redovisas inte.", 9.5, farg=GRA)
    _brod(doc, "Kontraktsvärden är totalvärden och ofta fleråriga "
               "ramavtalstak, inte årliga volymer. Antal anbud saknas för "
               "en del kontrakt och räknas då inte in i snitten. Svensk "
               "upphandlingsdata publicerar vinnande leverantör, inte "
               "vilka som lämnade anbud.", 9.5, farg=GRA)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Consens · consens.se")
    r.font.size = Pt(9)
    r.font.color.rgb = GRA

    buf = io.BytesIO()
    doc.save(buf)
    slug = re.sub(r"[^a-z0-9]+", "-", bransch.lower()).strip("-")
    return buf.getvalue(), f"consens-{slug}-{idag.isoformat()}.docx"


# ---------- Leverantörsrapport ----------

def bygg_organisationsrapport(db, oid: int) -> tuple[bytes, str] | None:
    """Köparprofil: allt Consens vet om en offentlig organisation, i ett
    dokument att förbereda kundmötet med. Samma maskin som bransch och
    leverantör: rubriker, punkter, tabeller, metodnot."""
    from .vyer.flode import KONTOTYPER, _arshjul, _inkopsmonster

    org = db.execute("SELECT * FROM organisation WHERE id=?",
                     (oid,)).fetchone()
    if org is None:
        return None
    idag = date.today()
    idag_s = idag.isoformat()

    avtal = [dict(a) for a in db.execute(
        "SELECT titel, leverantor, varde, startdatum, slutdatum "
        "FROM avtal WHERE organisation_id=? "
        "AND (slutdatum IS NULL OR slutdatum >= ?) "
        "ORDER BY slutdatum IS NULL, slutdatum", (oid, idag_s)).fetchall()]
    upphandlingar = db.execute(
        "SELECT titel, deadline, varde FROM signal "
        "WHERE organisation_id=? AND typ='upphandling' "
        "AND dublett_av IS NULL AND (deadline IS NULL OR deadline >= ?) "
        "ORDER BY deadline IS NULL, deadline", (oid, idag_s)).fetchall()
    planerade = db.execute(
        "SELECT titel, deadline, sammanfattning FROM signal "
        "WHERE organisation_id=? AND typ='planerad' "
        "AND dublett_av IS NULL AND (deadline IS NULL OR deadline >= ?) "
        "ORDER BY deadline IS NULL, deadline", (oid, idag_s)).fetchall()
    leverantorer: dict = {}
    for a in db.execute(
            "SELECT leverantor, COUNT(*) AS n, SUM(varde) AS varde "
            "FROM avtal WHERE organisation_id=? AND leverantor IS NOT NULL "
            "GROUP BY leverantor ORDER BY SUM(varde) DESC LIMIT 12",
            (oid,)).fetchall():
        leverantorer[a["leverantor"]] = a
    inkop = _inkopsmonster(db, org)
    arshjul = _arshjul(avtal)
    invanare = db.execute(
        "SELECT varde, ar FROM nyckeltal WHERE organisation_id=? "
        "AND slag='N01951'", (oid,)).fetchone()
    inom_12 = [a for a in avtal if a["slutdatum"]
               and a["slutdatum"] < (idag + timedelta(days=365)).isoformat()]

    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10.5)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(org["namn"])
    r.bold = True
    r.font.size = Pt(24)
    r.font.color.rgb = MORK
    undertext = KONTOTYPER.get(org["kontotyp"], org["kontotyp"])
    if org["orgnr"]:
        undertext = f"Org.nr {org['orgnr']} · " + undertext
    if org["lan"]:
        undertext += f" · {org['lan']}"
    _brod(doc, undertext + " · Köparprofil: avtal, utgångar och "
          "kommande upphandlingar", 12, farg=GRA)
    _brod(doc, f"Rapport genererad {idag_s} ur Consens databas över "
               "svenska upphandlingar, tilldelningar och avtal.",
          9.5, kursiv=True, farg=GRA)

    _rubrik(doc, "Överblick")
    if invanare:
        _punkt(doc, f"{_tal_rapport(invanare['varde'])} invånare "
                    f"({invanare['ar']}, Kolada).")
    varde_aktiva = sum(a["varde"] or 0 for a in avtal)
    if avtal:
        _punkt(doc, f"{len(avtal)} aktiva avtal i registret till ett "
                    f"samlat värde om {_mnkr(varde_aktiva)}.")
    if inom_12:
        _punkt(doc, f"{len(inom_12)} avtal löper ut inom tolv månader. "
                    f"Nästa {inom_12[0]['slutdatum']}.")
    if upphandlingar:
        _punkt(doc, f"{len(upphandlingar)} pågående upphandlingar.")
    if planerade:
        _punkt(doc, f"{len(planerade)} planerade upphandlingar, hämtade "
                    "ur upphandlingsplaner och beslutsunderlag.")
    if inkop:
        _punkt(doc, f"Publicerar öppna inköpsdata: {_mnkr(inkop['belopp'])} "
                    f"till {_tal_rapport(inkop['antal_leverantorer'])} "
                    f"leverantörer under {inkop['ar']}.")
    if not any([avtal, upphandlingar, planerade, inkop]):
        _brod(doc, "Registret saknar ännu aktiva avtal, upphandlingar "
                   "och planer för organisationen.", farg=GRA)

    if inom_12 or avtal:
        _rubrik(doc, "Avtal som löper ut")
        _brod(doc, "Sorterade efter utgångsdatum. Ett avtal som löper ut "
                   "föregås normalt av en ny upphandling. Det är då "
                   "affären står öppen.", 9.5, farg=GRA)
        tab = doc.add_table(rows=1, cols=4)
        tab.style = "Light Grid Accent 1"
        for i, txt in enumerate(("Avtal", "Leverantör", "Värde",
                                 "Löper ut")):
            tab.rows[0].cells[i].text = txt
        for a in [a for a in avtal if a["slutdatum"]][:25]:
            rad = tab.add_row().cells
            rad[0].text = (a["titel"] or "")[:70]
            rad[1].text = (a["leverantor"] or "")[:40]
            rad[2].text = _mnkr(a["varde"])
            rad[3].text = a["slutdatum"]

    if arshjul:
        _rubrik(doc, "Utgångar per månad, 24 månader framåt")
        _brod(doc, "Samma årshjul som organisationssidan visar: när "
                   "organisationen är i marknaden och när det är tyst.",
              9.5, farg=GRA)
        tab = doc.add_table(rows=1, cols=3)
        tab.style = "Light Grid Accent 1"
        for i, txt in enumerate(("Månad", "Avtal som löper ut", "Värde")):
            tab.rows[0].cells[i].text = txt
        for ruta in arshjul["rutor"]:
            if not ruta["antal"]:
                continue
            rad = tab.add_row().cells
            rad[0].text = f"{MANADER[ruta['manad'] - 1]} {ruta['ar']}"
            rad[1].text = str(ruta["antal"])
            rad[2].text = _mnkr(ruta["varde"]) if ruta["varde"] else "okänt"

    if upphandlingar:
        _rubrik(doc, "Pågående upphandlingar")
        tab = doc.add_table(rows=1, cols=3)
        tab.style = "Light Grid Accent 1"
        for i, txt in enumerate(("Upphandling", "Sista anbudsdag",
                                 "Värde")):
            tab.rows[0].cells[i].text = txt
        for u in upphandlingar[:10]:
            rad = tab.add_row().cells
            rad[0].text = (u["titel"] or "")[:80]
            rad[1].text = u["deadline"] or "ej angiven"
            rad[2].text = _mnkr(u["varde"])

    if planerade:
        _rubrik(doc, "Planerade upphandlingar")
        _brod(doc, "Upphandlingar som ännu inte annonserats, ur "
                   "upphandlingsplaner och politiska beslutsunderlag. "
                   "Här går kravbilden fortfarande att påverka.",
              9.5, farg=GRA)
        tab = doc.add_table(rows=1, cols=2)
        tab.style = "Light Grid Accent 1"
        for i, txt in enumerate(("Upphandling", "Planerad tidpunkt")):
            tab.rows[0].cells[i].text = txt
        for pl in planerade[:15]:
            rad = tab.add_row().cells
            rad[0].text = (pl["titel"] or "")[:80]
            rad[1].text = pl["deadline"] or "ej angiven"

    if leverantorer:
        _rubrik(doc, "Största leverantörerna enligt avtalsregistret")
        tab = doc.add_table(rows=1, cols=3)
        tab.style = "Light Grid Accent 1"
        for i, txt in enumerate(("Leverantör", "Kontrakt", "Värde")):
            tab.rows[0].cells[i].text = txt
        for namn, v in leverantorer.items():
            rad = tab.add_row().cells
            rad[0].text = namn[:60]
            rad[1].text = str(v["n"])
            rad[2].text = _mnkr(v["varde"])

    if inkop:
        _rubrik(doc, f"Vad organisationen faktiskt betalade {inkop['ar']}")
        _brod(doc, "Ur organisationens öppet publicerade "
                   "leverantörsreskontra. Ett ramavtalstak säger vad "
                   "kunden får handla för; reskontran säger vad den "
                   "handlade för.", 9.5, farg=GRA)
        tab = doc.add_table(rows=1, cols=3)
        tab.style = "Light Grid Accent 1"
        for i, txt in enumerate(("Leverantör", "Fakturor", "Belopp")):
            tab.rows[0].cells[i].text = txt
        for rd in inkop["rader"]:
            rad = tab.add_row().cells
            rad[0].text = (rd["leverantor"] or "")[:60]
            rad[1].text = _tal_rapport(rd["antal"])
            rad[2].text = _mnkr(rd["belopp"])
        if inkop["andel_avtal"] is not None:
            _brod(doc, f"{inkop['andel_avtal']} procent av beloppet gick "
                       "till leverantörer som återfinns i "
                       "avtalsregistret.", 9.5, farg=GRA)

    _rubrik(doc, "Om underlaget")
    _brod(doc, "Rapporten bygger på tilldelningsbeslut och avtal ur "
               "offentliga källor (TED med flera), organisationens "
               "upphandlingsplaner och beslutsunderlag samt i "
               "förekommande fall öppet publicerade inköpsdata. "
               "Kontraktsvärden är totalvärden, ofta fleråriga "
               "ramavtalstak, inte årliga volymer. Registret gör inte "
               "anspråk på att vara heltäckande: avtal under "
               "direktupphandlingsgränsen och äldre avtal kan saknas.",
          9.5, farg=GRA)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Consens · consens.se")
    r.font.size = Pt(9)
    r.font.color.rgb = GRA

    buf = io.BytesIO()
    doc.save(buf)
    namn = re.sub(r"[^a-z0-9]+", "-", org["namn"].lower()
                  .replace("å", "a").replace("ä", "a").replace("ö", "o")
                  ).strip("-")
    return buf.getvalue(), f"consens-{namn}-{idag_s}.docx"


def bygg_leverantorsrapport(db, slug: str) -> tuple[bytes, str] | None:
    """Konkurrentprofil: allt Consens vet om en leverantör, i ett
    dokument som går att ta med till ett möte."""
    from .vyer.leverantorer import _inkop_per_namn, leverantorer

    lev = leverantorer(db).get(slug)
    if lev is None:
        return None
    idag = date.today()
    idag_s = idag.isoformat()
    avtal = lev["avtal"]
    kommande = sorted((a for a in avtal
                       if a["slutdatum"] and a["slutdatum"] >= idag_s),
                      key=lambda a: a["slutdatum"])
    branscher: dict = {}
    kunder: dict = {}
    anbud = []
    for a in avtal:
        br = bransch_for_cpv(a["cpv"] or "")
        if br:
            b = branscher.setdefault(br, dict(n=0, varde=0.0))
            b["n"] += 1
            b["varde"] += a["varde"] or 0
        k = kunder.setdefault(a["org_namn"], dict(n=0, varde=0.0))
        k["n"] += 1
        k["varde"] += a["varde"] or 0
        try:  # kolumnen finns inte i registrets urval
            if a["antal_anbud"]:
                anbud.append(a["antal_anbud"])
        except (IndexError, KeyError):
            pass
    inkop = None
    if lev["orgnr"]:
        r = db.execute(
            "SELECT SUM(belopp) AS b, SUM(antal) AS a, "
            "COUNT(DISTINCT organisation_id) AS o, MIN(ar) AS a1, "
            "MAX(ar) AS a2 FROM inkopsmonster WHERE leverantor_orgnr=?",
            (lev["orgnr"],)).fetchone()
        if r and r["b"]:
            inkop = dict(r)
    if inkop is None:
        inkop = _inkop_per_namn(db).get(lev["norm"])

    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10.5)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(lev["visning"])
    r.bold = True
    r.font.size = Pt(24)
    r.font.color.rgb = MORK
    undertext = "Leverantörsprofil: offentliga kontrakt och avtalsutgångar"
    if lev["orgnr"]:
        undertext = f"Org.nr {lev['orgnr']} · " + undertext
    _brod(doc, undertext, 12, farg=GRA)
    _brod(doc, f"Rapport genererad {idag_s} ur Consens databas över "
               "svenska upphandlingar, tilldelningar och avtal.",
          9.5, kursiv=True, farg=GRA)

    _rubrik(doc, "Överblick")
    _punkt(doc, f"{len(avtal)} vunna kontrakt i registret till ett samlat "
                f"värde om {_mnkr(lev['varde'])}.")
    _punkt(doc, f"{len(lev['orger'])} offentliga kunder.")
    if branscher:
        topp_br = sorted(branscher.items(), key=lambda x: -x[1]["varde"])
        _punkt(doc, "Tyngdpunkt: " + ", ".join(
            f"{b} ({v['n']} kontrakt)" for b, v in topp_br[:3]) + ".")
    if anbud:
        snitt = sum(anbud) / len(anbud)
        _punkt(doc, f"Mötte i genomsnitt {snitt:.1f}".replace(".", ",")
               + f" anbudsgivare i de {len(anbud)} upphandlingar där "
                 "antalet redovisats.")
    if lev["ramavtal"]:
        _punkt(doc, f"{len(lev['ramavtal'])} platser på inköpscentralernas "
                    "ramavtal (Adda, Avropa, HBV, Sinfra).")
    if kommande:
        _punkt(doc, f"{len(kommande)} avtal löper ut framöver. Nästa "
                    f"{kommande[0]['slutdatum']}.")
    if inkop and inkop.get("b"):
        belopp = (f"{inkop['b'] / 1e6:.1f} mnkr".replace('.', ',')
                  if inkop['b'] < 1e9 else _mnkr(inkop['b']))
        _punkt(doc, f"Offentliga organisationer har betalat ca "
                    f"{belopp} enligt öppna "
                    f"leverantörsreskontror ({inkop['a']} fakturor).")

    if kommande:
        _rubrik(doc, "Avtal som löper ut")
        _brod(doc, "Sorterade efter utgångsdatum. Ett avtal som löper ut "
                   "föregås normalt av en ny upphandling. Det är då "
                   "affären står öppen.", 9.5, farg=GRA)
        tab = doc.add_table(rows=1, cols=4)
        tab.style = "Light Grid Accent 1"
        for i, txt in enumerate(("Avtal", "Köpare", "Värde", "Löper ut")):
            tab.rows[0].cells[i].text = txt
        for a in kommande[:25]:
            rad = tab.add_row().cells
            rad[0].text = (a["titel"] or "")[:70]
            rad[1].text = (a["org_namn"] or "")[:40]
            rad[2].text = _mnkr(a["varde"])
            rad[3].text = a["slutdatum"]

    if kunder:
        _rubrik(doc, "Största offentliga kunder")
        tab = doc.add_table(rows=1, cols=3)
        tab.style = "Light Grid Accent 1"
        for i, txt in enumerate(("Organisation", "Kontrakt", "Värde")):
            tab.rows[0].cells[i].text = txt
        for namn, v in sorted(kunder.items(),
                              key=lambda x: -x[1]["varde"])[:12]:
            rad = tab.add_row().cells
            rad[0].text = namn[:60]
            rad[1].text = str(v["n"])
            rad[2].text = _mnkr(v["varde"])

    if branscher:
        _rubrik(doc, "Branschfördelning")
        tab = doc.add_table(rows=1, cols=3)
        tab.style = "Light Grid Accent 1"
        for i, txt in enumerate(("Bransch", "Kontrakt", "Värde")):
            tab.rows[0].cells[i].text = txt
        for namn, v in sorted(branscher.items(),
                              key=lambda x: -x[1]["varde"]):
            rad = tab.add_row().cells
            rad[0].text = namn
            rad[1].text = str(v["n"])
            rad[2].text = _mnkr(v["varde"])

    if lev["ramavtal"]:
        _rubrik(doc, "Ramavtalsplatser")
        for kalla, titel in lev["ramavtal"][:20]:
            _punkt(doc, f"{titel} ({kalla})")

    _rubrik(doc, "Om underlaget")
    _brod(doc, "Rapporten bygger på tilldelningsbeslut och avtal ur "
               "offentliga källor (TED med flera) samt "
               "inköpscentralernas avtalskataloger. Ramavtal med flera "
               "vinnare räknas som ett kontrakt per leverantör. "
               "Kontraktsvärden är totalvärden, ofta fleråriga "
               "ramavtalstak, inte årliga volymer.", 9.5, farg=GRA)
    _brod(doc, "Svensk upphandlingsdata publicerar vinnande leverantör, "
               "inte vilka som lämnade anbud. Antal mötta anbudsgivare "
               "avser därför upphandlingar där antalet anbud redovisats, "
               "utan uppgift om vilka företagen var.", 9.5, farg=GRA)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Consens · consens.se")
    r.font.size = Pt(9)
    r.font.color.rgb = GRA

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), f"consens-{slug}-{idag_s}.docx"
