"""Minnescache för beräkningar som är dyra men sällan ändras.

Leverantörsregistret grupperar om 23 000 avtal vid varje sidvisning och
Insikter räknar om konkurrensläget per bransch. Bägge bygger på data som
bara ändras när källorna körs (06:30 och 12:00) - alltså är resultatet
identiskt tusentals sidvisningar i rad.

Cachen är medvetet enkel: process-lokal dict, tidsgräns, plus en
generationsräknare som binder posterna till databasens innehåll. När en
körning skriver nya signaler eller avtal ändras generationen och allt
cachat blir ogiltigt utan att någon behöver komma ihåg att rensa.
"""
import threading
import time

from .db import get_db

STANDARD_LIVSLANGD = 600  # sekunder

_las = threading.Lock()
_poster: dict = {}


def _generation(db) -> str:
    """Fingeravtryck av databasens innehåll: högsta id i de tabeller
    beräkningarna läser. Räknas på indexerade kolumner och tar
    mikrosekunder, till skillnad från beräkningarna vi slipper."""
    rad = db.execute(
        "SELECT (SELECT COALESCE(MAX(id), 0) FROM signal) AS s, "
        "(SELECT COALESCE(MAX(id), 0) FROM avtal) AS a, "
        "(SELECT COALESCE(MAX(id), 0) FROM organisation) AS o, "
        "(SELECT COALESCE(MAX(id), 0) FROM inkopsmonster) AS i"
    ).fetchone()
    return f"{rad['s']}:{rad['a']}:{rad['o']}:{rad['i']}"


def hamta(nyckel: str, bygg, livslangd: int = STANDARD_LIVSLANGD):
    """Returnera cachat värde för nyckeln, annars bygg det och spara.

    bygg() anropas utanför låset så att en långsam beräkning inte
    blockerar andra läsare (värsta fallet: två samtidiga byggen första
    gången, vilket är ofarligt)."""
    db = get_db()
    gen = _generation(db)
    nu = time.time()
    with _las:
        post = _poster.get(nyckel)
        if post and post["generation"] == gen and post["giltig_till"] > nu:
            return post["varde"]
    varde = bygg()
    with _las:
        _poster[nyckel] = dict(varde=varde, generation=gen,
                               giltig_till=nu + livslangd)
    return varde


def rensa(nyckel: str | None = None) -> None:
    """Töm cachen helt eller för en nyckel (används av tester och
    admin-knappen)."""
    with _las:
        if nyckel is None:
            _poster.clear()
        else:
            _poster.pop(nyckel, None)


def status() -> list[dict]:
    """Vad ligger i cachen just nu - för adminöversikten."""
    nu = time.time()
    with _las:
        return [dict(nyckel=k, sekunder_kvar=max(0, int(v["giltig_till"] - nu)),
                     generation=v["generation"])
                for k, v in sorted(_poster.items())]
