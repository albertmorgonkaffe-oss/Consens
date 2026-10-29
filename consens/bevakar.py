"""Fördjupningssidor bakom korten i "Vad vi bevakar" på startsidan.

Varje signaltyp får en egen sida som förklarar vad den är, varför den
spelar roll och hur den ser ut i tjänsten. Sidorna nås bara via korten,
inte via menyn, och de är medvetet konkreta: en signaltyp, ett exempel,
en produktvy.
"""

SIDOR = {
    "avtalsutgang": dict(
        rubrik="Avtalsutgång",
        ingress=("Ett avtal som löper ut är en affär som öppnar sig. "
                 "Consens räknar fram slutdatumet ur tilldelningens "
                 "löptid och lägger avtalet i flödet i god tid innan "
                 "omupphandlingen annonseras."),
        punkter=[
            "Varje tilldelat avtal sparas med vinnare, värde, startdatum och slutdatum.",
            "När slutet närmar sig dyker avtalet upp i flödet, ofta ett år före annonsen.",
            "Löptidsmätaren visar hur stor del av avtalstiden som gått och hur många månader som är kvar.",
            "Filtrera på tid till utgång så ser du bara det som är aktuellt för din säljcykel.",
        ],
        vy="flode", tagg="tagg-utgang", tagg_text="Avtalsutgång",
        kalla="Beräknad ur tilldelningsdata",
        titel="Livsmedel och specialkost löper ut",
        text=("Slutdatum <b>2027-05-31</b>, om cirka 10 månader. Nuvarande "
              "leverantör: Martin &amp; Servera Restauranghandel AB. Värde 42,0 mnkr."),
        matare=True, chips=["Livsmedel och måltid", "Halmstads kommun →"]),
    "upphandling": dict(
        rubrik="Upphandling",
        ingress=("Den pågående annonsen, hämtad samma dygn som den "
                 "publiceras, med beskrivning, värde, förfarande och "
                 "sista anbudsdag."),
        punkter=[
            "Källor: EU:s annonsdatabas TED samt de svenska annonsdatabaserna.",
            "Hela annonsen följer med, inte bara rubriken, och länken går till källan.",
            "Filtrera på bransch, tjänst, län och tid till deadline.",
            "Spara filtret som bevakning så kommer nya träffar med mejl varje morgon.",
        ],
        vy="flode", tagg="tagg-upphandling", tagg_text="Upphandling",
        kalla="Källa: TED",
        titel="Skolskjuts och särskild kollektivtrafik",
        text=("Sista anbudsdag <b>2026-10-03</b>. Uppskattat värde 96,0 mnkr. "
              "Tre delområden, avtal på fyra år med option på två."),
        chips=["Transport och fordon", "Trafikplanerare", "Norrköpings kommun →"]),
    "tilldelning": dict(
        rubrik="Tilldelning",
        ingress=("Vem vann, till vilket värde och hur många som lämnade "
                 "anbud. Tilldelningen är slutet på en upphandling och "
                 "startpunkten för nästa: den säger när avtalet tar slut "
                 "och vem du möter när det gör det."),
        punkter=[
            "Över 23 000 tilldelade avtal med leverantör, värde, löptid och antal anbud.",
            "Ramavtal med flera vinnare delas upp, så varje företag syns för sig.",
            "Varje tilldelning blir en framtida avtalsutgång i flödet.",
            "Klicka på leverantören för att se allt annat företaget vunnit.",
        ],
        vy="avtal"),
    "svag-konkurrens": dict(
        rubrik="Svag konkurrens",
        ingress=("Fick avtalet bara ett eller två anbud? Då är marknaden "
                 "svagt bearbetad, och chansen att vinna nästa gång är "
                 "större än i ett område med tio anbudsgivare. Consens "
                 "räknar ut det per bransch."),
        punkter=[
            "Genomsnittligt antal anbud och andel upphandlingar som avgjordes med ett enda anbud.",
            "Leverantörskoncentration: hur stor del av kontraktsvärdet som ligger hos de fem största.",
            "Bedömningen visas som låg, måttlig eller hård konkurrens, med underlaget utskrivet.",
            "Antal anbud saknas i källdata för en del kontrakt, och de räknas då inte in.",
        ],
        vy="insikter"),
    "planerad-upphandling": dict(
        rubrik="Planerad upphandling",
        ingress=("Innan annonsen finns beslutet. Kommuner och regioner "
                 "publicerar sina upphandlingsplaner, sina "
                 "investeringsbudgetar och sina nämndbeslut, och där syns "
                 "affären ofta ett till tre år i förväg."),
        punkter=[
            "Kommunala och regionala upphandlingsplaner med planerad period.",
            "Beslutade investeringar ur budgetdokument, med belopp och planperiod.",
            "Politiska beslutsärenden ur nämndernas och fullmäktiges handlingar.",
            "Inköpscentralernas planer, inklusive avtalsområden som ännu bara utreds.",
            "I det här skedet går kravbilden fortfarande att påverka genom dialog.",
        ],
        vy="flode", tagg="tagg-planerad", tagg_text="Planerad upphandling",
        kalla="Kommunens beslutsunderlag",
        titel="Om- och tillbyggnad av reningsverk",
        text=("Upptagen i kommunens investeringsplan 2026 till 2028 med "
              "210 mnkr. Beslutad budget, upphandling följer senare i processen."),
        chips=["Energi och VA", "Gävle kommun →"]),
    "konkurrentens-avtal": dict(
        rubrik="Konkurrentens avtal",
        ingress=("Över 17 000 företag som vunnit svenska offentliga "
                 "kontrakt, med kunder, ramavtalsplatser och "
                 "utgångsdatum. Slå upp vem som helst, eller följ dem och "
                 "få besked när något händer."),
        punkter=[
            "Vunna kontrakt, samlat värde, antal offentliga kunder och branschtyngdpunkt.",
            "Avtal som löper ut, sorterade närmast först: där står nästa affär.",
            "Platser på inköpscentralernas ramavtal hos Adda, Statens inköpscentral, HBV och Sinfra.",
            "Följ ett företag så mejlar vi när deras avtal närmar sig utgång eller när de vinner nytt.",
            "Hela profilen kan laddas ner som rapport i Word.",
        ],
        vy="leverantor"),
    "marknadsinsikter": dict(
        rubrik="Marknadsinsikter",
        ingress=("Konkurrensläge, andel upphandlingar med ett enda anbud "
                 "och leverantörskoncentration för tjugo branscher, "
                 "räknat ur tilldelningsdata i stället för gissningar."),
        punkter=[
            "Anbudssnitt, enanbudsandel, topp fem-koncentration och kommande utgångar per bransch.",
            "Största leverantörerna med andel av kontraktsvärdet, klickbara till registret.",
            "Ladda ner en bransch eller ett enskilt företag som färdig rapport i Word.",
            "Uppdateras med varje hämtning, så siffrorna följer marknaden.",
        ],
        vy="insikter"),
    "vad-kommunen-betalar": dict(
        rubrik="Vad kommunen faktiskt betalar",
        ingress=("Ett ramavtalstak säger vad kunden får handla för. "
                 "Leverantörsreskontran säger vad de handlar för. För de "
                 "organisationer som publicerar sina inköpsdata visar vi "
                 "fakturerade belopp per leverantör och år."),
        punkter=[
            "Aggregerade betalningar per leverantör, år och organisation.",
            "Visar vilka leverantörer som verkligen används, inte bara vilka som står på avtalet.",
            "Syns både på organisationssidan och i leverantörsprofilen.",
            "Bygger på kommunernas egna öppna data, och täckningen växer med antalet som publicerar.",
        ],
        vy="inkop"),
}
