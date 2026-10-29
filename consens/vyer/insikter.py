"""Marknadsinsikter per bransch - beräknade ur egna tilldelningsdatan.

Leverantörsvinkeln: låg konkurrens är en öppning, inte ett problem.
Allt är rena aggregat över avtal-tabellen (antal anbud, leverantörer,
värden, löptider) - inga gissningar, ingen extern data, och trösklarna
för etiketterna redovisas öppet på sidan."""
import re
from datetime import date, timedelta

from flask import Blueprint, Response, abort, render_template

from ..cpv import bransch_for_cpv
from ..cache import hamta as cache_hamta
from ..db import get_db
from .auth import kraver_inloggning

bp = Blueprint("insikter", __name__)

MIN_UNDERLAG = 10  # branscher med färre tilldelningar visas inte


def berakna_insikter(db) -> list[dict]:
    idag = date.today().isoformat()
    horisont = (date.today() + timedelta(days=548)).isoformat()  # 18 mån
    grupper: dict[str, dict] = {}
    for rad in db.execute(
            "SELECT cpv, antal_anbud, leverantor, varde, startdatum, "
            "slutdatum, organisation_id FROM avtal WHERE cpv IS NOT NULL"):
        bransch = bransch_for_cpv(rad["cpv"])
        if not bransch:
            continue
        g = grupper.setdefault(bransch, dict(
            n=0, anbud=[], ett_anbud=0, per_leverantor={}, varde_tot=0.0,
            langder=[], utgar=0, utgar_orgar=set()))
        g["n"] += 1
        if rad["antal_anbud"]:
            g["anbud"].append(rad["antal_anbud"])
            if rad["antal_anbud"] == 1:
                g["ett_anbud"] += 1
        if rad["leverantor"] and rad["varde"]:
            g["varde_tot"] += rad["varde"]
            # Ramavtal med flera vinnare lagras som en kommaseparerad
            # sträng - dela värdet lika mellan bolagen så topplistan
            # visar företag, inte strängar.
            delar = [d.strip() for d in re.split(r"[,;]",
                     rad["leverantor"]) if 2 < len(d.strip()) <= 200]
            for del_ in delar:
                g["per_leverantor"][del_] = (
                    g["per_leverantor"].get(del_, 0)
                    + rad["varde"] / len(delar))
        if rad["startdatum"] and rad["slutdatum"]:
            man = ((int(rad["slutdatum"][:4]) - int(rad["startdatum"][:4]))
                   * 12 + int(rad["slutdatum"][5:7])
                   - int(rad["startdatum"][5:7]))
            if 0 < man <= 12 * 15:
                g["langder"].append(man)
        if rad["slutdatum"] and idag < rad["slutdatum"] <= horisont:
            g["utgar"] += 1
            g["utgar_orgar"].add(rad["organisation_id"])

    ut = []
    for bransch, g in grupper.items():
        if g["n"] < MIN_UNDERLAG:
            continue
        snitt = (sum(g["anbud"]) / len(g["anbud"])) if g["anbud"] else None
        andel_ett = (round(100 * g["ett_anbud"] / len(g["anbud"]))
                     if g["anbud"] else None)
        if snitt is None:
            lage, lage_klass = "Okänt konkurrensläge", "okand"
        elif snitt < 3 or (andel_ett or 0) >= 25:
            lage, lage_klass = "Låg konkurrens, öppning", "lag"
        elif snitt < 4.5:
            lage, lage_klass = "Måttlig konkurrens", "mattlig"
        else:
            lage, lage_klass = "Hård konkurrens", "hard"
        topp = sorted(g["per_leverantor"].items(), key=lambda x: -x[1])[:5]
        topp_andel = (round(100 * sum(v for _, v in topp) / g["varde_tot"])
                      if g["varde_tot"] else None)
        ut.append(dict(
            bransch=bransch, n=g["n"],
            snitt_anbud=round(snitt, 1) if snitt else None,
            andel_ett_anbud=andel_ett, lage=lage, lage_klass=lage_klass,
            topp=[(namn, round(100 * v / g["varde_tot"]))
                  for namn, v in topp] if g["varde_tot"] else [],
            topp_andel=topp_andel,
            medellangd=(round(sum(g["langder"]) / len(g["langder"]))
                        if g["langder"] else None),
            utgar=g["utgar"], utgar_orgar=len(g["utgar_orgar"]),
        ))
    ut.sort(key=lambda x: -x["n"])
    return ut


@bp.route("/insikter/<bransch>/rapport.docx")
@kraver_inloggning
def rapport(bransch):
    """Branschrapport som Word-dokument - samma underlag som sidan."""
    from ..rapport import bygg_rapport
    resultat = bygg_rapport(get_db(), bransch)
    if resultat is None:
        abort(404)
    data, filnamn = resultat
    return Response(
        data,
        mimetype=("application/vnd.openxmlformats-officedocument"
                  ".wordprocessingml.document"),
        headers={"Content-Disposition":
                 f'attachment; filename="{filnamn}"'})


@bp.route("/insikter")
@kraver_inloggning
def insikter():
    branscher_data = cache_hamta("insikter", lambda: berakna_insikter(get_db()))
    med_anbud = [b for b in branscher_data if b["snitt_anbud"]]
    rikssnitt = (round(sum(b["snitt_anbud"] * b["n"] for b in med_anbud)
                       / sum(b["n"] for b in med_anbud), 1)
                 if med_anbud else None)
    hogst = max((b["snitt_anbud"] for b in med_anbud), default=1) or 1
    stapel = sorted(med_anbud, key=lambda b: b["snitt_anbud"])
    for b in stapel:
        b["bredd"] = round(100 * b["snitt_anbud"] / hogst)
    riks_bredd = round(100 * rikssnitt / hogst) if rikssnitt else 0
    return render_template("insikter.html",
                           branscher=branscher_data,
                           stapel=stapel, rikssnitt=rikssnitt,
                           riks_bredd=riks_bredd,
                           min_underlag=MIN_UNDERLAG)
