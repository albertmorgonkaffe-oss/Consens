"""E-postdigest för sparade bevakningar.

Körs som cron efter morgonens hämtningar:
    FLASK_APP=consens flask skicka-digest            # dagliga
    FLASK_APP=consens flask skicka-digest --veckovis # + veckovisa (måndagar)

SMTP konfigureras med miljövariabler (annars loggas digesten bara):
    CONSENS_SMTP_HOST, CONSENS_SMTP_PORT (587),
    CONSENS_SMTP_ANVANDARE, CONSENS_SMTP_LOSENORD,
    CONSENS_SMTP_FRAN (t.ex. "Consens <digest@consens.se>")
"""
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import parse_qsl

import click
from werkzeug.datastructures import MultiDict

from .db import get_db


def _skicka(till: str, amne: str, text: str, typ: str = "digest",
            anvandare_id=None, html: str | None = None) -> bool:
    _logga_utskick(till, amne, typ, anvandare_id)
    return _skicka_ratt(till, amne, text, html)


def _skicka_resend(nyckel: str, till: str, amne: str, text: str,
                   html: str | None = None) -> bool:
    import json as _json
    import urllib.error
    import urllib.request
    innehall = {
        "from": os.environ.get("CONSENS_FRAN", "Consens <info@consens.se>"),
        "to": [till], "subject": amne, "text": text,
    }
    if html:
        innehall["html"] = html
    data = _json.dumps(innehall).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=data, method="POST",
        headers={"Authorization": f"Bearer {nyckel}",
                 "Content-Type": "application/json",
                 "User-Agent": "Consens/1.0 (+https://consens.se)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as svar:
            return 200 <= svar.status < 300
    except urllib.error.HTTPError as e:
        try:
            kropp = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            kropp = ""
        print(f"[resend-fel] {till} | {amne}: {e} | {kropp}")
        return False
    except Exception as e:
        print(f"[resend-fel] {till} | {amne}: {e}")
        return False


def _logga_utskick(till, amne, typ, anvandare_id):
    try:
        from .db import get_db
        db = get_db()
        if anvandare_id is None:
            rad = db.execute("SELECT id FROM anvandare WHERE epost=?",
                             (till,)).fetchone()
            anvandare_id = rad["id"] if rad else None
        db.execute("INSERT INTO utskickslogg (anvandare_id, epost, typ, amne) "
                   "VALUES (?,?,?,?)", (anvandare_id, till, typ, amne))
        db.commit()
    except Exception:
        pass  # loggning får aldrig stoppa ett utskick


def _skicka_ratt(till: str, amne: str, text: str,
                 html: str | None = None) -> bool:
    # 1) Resend om nyckel finns, 2) annars SMTP, 3) annars loggutskrift
    resend_nyckel = os.environ.get("CONSENS_RESEND_KEY")
    if resend_nyckel:
        return _skicka_resend(resend_nyckel, till, amne, text, html)
    host = os.environ.get("CONSENS_SMTP_HOST")
    if not host:
        print(f"[digest utan SMTP] Till: {till} | {amne}\n{text}\n")
        return True
    msg = EmailMessage()
    msg["From"] = os.environ.get("CONSENS_SMTP_FRAN", "Consens <noreply@consens.se>")
    msg["To"] = till
    msg["Subject"] = amne
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(host, int(os.environ.get("CONSENS_SMTP_PORT", 587))) as s:
        s.starttls()
        anv = os.environ.get("CONSENS_SMTP_ANVANDARE")
        if anv:
            s.login(anv, os.environ.get("CONSENS_SMTP_LOSENORD", ""))
        s.send_message(msg)
    return True


MAX_PER_MAIL = 20

VECKODAGAR = ("måndag", "tisdag", "onsdag", "torsdag",
              "fredag", "lördag", "söndag")


def _hogtid(nu) -> str | None:
    """Hälsningsstart vid högtid, annars None. Midsommarafton är
    fredagen 19-25 juni."""
    m, d = nu.month, nu.day
    if (m, d) == (10, 31):
        return "Trevlig halloween"
    if m == 12 and 23 <= d <= 26:
        return "God jul"
    if (m, d) in ((12, 31), (1, 1)):
        return "Gott nytt år"
    if (m, d) == (12, 13):
        return "Trevlig lucia"
    if (m, d) == (4, 30):
        return "Glad valborg"
    if (m, d) == (6, 6):
        return "Glad nationaldag"
    if m == 6 and 19 <= d <= 25 and nu.weekday() == 4:
        return "Glad midsommar"
    return None


def _halsning(namn: str | None, nu=None) -> str:
    """Varierad hälsningsfras: högtider vinner, annars slumpas bland
    tid- och dagsanpassade varianter."""
    import random
    nu = nu or datetime.now()
    fornamn = (namn or "").strip().split(" ")[0] if namn else ""
    hogtid = _hogtid(nu)
    if hogtid:
        start = hogtid
    else:
        val = ["Hej", f"God {VECKODAGAR[nu.weekday()]}"]
        if nu.hour < 10:
            val.append("God morgon")
        if nu.weekday() == 0:
            val.append("Välkommen tillbaka")
        start = random.choice(val)
    if fornamn:
        return (f"{start} {fornamn}!" if start == "Hej"
                else f"{start}, {fornamn}!")
    return f"{start}!"


def _mest_relevanta(traffar, tak: int):
    """De mest relevanta träffarna: högt värde och nära deadline lyfts,
    resten faller på plats efter publiceringsordning."""
    def nyckel(s):
        varde = s["varde"] or 0
        deadline = s["deadline"] or "9999"
        return (-(varde > 0), -varde, deadline)
    return sorted(traffar, key=nyckel)[:tak]


def _kvartal(datum) -> str:
    if not datum or len(str(datum)) < 7:
        return ""
    return f"Q{(int(str(datum)[5:7]) - 1) // 3 + 1} {str(datum)[:4]}"


def _rad_html(s, bas_url: str) -> str:
    import html as _h
    org = _h.escape(s["org_namn"]) if s.get("org_namn") else ""
    lank = s.get("url") or f"{bas_url}/flode"
    varde = ""
    if s["varde"]:
        mnkr = s["varde"] / 1_000_000
        varde = f" · ca {mnkr:,.1f} mnkr".replace(",", " ").replace(".", ",")
    if s["typ"] == "avtalsutgang":
        meta = (f"Avtalsutgång · {s['man_kvar']} mån kvar"
                if s.get("man_kvar") is not None else "Avtalsutgång")
    elif s["typ"] == "planerad":
        kv = _kvartal(s.get("deadline"))
        meta = (f"Planerad upphandling · start {kv}" if kv
                else "Planerad upphandling")
    else:
        meta = f"Upphandling · sista anbudsdag {s['deadline'] or 'okänd'}"
    return (f'<p style="margin:0 0 14px">'
            f'<a href="{_h.escape(lank, quote=True)}" '
            f'style="color:#1D5FDB;font-weight:600;text-decoration:none">'
            f'{_h.escape(s["titel"])}</a><br>'
            f'<span style="color:#5A6472;font-size:13px">{org}'
            f'{" · " if org else ""}{meta}{varde}</span></p>')


def _rad(s) -> str:
    org = f" — {s['org_namn']}" if s.get("org_namn") else ""
    varde = ""
    if s["varde"]:
        mnkr = s["varde"] / 1_000_000
        varde = f" · ca {mnkr:,.1f} mnkr".replace(",", " ").replace(".", ",")
    lank = f"\n  {s['url']}" if s.get("url") else ""
    if s["typ"] == "planerad":
        kv = _kvartal(s.get("deadline"))
        rad2 = (f"Planerad upphandling · start {kv}" if kv
                else "Planerad upphandling")
        return f"• {s['titel']}{org}\n  {rad2}{varde}{lank}"
    if s["typ"] == "avtalsutgang":
        tid = (f"{s['man_kvar']} mån kvar"
               if s.get("man_kvar") is not None else "")
        return f"• {s['titel']}{org}\n  Avtalsutgång · {tid}{varde}{lank}"
    return (f"• {s['titel']}{org}\n"
            f"  Upphandling · sista anbudsdag {s['deadline'] or 'okänd'}"
            f"{varde}{lank}")


MAX_NOTISRADER = 15


def _leverantorsnotiser(db, bas_url: str) -> int:
    """Ett mejl per användare med bevakade leverantörer, när något hänt:
    (1) ett av leverantörens avtal löper ut inom 12 månader, eller
    (2) leverantören vunnit ett nytt kontrakt (färsk utgångssignal).
    Varje händelse notifieras EN gång per användare (leverantorsnotis)."""
    import html as _h
    import re as _re

    from .matchning import normalisera_foretagsnamn, normalisera_orgnr
    anvandare = db.execute(
        "SELECT DISTINCT u.id, u.epost, u.namn FROM bevakad_leverantor bl "
        "JOIN anvandare u ON u.id=bl.anvandare_id WHERE u.aktiv=1"
    ).fetchall()
    if not anvandare:
        return 0

    def indexera(rader):
        per_norm, per_onr = {}, {}
        for rad in rader:
            for del_ in _re.split(r"[,;]", rad["leverantor"] or ""):
                del_ = del_.strip()
                if not 2 < len(del_) <= 200:
                    continue
                n = normalisera_foretagsnamn(del_.lower())
                if n:
                    per_norm.setdefault(n, []).append(rad)
            onr = normalisera_orgnr(rad["leverantor_orgnr"])
            if onr:
                per_onr.setdefault(onr, []).append(rad)
        return per_norm, per_onr

    utgar = db.execute(
        "SELECT a.id, a.titel, a.leverantor, a.leverantor_orgnr, a.varde, "
        "a.slutdatum, o.namn AS org_namn FROM avtal a "
        "JOIN organisation o ON o.id=a.organisation_id "
        "WHERE a.leverantor IS NOT NULL AND a.slutdatum BETWEEN "
        "date('now') AND date('now', '+12 months')").fetchall()
    nya = db.execute(
        "SELECT a.id, a.titel, a.leverantor, a.leverantor_orgnr, a.varde, "
        "a.slutdatum, o.namn AS org_namn FROM avtal a "
        "JOIN signal s ON s.extern_id = 'utgang:' || a.extern_id "
        "JOIN organisation o ON o.id=a.organisation_id "
        "WHERE a.leverantor IS NOT NULL "
        "AND s.upptackt >= date('now', '-8 day')").fetchall()
    utgar_norm, utgar_onr = indexera(utgar)
    nya_norm, nya_onr = indexera(nya)

    skickade = 0
    for anv in anvandare:
        bevakade = db.execute(
            "SELECT * FROM bevakad_leverantor WHERE anvandare_id=?",
            (anv["id"],)).fetchall()
        notifierade = {r[0] for r in db.execute(
            "SELECT nyckel FROM leverantorsnotis WHERE anvandare_id=?",
            (anv["id"],))}
        rader = []  # (text, html, nyckel)
        for bl in bevakade:
            traffar = []
            for kalla_norm, kalla_onr, prefix in (
                    (utgar_norm, utgar_onr, "lu"),
                    (nya_norm, nya_onr, "ln")):
                avtal = list(kalla_onr.get(bl["orgnr"] or "", []))
                avtal += [a for a in kalla_norm.get(bl["namn_norm"], [])
                          if a not in avtal]
                for a in avtal:
                    traffar.append((prefix, a))
            for prefix, a in traffar:
                nyckel = f"{prefix}:{a['id']}"
                if nyckel in notifierade:
                    continue
                notifierade.add(nyckel)
                varde = (f" · ca {a['varde'] / 1e6:.1f} mnkr".replace(
                    ".", ",") if a["varde"] else "")
                if prefix == "lu":
                    text = (f"• {bl['visningsnamn']}: avtalet "
                            f"”{a['titel']}” med {a['org_namn']} löper ut "
                            f"{a['slutdatum']}{varde}")
                else:
                    text = (f"• {bl['visningsnamn']} har vunnit ett nytt "
                            f"kontrakt: ”{a['titel']}”, "
                            f"{a['org_namn']}{varde}")
                html_rad = (
                    f'<p style="margin:0 0 12px">{_h.escape(text[2:])}'
                    f'</p>')
                rader.append((text, html_rad, nyckel))
        if not rader:
            continue
        urval = rader[:MAX_NOTISRADER]
        fler = len(rader) - len(urval)
        halsning = _halsning(anv["namn"])
        lank = f"{bas_url}/bevakningar"
        text = (f"{halsning}\n\n"
                f"Nytt kring dina bevakade leverantörer:\n\n"
                + "\n\n".join(r[0] for r in urval)
                + (f"\n\n…och {fler} händelser till." if fler else "")
                + f"\n\nMed vänliga hälsningar\nConsens\n\n—\n"
                f"Du får det här mejlet för att du följer leverantörer "
                f"hos Consens.\nHantera dina bevakningar: {lank}\n")
        html = (
            f'<div style="font-family:Arial,Helvetica,sans-serif;'
            f'max-width:600px;margin:0 auto;padding:8px 4px;'
            f'color:#0B0D12;font-size:15px;line-height:1.5">'
            f'<p style="margin:0 0 16px">{_h.escape(halsning)}</p>'
            f'<p style="margin:0 0 18px"><b>Nytt kring dina bevakade '
            f'leverantörer:</b></p>'
            + "".join(r[1] for r in urval)
            + (f'<p style="color:#5A6472">…och {fler} händelser till.</p>'
               if fler else "")
            + f'<p style="border-top:1px solid #E4E7EC;margin-top:22px;'
            f'padding-top:12px;color:#5A6472;font-size:12px">'
            f'Du får det här mejlet för att du följer leverantörer hos '
            f'Consens. <a href="{lank}" style="color:#1D5FDB">Hantera '
            f'dina bevakningar</a>.</p></div>')
        if _skicka(anv["epost"],
                   f"Consens · {len(rader)} händelser hos dina bevakade "
                   f"leverantörer", text, typ="leverantorsdigest",
                   anvandare_id=anv["id"], html=html):
            for _, _, nyckel in urval:
                db.execute(
                    "INSERT OR IGNORE INTO leverantorsnotis "
                    "(anvandare_id, nyckel) VALUES (?,?)",
                    (anv["id"], nyckel))
            db.commit()
            skickade += 1
    return skickade



@click.command("skicka-digest")
@click.option("--veckovis", is_flag=True,
              help="Inkludera även veckovisa bevakningar.")
def skicka_digest_command(veckovis):
    """Skicka digest för aktiva bevakningar med nya träffar."""
    from .vyer.flode import hamta_signaler

    db = get_db()
    frekvenser = ["daglig"] + (["veckovis"] if veckovis else [])
    bevakningar = db.execute(
        "SELECT b.*, u.epost, u.namn AS anvandarnamn FROM bevakning b "
        "JOIN anvandare u ON u.id=b.anvandare_id "
        f"WHERE b.aktiv=1 AND u.aktiv=1 AND b.frekvens IN "
        f"({','.join('?' * len(frekvenser))})", frekvenser).fetchall()

    skickade = 0
    bas_url = os.environ.get("CONSENS_BAS_URL", "https://consens.se")
    nu = datetime.now().isoformat(timespec="seconds")
    for b in bevakningar:
        args = MultiDict(parse_qsl(b["params"]))
        traffar = hamta_signaler(db, args, nyare_an=b["senast_kord"],
                                 bara_aktiva=True)
        if traffar:
            import html as _h
            urval = _mest_relevanta(traffar, MAX_PER_MAIL)
            halsning = _halsning(b["anvandarnamn"])
            fler = len(traffar) - len(urval)
            flodeslank = f"{bas_url}/flode?{b['params']}"

            rader = "\n\n".join(_rad(s) for s in urval)
            fler_rad = (f"\n\n…och {fler} träffar till.") if fler > 0 else ""
            text = (f"{halsning}\n\n"
                    f"Din bevakning ”{b['namn']}” har "
                    f"{len(traffar)} nya träffar:\n\n"
                    f"{rader}{fler_rad}\n\n"
                    f"Se alla träffar: Consens – {b['namn']}\n"
                    f"{flodeslank}\n\n"
                    f"Med vänliga hälsningar\n"
                    f"Consens\n\n"
                    f"—\n"
                    f"Du får det här mejlet för att du har en bevakning hos "
                    f"Consens.\n"
                    f"Hantera dina bevakningar och utskick: "
                    f"{bas_url}/bevakningar\n")

            html_rader = "".join(_rad_html(s, bas_url) for s in urval)
            fler_html = (f'<p style="color:#5A6472;margin:0 0 18px">'
                         f'…och {fler} träffar till.</p>') if fler > 0 else ""
            html = (
                f'<div style="font-family:Arial,Helvetica,sans-serif;'
                f'max-width:600px;margin:0 auto;padding:8px 4px;'
                f'color:#0B0D12;font-size:15px;line-height:1.5">'
                f'<p style="margin:0 0 16px">{_h.escape(halsning)}</p>'
                f'<p style="margin:0 0 18px">Din bevakning '
                f'<b>”{_h.escape(b["namn"])}”</b> har {len(traffar)} nya '
                f'träffar:</p>'
                f'{html_rader}{fler_html}'
                f'<p style="margin:22px 0">'
                f'<a href="{_h.escape(flodeslank, quote=True)}" '
                f'style="background:#1D5FDB;color:#ffffff;'
                f'text-decoration:none;padding:10px 18px;border-radius:8px;'
                f'font-weight:600;display:inline-block">'
                f'Consens – {_h.escape(b["namn"])}</a></p>'
                f'<p style="margin:0 0 4px">Med vänliga hälsningar</p>'
                f'<p style="margin:0 0 22px;font-weight:600">Consens</p>'
                f'<p style="border-top:1px solid #E4E7EC;padding-top:12px;'
                f'color:#5A6472;font-size:12px">Du får det här mejlet för '
                f'att du har en bevakning hos Consens. '
                f'<a href="{bas_url}/bevakningar" style="color:#1D5FDB">'
                f'Hantera dina bevakningar och utskick</a>.</p></div>')

            _skicka(b["epost"],
                    f"Consens · {len(traffar)} nya träffar för ”{b['namn']}”",
                    text, html=html)
            skickade += 1
        db.execute("UPDATE bevakning SET senast_kord=? WHERE id=?",
                   (nu, b["id"]))
    db.commit()
    db.commit()
    levmejl = _leverantorsnotiser(db, bas_url)
    if levmejl:
        click.echo(f"Leverantörsnotiser: {levmejl} mejl skickade.")
    click.echo(f"Digest: {skickade} av {len(bevakningar)} bevakningar "
               f"hade nya träffar.")
