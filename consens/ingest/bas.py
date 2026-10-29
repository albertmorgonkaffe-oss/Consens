"""Ingest-ramverket: kor_kalla + körningshistorik + torrkörning.

Varje källa är en modul med en funktion `hamta()` som returnerar en lista
signal-dicts. Ramverket sköter dubbletthantering (extern_id), loggning
och felisolering — en källas krasch stoppar aldrig de andra.
"""
from datetime import datetime

from flask import current_app

from ..db import get_db
from ..kategorier import gissa_cpv
from ..yrken import tagga_yrken

SIGNALFALT = (
    "typ", "organisation_id", "avtal_id", "titel", "sammanfattning",
    "varde", "varde_sort", "cpv", "lan", "kalla", "extern_id",
    "deadline", "prioritet", "url", "publicerad", "detaljer",
    "upphandlings_id",
)


def kor_kalla(namn: str, hamta_fn, torr: bool = False) -> dict:
    """Kör en källa. torr=True skriver ingenting — bara räknar och loggar."""
    db = get_db()
    kid = db.execute(
        "INSERT INTO korningshistorik (kalla, startad) VALUES (?, ?)",
        (namn, datetime.now().isoformat(timespec="seconds")),
    ).lastrowid
    db.commit()

    nya = dubbletter = 0
    try:
        for rad in hamta_fn():
            fanns = db.execute(
                "SELECT 1 FROM signal WHERE extern_id = ?", (rad["extern_id"],)
            ).fetchone()
            if fanns:
                dubbletter += 1
                continue
            nya += 1
            if torr:
                current_app.logger.info("[torr] ny signal: %s", rad["extern_id"])
                continue
            rad = dict(rad, korning_id=kid)
            if rad.get("typ") == "upphandling" and "upphandlings_id" not in rad:
                from ..matchning import extrahera_referens
                ref = extrahera_referens(rad.get("detaljer"))
                if ref:
                    rad["upphandlings_id"] = ref
            falt = [f for f in SIGNALFALT if f in rad] + ["korning_id"]
            sid = db.execute(
                f"INSERT INTO signal ({', '.join(falt)}) "
                f"VALUES ({', '.join('?' * len(falt))})",
                [rad[f] for f in falt],
            ).lastrowid
            if rad.get("upphandlings_id"):
                from ..matchning import koppla_signal_dubblett
                koppla_signal_dubblett(db, sid, rad.get("organisation_id"),
                                       rad["upphandlings_id"])
            if not rad.get("cpv"):
                # Planer, budgetar och protokoll saknar CPV i källan.
                # Utan kod hamnar de utanför bransch- och yrkesfiltren,
                # så vi gissar en representativ kod ur titeln.
                gissad = gissa_cpv(rad.get("titel"))
                if gissad:
                    rad["cpv"] = gissad
            for yrke in tagga_yrken(rad.get("titel"), rad.get("cpv")):
                db.execute(
                    "INSERT OR IGNORE INTO signal_yrke VALUES (?, ?)",
                    (sid, yrke),
                )
            db.commit()  # per rad: håll aldrig låset över nästa nätanrop
        status, fel = "ok", None
    except Exception as e:  # felisolering: logga, släpp inte vidare
        status, fel = "fel", f"{type(e).__name__}: {e}"
        current_app.logger.exception("Källan %s kraschade", namn)

    db.execute(
        "UPDATE korningshistorik SET avslutad=?, antal_nya=?, "
        "antal_dubbletter=?, status=?, fel=? WHERE id=?",
        (datetime.now().isoformat(timespec="seconds"),
         nya, dubbletter, status, fel, kid),
    )
    if not torr:
        db.commit()
    else:
        db.commit()  # historikraden ska sparas även vid torrkörning

    return {"kalla": namn, "nya": nya, "dubbletter": dubbletter,
            "status": status, "torr": torr}


AVTALSFALT = (
    "organisation_id", "titel", "leverantor", "leverantor_orgnr", "varde",
    "varde_sort", "antal_anbud", "startdatum", "slutdatum", "cpv",
    "kalla", "extern_id", "url", "detaljer",
)


def kor_avtalskalla(namn: str, hamta_fn, torr: bool = False) -> dict:
    """Som kor_kalla, men för källor som producerar AVTAL (tilldelningar).
    Avtalen taggas med yrken direkt; avtalsutgangar.py gör dem till signaler."""
    db = get_db()
    kid = db.execute(
        "INSERT INTO korningshistorik (kalla, startad) VALUES (?, ?)",
        (namn, datetime.now().isoformat(timespec="seconds")),
    ).lastrowid
    db.commit()

    nya = dubbletter = 0
    try:
        for rad in hamta_fn():
            fanns = db.execute(
                "SELECT 1 FROM avtal WHERE extern_id = ?", (rad["extern_id"],)
            ).fetchone()
            if fanns:
                dubbletter += 1
                continue
            nya += 1
            if torr:
                current_app.logger.info("[torr] nytt avtal: %s", rad["extern_id"])
                continue
            if not rad.get("cpv"):
                from ..kategorier import gissa_cpv
                rad["cpv"] = gissa_cpv(rad.get("titel"))
            falt = [f for f in AVTALSFALT if f in rad]
            aid = db.execute(
                f"INSERT INTO avtal ({', '.join(falt)}) "
                f"VALUES ({', '.join('?' * len(falt))})",
                [rad[f] for f in falt],
            ).lastrowid
            for yrke in tagga_yrken(rad.get("titel"), rad.get("cpv")):
                db.execute("INSERT OR IGNORE INTO avtal_yrke VALUES (?, ?)",
                           (aid, yrke))
            db.commit()
        status, fel = "ok", None
    except Exception as e:
        status, fel = "fel", f"{type(e).__name__}: {e}"
        current_app.logger.exception("Källan %s kraschade", namn)

    db.execute(
        "UPDATE korningshistorik SET avslutad=?, antal_nya=?, "
        "antal_dubbletter=?, status=?, fel=? WHERE id=?",
        (datetime.now().isoformat(timespec="seconds"),
         nya, dubbletter, status, fel, kid),
    )
    db.commit()
    return {"kalla": namn, "nya": nya, "dubbletter": dubbletter,
            "status": status, "torr": torr}
