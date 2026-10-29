"""Avtalsutgångsmotorn: gör avtal till framåtblickande signaler.

Den enda helt förutsägbara köpsignalen - övriga källor hittar sådant som
redan hänt, den här hittar sådant som SKA hända. Inget CPV-filter: allt tas.

Dubbletthanteringen är extern_id-baserad (utgang:{avtalets extern_id}), så
kor_kalla-ramverket sköter att varje avtal bara blir en signal en gång -
ingen flaggkolumn behövs.
"""
from datetime import date

from ..db import get_db
from .bas import kor_kalla

FONSTER_MANADER = 18   # täcker UI-filtret 12-18 månader
STORT_BELOPP = 50_000_000


def _sammanfattning(a, man_kvar: int) -> str:
    delar = [f"Avtalet beräknas löpa ut {a['slutdatum']}, "
             f"om cirka {man_kvar} månader."]
    if a["leverantor"]:
        delar.append(f"Nuvarande leverantör: {a['leverantor']}.")
    if a["antal_anbud"]:
        delar.append(f"{a['antal_anbud']} anbud i ursprunglig upphandling.")
    delar.append("Slutdatumet kommer från källan och kan ha förlängts "
                 "genom optioner.")
    return " ".join(delar)


def hamta():
    db = get_db()
    idag = date.today()
    grans = date.fromordinal(idag.toordinal() + round(FONSTER_MANADER * 30.44))

    rader = db.execute(
        "SELECT a.*, o.namn AS org_namn, o.lan AS org_lan FROM avtal a "
        "JOIN organisation o ON o.id = a.organisation_id "
        "WHERE a.slutdatum IS NOT NULL AND a.slutdatum > ? AND a.slutdatum <= ? "
        "ORDER BY a.slutdatum",
        (idag.isoformat(), grans.isoformat())).fetchall()

    for a in rader:
        a = dict(a)
        man_kvar = round((date.fromisoformat(a["slutdatum"]) - idag).days / 30.44)
        hog = man_kvar <= 3 or (a["varde"] or 0) >= STORT_BELOPP
        yield dict(
            typ="avtalsutgang",
            organisation_id=a["organisation_id"],
            avtal_id=a["id"],
            titel=f"{a['org_namn']} — {a['titel']} löper ut"[:500],
            sammanfattning=_sammanfattning(a, man_kvar),
            varde=a["varde"], varde_sort=a["varde_sort"],
            cpv=a["cpv"], lan=a["org_lan"],
            kalla=a["kalla"],
            extern_id=f"utgang:{a['extern_id']}",
            url=a["url"],
            deadline=a["slutdatum"],
            prioritet="hog" if hog else "normal",
        )


def kor(torr=False):
    return kor_kalla("avtalsutgangar", hamta, torr=torr)
