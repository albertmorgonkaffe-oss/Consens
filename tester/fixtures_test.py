# -*- coding: utf-8 -*-
"""Parsertester mot fixtures som återskapar Nodpunkts dokumenterade fällor."""
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, ".")

EFORMS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ContractAwardNotice xmlns:efext="http://data.europa.eu/p27/eforms-ubl-extensions/1"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <efext:EformsExtension><cbc:X>1</cbc:X></efext:EformsExtension>
 <cac:TenderResult>
   <cbc:AwardDate>2000-01-01+01:00</cbc:AwardDate><!-- PLATSHÅLLARE - ska ignoreras -->
 </cac:TenderResult>
 <cac:SettledContract>
   <cbc:AwardDate>2026-05-12+02:00</cbc:AwardDate>
 </cac:SettledContract>
 <cac:ProcurementProject>
   <cbc:Description>Lokalvård av kommunala fastigheter i tre etapper.</cbc:Description>
 </cac:ProcurementProject>
 <cbc:ProcedureCode>open</cbc:ProcedureCode>
 <cac:Address><cbc:CountrySubentityCode>SE313</cbc:CountrySubentityCode></cac:Address>
 <efac:SubmissionStatistics xmlns:efac="http://x">
   <efbc:StatisticsCode xmlns:efbc="http://y" listName="received-submission-type">tenders</efbc:StatisticsCode>
   <efbc:StatisticsNumeric xmlns:efbc="http://y">5</efbc:StatisticsNumeric>
 </efac:SubmissionStatistics>
 <cac:ProcurementProjectLot>
   <cac:PlannedPeriod>
     <cbc:StartDate>2026-09-01+02:00</cbc:StartDate>
     <cbc:EndDate>2029-08-31+02:00</cbc:EndDate>
   </cac:PlannedPeriod>
 </cac:ProcurementProjectLot>
 <cac:LotTender>
   <cbc:ID schemeName="tender">TEN-0001</cbc:ID>
   <cac:X><cbc:ID schemeName="tendering-party">TPA-0001</cbc:ID></cac:X>
   <cac:LegalMonetaryTotal>
     <cbc:PayableAmount currencyID="SEK">4500000.00</cbc:PayableAmount>
   </cac:LegalMonetaryTotal>
 </cac:LotTender>
 <cac:TenderingParty>
   <cbc:ID schemeName="tendering-party">TPA-0001</cbc:ID>
   <cac:Tenderer><cbc:ID schemeName="organization">ORG-0003</cbc:ID></cac:Tenderer>
 </cac:TenderingParty>
 <cac:Organization><cbc:ID schemeName="organization">ORG-0001</cbc:ID>
   <cbc:Name>Gävle kommun</cbc:Name></cac:Organization>
 <cac:Organization><cbc:ID schemeName="organization">ORG-0002</cbc:ID>
   <cbc:Name>Förvaltningsrätten i Malmö</cbc:Name></cac:Organization><!-- domstolsfällan -->
 <cac:Organization><cbc:ID schemeName="organization">ORG-0003</cbc:ID>
   <cbc:Name>Nordisk Städservice AB</cbc:Name></cac:Organization>
</ContractAwardNotice>"""

GAMMALT_XML = """<?xml version="1.0"?><TED_EXPORT>
 <AWARDED_CONTRACT><CONTRACTOR><OFFICIALNAME>Gamla Bolaget AB</OFFICIALNAME></CONTRACTOR>
 <VAL_TOTAL CURRENCY="SEK">1200000</VAL_TOTAL></AWARDED_CONTRACT></TED_EXPORT>"""

KOMMERS_HTML = """<html><head><title>Tilldelningsmeddelande - Städtjänster</title></head><body>
<select><option>03000000 Jordbruks- och trädgårdsprodukter</option><option>72000000 IT</option></select>
<script>var x = "Officellt namn FEJK";</script>
<div>Officellt namn</div><div>Gavlefastigheter Gävle kommun AB</div>
<div>Nationellt organisationsnummer</div><div>556009-8628</div>
<div>Typ av upphandlande myndighet eller enhet</div><div>Offentligt styrt organ</div>
<div>Titel</div><div>Städtjänster för skolfastigheter</div>
<div>Beskrivning</div><div>Lokalvård av kommunala fastigheter.</div>
<div>Huvudsaklig CPV-kod</div><div>90910000 Städning</div>
<div>Uppskattat värde</div><div></div>
<div>Avtalsperiodens start</div><div>2026-10-01</div>
<div>Avtalsperiodens slut</div><div>2028-09-30</div>
<div>Antal mottagna anbud</div><div>3 st</div>
<div>Lägsta anbud</div><div>2161940,0000</div>
<div>Högsta anbud</div><div>2161940,0000</div>
<div>Beslutsdatum</div><div>2026-07-15</div>
<div>Officiellt namn anbudsgivare</div><div>Renab Städ AB</div>
<div>Nationellt organisationsnummer</div><div>5560111222</div>
<div>Kontraktsdatum</div><div>2026-08-01</div>
<div>Ted annonsnummer</div>
<footer>© 2026 - Antirio</footer></body></html>"""

KOMMERS_AVBRUTEN = KOMMERS_HTML.replace(
    "Lokalvård av kommunala fastigheter.",
    "Upphandlingen avbryts på grund av bristande konkurrens.")

KOMMERS_EFORMS = "<html><head><title>Notice 12345</title></head><body>eForms</body></html>"

EAVROP_HTML = """<html><body><table>
<tr class="rowline"><td><a href="/e-Upphandling/Announcement.aspx?id=98765">Ramavtal snöröjning</a></td>
<td>2026-08-01</td><td>Ljusdal Energi Aktiebolag</td><td>90620000</td><td>2026-08-29</td></tr>
<tr class="rowline"><td><a href="/e-Upphandling/Announcement.aspx?id=98766">Skolskjuts 2027</a></td>
<td>2026-08-02</td><td>Härjedalens kommun</td><td>60130000</td><td>2026-09-15</td></tr>
</table></body></html>"""


def test_eforms():
    from consens.ingest.ted_tilldelningar import _parsa_eforms
    d = _parsa_eforms(ET.fromstring(EFORMS_XML))
    assert d["schema"] == "eforms"
    # Domstolsfällan: kedjan ger BARA den faktiska vinnaren
    assert d["leverantor"] == "Nordisk Städservice AB", d["leverantor"]
    assert "Förvaltningsrätten" not in (d["leverantor"] or "")
    # Ett unikt faktiskt belopp -> anbud
    assert d["varde"] == 4500000 and d["varde_sort"] == "anbud"
    assert d["startdatum"] == "2026-09-01" and d["slutdatum"] == "2029-08-31"
    print("  eForms: domstolsfällan, värde, period - ok")


def test_eforms_varderangordning():
    from consens.ingest.ted_tilldelningar import _parsa_eforms
    # Två OLIKA faktiska belopp -> faller till ramavtalstak
    xml = EFORMS_XML.replace(
        "</ContractAwardNotice>",
        """<cac:LotTender><cbc:ID schemeName="tender">TEN-0002</cbc:ID>
        <cac:X><cbc:ID schemeName="tendering-party">TPA-0001</cbc:ID></cac:X>
        <cac:LegalMonetaryTotal><cbc:PayableAmount currencyID="SEK">9900000</cbc:PayableAmount>
        </cac:LegalMonetaryTotal></cac:LotTender>
        <efext:OverallApproximateFrameworkContractsAmount currencyID="SEK">20000000
        </efext:OverallApproximateFrameworkContractsAmount></ContractAwardNotice>""")
    d = _parsa_eforms(ET.fromstring(xml))
    assert d["varde"] == 20000000 and d["varde_sort"] == "ramavtalstak", d
    print("  eForms: värderangordning anbud->ramavtalstak - ok")


def test_gammalt_schema():
    from consens.ingest.ted_tilldelningar import _hamta_xml_detaljer, _parsa_gammalt
    d = _parsa_gammalt(ET.fromstring(GAMMALT_XML))
    assert d["leverantor"] == "Gamla Bolaget AB" and d["varde"] == 1200000
    print("  Gammalt TED-schema - ok")


def test_rensa_titel():
    from consens.ingest.gemensamt import rensa_ted_titel
    t = "Sverige – IT-tjänster: konsultverksamhet – Ramavtal – teknisk konsult"
    en = rensa_ted_titel(t)
    assert en == "Ramavtal – teknisk konsult"
    assert rensa_ted_titel(en) == en, "idempotens bruten!"
    print("  TED-titelrensning idempotent - ok")


def test_kommers():
    from consens.ingest.kommers import tolka_detaljsida, _belopp, _heltal
    f = tolka_detaljsida(KOMMERS_HTML)
    assert f is not None
    # Dubbla orgnr-etiketter: sekventiell läsning skiljer köpare från vinnare
    assert f["kop_orgnr"].startswith("556009"), f["kop_orgnr"]
    assert f["vinnare_orgnr"].startswith("5560111"), f["vinnare_orgnr"]
    assert f["kop_namn"] == "Gavlefastigheter Gävle kommun AB"
    assert f["vinnare"] == "Renab Städ AB"
    # Sidfotsfällan: sista etiketten får INTE '© 2026 - Antirio'
    assert "Antirio" not in f.get("ted_nummer", "")
    # Select-skräpet borta: CPV är fältets, inte rullgardinens
    assert "90910000" in f["cpv"] and "03000000" not in f["cpv"]
    assert _belopp(f["lagsta_anbud"]) == 2161940
    assert _heltal(f["antal_anbud"]) == 3
    # Avbrutna hoppas; eForms-mallen hoppas
    assert tolka_detaljsida(KOMMERS_AVBRUTEN) is None
    assert tolka_detaljsida(KOMMERS_EFORMS) is None
    print("  Kommers: dubbla orgnr, sidfot, select, avbrutna, eForms-mall - ok")


def test_eavrop():
    from consens.ingest.e_avrop import tolka_lista
    rader = list(tolka_lista(EAVROP_HTML))
    assert len(rader) == 2
    assert rader[0]["kopare"] == "Ljusdal Energi Aktiebolag"
    assert rader[0]["cpv"] == "90620000" and rader[0]["deadline"] == "2026-08-29"
    print("  e-Avrop: radtolkning - ok")



# ===== Sinfra =====
SINFRA_LISTA = """<a href="/ramavtal/ramavtalsomraden/el-fiber/laddstationer/">El &amp; Fiber Laddstationer</a>
<a href="/ramavtal/ramavtalsomraden/vatten-avlopp/va-material/">Vatten &amp; Avlopp VA-material</a>
<a href="/ramavtal/ramavtalsomraden/">Ramavtalsomr&aring;den</a>"""
SINFRA_DETALJ = """<h1>Laddstationer</h1>
<h4>Avtalsperiod</h4><p>till&auml;mpligt fr&aring;n och med 2025-04-01 till och med 2027-03-31
med m&ouml;jlighet till ytterligare f&ouml;rl&auml;ngning som l&auml;ngst till 2033-03-31.</p>
<h4>Upphandlat enligt</h4><p>LUF</p>
<h4>Leverant&ouml;rer</h4><a href="https://a.se">Alfa AB</a><a href="https://b.se">Beta AB</a><footer></footer>"""


def test_sinfra():
    from consens.ingest.sinfra import tolka_lista, tolka_detalj
    rader = list(tolka_lista(SINFRA_LISTA))
    assert len(rader) == 2
    assert rader[0][0] == "el-fiber" and rader[0][1] == "Laddstationer"
    d = tolka_detalj(SINFRA_DETALJ)
    assert d["startdatum"] == "2025-04-01" and d["slutdatum"] == "2027-03-31"
    assert d["max_forlangning"] == "2033-03-31" and d["lagrum"] == "LUF"
    assert d["leverantorer"] == ["Alfa AB", "Beta AB"]


def test_kategorier():
    from consens.kategorier import NYCKELORD_CPV, gissa_cpv
    from consens.cpv import bransch_for_cpv
    for ord_, cpv in NYCKELORD_CPV:
        assert bransch_for_cpv(cpv), f"{cpv} saknar bransch"
    assert bransch_for_cpv(gissa_cpv("IT-tjänster")) == "IT och telekom"
    assert bransch_for_cpv(gissa_cpv("Tvättstugeutrustning")) == "Fastighet och drift"
    assert gissa_cpv("Yrkeskläder") is None


ADDA_DETALJ = """<h3>Avtalsperiod:</h3><p>2024-02-15 - 2028-02-28</p>
<h3>Leverant\u00f6rer</h3>
<button>Ahlsell Sverige AB <span>remove</span> <span>add</span></button>
<button>Uppf\u00f6ljning <span>remove</span> <span>add</span></button>
<button>Swedol AB <span>remove</span> <span>add</span></button>
<h3>Nyttor</h3>"""


def test_adda_detalj():
    from consens.ingest.adda import tolka_detalj
    d = tolka_detalj(ADDA_DETALJ)
    assert d["startdatum"] == "2024-02-15"
    assert d["leverantorer"] == ["Ahlsell Sverige AB", "Swedol AB"]

if __name__ == "__main__":
    test_eforms()
    test_eforms_varderangordning()
    test_gammalt_schema()
    test_rensa_titel()
    test_kommers()
    test_eavrop()
    print("Parsertester: alla gröna.")


CN_XML = """<?xml version="1.0"?>
<ContractNotice xmlns:cac="urn:x" xmlns:cbc="urn:y">
 <cbc:ProcedureCode>open</cbc:ProcedureCode>
 <cac:ProcurementProject>
   <cbc:Name>Projektledare</cbc:Name>
   <cbc:Description>Uppdraget avser projektledning vid flytt av verksamheter till nya lokaler.</cbc:Description>
   <cac:RequestedTenderTotal>
     <cbc:EstimatedOverallContractAmount currencyID="SEK">3500000</cbc:EstimatedOverallContractAmount>
   </cac:RequestedTenderTotal>
   <cac:RealizedLocation><cac:Address>
     <cbc:CountrySubentityCode>SE224</cbc:CountrySubentityCode>
   </cac:Address></cac:RealizedLocation>
 </cac:ProcurementProject>
</ContractNotice>"""


EAVROP_DETALJ_HTML = """<html><head><script>var skrap=1;</script></head><body>
<nav>Meny Hem Upphandlingar Logga in</nav>
<h1>Begagnad lätt lastbil</h1>
<table>
<tr><td>Upphandlande enhet</td><td>Karlshamns kommun</td></tr>
<tr><td>Organisationsnummer</td><td>212000-0845</td></tr>
<tr><td>Förfarande</td><td>Förenklat förfarande</td></tr>
<tr><td>Avtalstyp</td><td>Kontrakt</td></tr>
<tr><td>Sista anbudsdag</td><td>2026-08-12 23:59</td></tr>
<tr><td colspan="3">skräprad</td></tr>
</table>
<div>Karlshamns kommun avser att anskaffa en begagnad lätt lastbil med skåp
och bakgavellyft för verksamheten inom kost och städ. Fordonet ska vara
högst fem år gammalt, ha en maximal totalvikt om 3,5 ton och vara besiktigat
utan anmärkning. Leverans önskas senast åtta veckor efter avtalstecknande.</div>
<footer>© e-Avrop AB</footer>
</body></html>"""

ADDA_HTML = """<html><body>
<table>
<tr><th>Namn</th><th>Nuvarande avtalstid</th><th>Kategori</th></tr>
<tr><td>F</td><td></td><td></td></tr>
<tr><td><a href="/upphandling-och-ramavtal/ramavtal-och-avtalskategorier/fordon/fordon-2022/fordon-2022/">Fordon 2022chevron_right</a></td>
    <td>2026-11-23</td><td>Fordon 2022</td></tr>
<tr><td><a href="/upphandling-och-ramavtal/ramavtal-och-avtalskategorier/it-produkter-och-tjanster/it-konsulttjanster-2021/it-konsulttjanster-2021/">IT-konsulttjänster
2021<span class="icon">chevron_right</span></a></td>
    <td>2026-09-25</td><td>IT-konsulttjänster 2021</td></tr>
<tr><td><a href="/upphandling-och-ramavtal/ramavtal-och-avtalskategorier/bygg-och-fastighet/forskole--och-skolbyggnader-2025-2/">Förskole- och
skolbyggnader 2025-2<span>chevron_right</span></a></td>
    <td>2032-08-14</td><td></td></tr>
<tr><td><a href="/upphandling-och-ramavtal/ramavtal-och-avtalskategorier/lakemedel/vaccin-mot-covid-19-2027-2028/">Vaccin mot covid-19 2027-2028chevron_right</a></td>
    <td>-</td><td></td></tr>
</table>
</body></html>"""

HBV_HTML = """<html><body><table>
<tr><th>Ref.nr.</th><th>Ramavtal</th><th>Giltigt t.o.m</th></tr>
<tr><td>22-104</td><td><a href="https://contracts.tendsign.com/ContractArea/Details/1006387?eId=x">Rörmaterial</a></td><td>2026-09-18</td></tr>
<tr><td>24-131</td><td><a href="https://contracts.tendsign.com/ContractArea/Details/2631305?eId=x">Brandvarnare och
brandfiltar</a></td><td>2026-12-31*</td></tr>
<tr><td>21-155</td><td><a href="https://contracts.tendsign.com/ContractArea/Details/1002077?eId=x">Balkonginglasning</a></td><td>2026-08-18 (tillfälligt förlängningsavtal)</td></tr>
<tr><td>26-123</td><td><a href="https://contracts.tendsign.com/ContractArea/Details/3234703?eId=x">Byggmaterial</a></td><td>Se respektive avtalsdel</td></tr>
<tr><td></td><td><a href="https://contracts.tendsign.com/ContractArea/Details/999813?eId=x">Elkraft (flera ramavtal)</a></td><td>Se respektive avtalsdel</td></tr>
<tr><td>26-193</td><td><a href="https://hbv.se/25-193-hemforsakring/">Hemförsäkring allmännyttan</a></td><td></td></tr>
<tr><td>24-144</td><td><a href="https://contracts.tendsign.com/ContractArea/Details/2888096?eId=x">Skyltar (inomhus)</a></td><td>2028-06-16</td></tr>
<tr><td>24-144</td><td><a href="https://contracts.tendsign.com/ContractArea/Details/2888106?eId=x">Skyltar (utomhus)</a></td><td>2028-11-24</td></tr>
</table></body></html>"""
