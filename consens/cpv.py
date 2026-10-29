"""CPV-hantering.

Bransch är ett användarvänligt skal ovanpå CPV: varje bransch definieras
som en lista CPV-prefix. Signaler klassificeras aldrig dubbelt — bransch
härleds alltid ur CPV-koden. Detta är inversionen av Nodpunkts hårdkodade
IT-filter: samma tabell, men prefixen är användarens val i stället för
systemets gräns.
"""

# Bransch -> CPV-prefix (matchar på kodens början).
BRANSCHER = {
    "Avfall och återvinning": ["905"],
    "Bygg och anläggning": ["45"],
    "Energi och VA": ["09", "65", "31"],
    "Fastighet och drift": ["70", "50700"],
    "Finans och försäkring": ["66"],
    "IT och telekom": ["48", "72", "302", "325"],
    "Konsulttjänster": ["71", "73", "794"],
    "Kontor och inredning": ["301", "391", "228", "798"],
    "Kultur och fritid": ["92"],
    "Livsmedel och måltid": ["15", "55"],
    "Medicinteknik och läkemedel": ["33"],
    "Park och grönyta": ["77"],
    "Städ och FM-tjänster": ["909", "907"],
    "Säkerhet och bevakning": ["797"],
    "Transport och fordon": ["34", "60"],
    "Utbildning": ["80"],
    "Vård och omsorg": ["85"],
}


def bransch_for_cpv(cpv: str) -> str | None:
    """Härled bransch ur en CPV-kod. Längsta matchande prefix vinner."""
    if not cpv:
        return None
    basta, basta_langd = None, 0
    for bransch, prefixar in BRANSCHER.items():
        for p in prefixar:
            if cpv.startswith(p) and len(p) > basta_langd:
                basta, basta_langd = bransch, len(p)
    return basta


def prefix_for_bransch(bransch: str) -> list[str]:
    return BRANSCHER.get(bransch, [])
