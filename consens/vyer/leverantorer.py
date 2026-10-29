"""Leverantörsregistret - beräknat live ur avtalsdatan, som Insikter.

Kärnproblemet är namnbruset: samma bolag stavas olika mellan källor,
och ramavtal med flera vinnare lagrar alla namnen i EN kommaseparerad
sträng. Grupperingen splittrar flervinnarsträngar, nycklar på orgnr
när det finns och normaliserat namn annars, och visar det vanligaste
namnskicket utåt."""
import re
from datetime import date

from flask import (Blueprint, Response, abort, g, redirect,
                   render_template, request, url_for)

from ..cache import hamta as cache_hamta
from ..cpv import BRANSCHER, bransch_for_cpv
from ..db import get_db
from ..matchning import normalisera_foretagsnamn, normalisera_orgnr

bp = Blueprint("leverantorer", __name__)

PER_SIDA = 50


def _delar(text: str):
    """Flervinnarsträng -> enskilda bolagsnamn."""
    for del_ in re.split(r"[,;]", text or ""):
        del_ = del_.strip()
        if 2 < len(del_) <= 200:
            yield del_


def _slugga(namn: str) -> str:
    return re.sub(r"[^a-z0-9åäö]+", "-", namn.lower()).strip("-")[:80]


def leverantorer(db) -> dict:
    """Cachad ingång till registret - se bygg_leverantorer."""
    return cache_hamta("leverantorer", lambda: bygg_leverantorer(db))


def bygg_leverantorer(db) -> dict:
    """nyckel -> {namn, slug, orgnr, avtal: [rader], varde, orger,
    ramavtal: [(kallnamn, titel)]}. En genomgång av avtalstabellen."""
    import json
    grupper: dict = {}
    slug_till: dict = {}

    def grupp(namn, orgnr=None):
        norm = normalisera_foretagsnamn(namn.lower()) or namn.lower()
        nyckel = f"onr:{orgnr}" if orgnr else f"namn:{norm}"
        g = grupper.get(nyckel)
        if g is None:
            g = grupper[nyckel] = dict(
                namn={}, orgnr=orgnr, norm=norm, avtal=[], varde=0.0,
                orger=set(), ramavtal=[], nasta_utgang=None,
                branscher=set())
        g["namn"][namn] = g["namn"].get(namn, 0) + 1
        return g

    ramavtalskallor = ("adda", "avropa", "hbv", "sinfra")
    idag = date.today().isoformat()
    for rad in db.execute(
            "SELECT a.id, a.titel, a.kalla, a.leverantor, "
            "a.leverantor_orgnr, a.varde, a.slutdatum, a.cpv, a.detaljer, "
            "a.organisation_id, o.namn AS org_namn, o.orgnr AS org_orgnr "
            "FROM avtal a JOIN organisation o ON o.id=a.organisation_id "
            "WHERE a.leverantor IS NOT NULL OR a.detaljer IS NOT NULL"):
        namn_lista = list(_delar(rad["leverantor"] or ""))
        ar_ramavtal = any(k in (rad["kalla"] or "")
                          for k in ramavtalskallor)
        if ar_ramavtal and rad["detaljer"]:
            try:
                extra = json.loads(rad["detaljer"]).get("Leverantörer", "")
                namn_lista += [n for n in _delar(extra)
                               if n not in namn_lista]
            except Exception:
                pass
        if not namn_lista:
            continue
        orgnr = (normalisera_orgnr(rad["leverantor_orgnr"])
                 if len(namn_lista) == 1 else None)
        for namn in namn_lista:
            g = grupp(namn, orgnr if len(namn_lista) == 1 else None)
            if ar_ramavtal:
                g["ramavtal"].append((rad["kalla"], rad["titel"]))
                continue
            g["avtal"].append(rad)
            g["varde"] += rad["varde"] or 0
            g["orger"].add(rad["org_namn"])
            br = bransch_for_cpv(rad["cpv"] or "")
            if br:
                g["branscher"].add(br)
            if (rad["slutdatum"] and rad["slutdatum"] >= idag
                    and (g["nasta_utgang"] is None
                         or rad["slutdatum"] < g["nasta_utgang"])):
                g["nasta_utgang"] = rad["slutdatum"]

    # Slå ihop namn-nycklar med orgnr-nycklar när normen sammanfaller
    per_norm = {}
    for nyckel, g in list(grupper.items()):
        if nyckel.startswith("onr:"):
            per_norm.setdefault(g["norm"], nyckel)
    for nyckel in list(grupper):
        if not nyckel.startswith("namn:"):
            continue
        mal = per_norm.get(grupper[nyckel]["norm"])
        if mal and mal != nyckel:
            g, m = grupper.pop(nyckel), grupper[mal]
            for k, v in g["namn"].items():
                m["namn"][k] = m["namn"].get(k, 0) + v
            m["avtal"] += g["avtal"]
            m["varde"] += g["varde"]
            m["orger"] |= g["orger"]
            m["ramavtal"] += g["ramavtal"]
            m["branscher"] |= g["branscher"]
            if g["nasta_utgang"] and (m["nasta_utgang"] is None
                                      or g["nasta_utgang"]
                                      < m["nasta_utgang"]):
                m["nasta_utgang"] = g["nasta_utgang"]

    ut = {}
    for nyckel, g in grupper.items():
        if not g["avtal"] and not g["ramavtal"]:
            continue
        visning = max(g["namn"], key=g["namn"].get)
        slug = _slugga(visning)
        if slug in slug_till:  # krock: särskilj med orgnr-svans
            slug = f"{slug}-{(g['orgnr'] or 'x')[-4:]}"
        slug_till[slug] = nyckel
        g["visning"], g["slug"] = visning, slug
        ut[slug] = g
    return ut


@bp.route("/leverantorer")
def lista():
    from urllib.parse import urlencode
    db = get_db()
    sokt = bool(request.args.get("sok"))
    rader, totalt, sida, antal_sidor, bas_qs = [], 0, 1, 1, ""
    if sokt:
        q = request.args.get("q", "").strip()
        bransch = request.args.get("bransch", "")
        endast = request.args.get("endast", "")
        alla = list(leverantorer(db).values())
        if q:
            ql = q.lower()
            alla = [g for g in alla if ql in g["visning"].lower()
                    or (g["orgnr"] and ql in g["orgnr"])]
        if bransch:
            alla = [g for g in alla if bransch in g["branscher"]]
        if endast == "utgang":
            alla = [g for g in alla if g["nasta_utgang"]]
        elif endast == "ramavtal":
            alla = [g for g in alla if g["ramavtal"]]
        sortera = request.args.get("sortera") or "varde"
        nycklar = {
            "varde": lambda g: (-g["varde"], g["visning"]),
            "avtal": lambda g: (-len(g["avtal"]), g["visning"]),
            "namn": lambda g: g["visning"].lower(),
            "utgang": lambda g: (g["nasta_utgang"] is None,
                                 g["nasta_utgang"] or "", g["visning"]),
        }
        alla.sort(key=nycklar.get(sortera, nycklar["varde"]))
        totalt = len(alla)
        sida = max(1, request.args.get("sida", 1, type=int))
        antal_sidor = max(1, -(-totalt // PER_SIDA))
        sida = min(sida, antal_sidor)
        rader = alla[(sida - 1) * PER_SIDA: sida * PER_SIDA]
        bas_qs = urlencode([(k, v) for k, v
                            in request.args.items(multi=True)
                            if k != "sida"])
    return render_template(
        "leverantorer.html", rader=rader, totalt=totalt, sokt=sokt,
        sida=sida, antal_sidor=antal_sidor, bas_qs=bas_qs,
        branscher=sorted(BRANSCHER), valda=request.args)


def _inkop_per_namn(db) -> dict:
    """normaliserat leverantörsnamn -> summerad reskontra."""
    def bygg():
        per: dict = {}
        for r in db.execute("SELECT leverantor, belopp, antal, "
                            "organisation_id, ar FROM inkopsmonster"):
            n = normalisera_foretagsnamn((r["leverantor"] or "").lower())
            if not n:
                continue
            d = per.setdefault(n, dict(b=0.0, a=0, orger=set(),
                                       a1=9999, a2=0))
            d["b"] += r["belopp"] or 0
            d["a"] += r["antal"] or 0
            d["orger"].add(r["organisation_id"])
            d["a1"] = min(d["a1"], r["ar"] or 9999)
            d["a2"] = max(d["a2"], r["ar"] or 0)
        return {n: dict(b=d["b"], a=d["a"], o=len(d["orger"]),
                        a1=d["a1"], a2=d["a2"]) for n, d in per.items()}
    return cache_hamta("inkop_per_namn", bygg)


@bp.route("/leverantorer/<slug>")
def profil(slug):
    db = get_db()
    alla = leverantorer(db)
    grupp = alla.get(slug)
    if grupp is None:
        return render_template("404.html"), 404
    idag = date.today().isoformat()
    avtal = grupp["avtal"]
    fq = request.args.get("fq", "").strip().lower()
    fbransch = request.args.get("fbransch", "")
    if fq:
        avtal = [a for a in avtal if fq in (a["titel"] or "").lower()
                 or fq in (a["org_namn"] or "").lower()]
    if fbransch:
        avtal = [a for a in avtal
                 if bransch_for_cpv(a["cpv"] or "") == fbransch]
    kommande = sorted((a for a in avtal
                       if a["slutdatum"] and a["slutdatum"] >= idag),
                      key=lambda a: a["slutdatum"])
    ovriga = sorted((a for a in avtal if a not in kommande),
                    key=lambda a: a["slutdatum"] or "", reverse=True)
    branscher = {}
    for a in grupp["avtal"]:
        br = bransch_for_cpv(a["cpv"] or "")
        if br:
            branscher[br] = branscher.get(br, 0) + 1
    # Reskontra: orgnr först, normaliserat namn som reserv
    inkop = None
    if grupp["orgnr"]:
        inkop = db.execute(
            "SELECT SUM(belopp) AS b, SUM(antal) AS a, "
            "COUNT(DISTINCT organisation_id) AS o, MIN(ar) AS a1, "
            "MAX(ar) AS a2 FROM inkopsmonster WHERE leverantor_orgnr=?",
            (grupp["orgnr"],)).fetchone()
    if not inkop or not inkop["b"]:
        # Namnreserven byggs en gång och cachas: annars läses hela
        # inköpstabellen (44 000 poster) vid varje profilvisning.
        inkop = _inkop_per_namn(db).get(grupp["norm"])
    foljer = bool(g_anv() and db.execute(
        "SELECT 1 FROM bevakad_leverantor WHERE anvandare_id=? "
        "AND namn_norm=?", (g_anv()["id"], grupp["norm"])).fetchone())
    return render_template(
        "leverantor.html", lev=grupp, kommande=kommande,
        ovriga=ovriga[:50], foljer=foljer,
        branscher=sorted(branscher.items(), key=lambda x: -x[1])[:6],
        inkop=inkop, antal_ovriga=len(ovriga),
        alla_branscher=sorted(BRANSCHER), valda=request.args)


def g_anv():
    return getattr(g, "anvandare", None)


@bp.route("/leverantorer/<slug>/rapport.docx")
def rapport(slug):
    """Leverantörsprofilen som Word-dokument."""
    from ..rapport import bygg_leverantorsrapport
    resultat = bygg_leverantorsrapport(get_db(), slug)
    if resultat is None:
        abort(404)
    data, filnamn = resultat
    return Response(
        data,
        mimetype=("application/vnd.openxmlformats-officedocument"
                  ".wordprocessingml.document"),
        headers={"Content-Disposition":
                 f'attachment; filename="{filnamn}"'})


@bp.route("/leverantorer/<slug>/folj", methods=("POST",))
def folj(slug):
    """Växlar följning av en leverantör för inloggad användare."""
    db = get_db()
    anv = g_anv()
    if anv is None:
        return redirect(url_for("leverantorer.profil", slug=slug))
    grupp = leverantorer(db).get(slug)
    if grupp is None:
        return render_template("404.html"), 404
    bef = db.execute(
        "SELECT id FROM bevakad_leverantor WHERE anvandare_id=? "
        "AND namn_norm=?", (anv["id"], grupp["norm"])).fetchone()
    if bef:
        db.execute("DELETE FROM bevakad_leverantor WHERE id=?",
                   (bef["id"],))
    else:
        db.execute(
            "INSERT INTO bevakad_leverantor (anvandare_id, visningsnamn, "
            "namn_norm, orgnr, slug) VALUES (?,?,?,?,?)",
            (anv["id"], grupp["visning"], grupp["norm"], grupp["orgnr"],
             slug))
    db.commit()
    if request.form.get("nasta") == "bevakningar":
        return redirect(url_for("flode.bevakningar"))
    return redirect(url_for("leverantorer.profil", slug=slug))
