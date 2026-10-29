"""Stripe-betalningar: Checkout (hostad sida), kundportal och webhook.

Konfiguration via miljön (.env i projektroten, läses av create_app):
  STRIPE_SECRET_KEY       sk_test_... eller sk_live_...
  STRIPE_WEBHOOK_SECRET   whsec_... från webhook-endpointen i dashboarden
  STRIPE_PRICE_ID         price_... för Consens 595 kr/månad

Flödet: Uppgradera-knappen i inställningarna skapar en Checkout-session
(prenumeration, 14 dagar gratis) och skickar användaren till Stripes
betalsida. Stripe anropar sedan webhooken, som är sanningskällan för
access: den sätter roll, kopplar Stripe-kund och förlänger giltig_till
vid varje betald faktura. Sidan efter betalning visar bara ett besked.
"""
import os
import secrets

import click
from datetime import date, datetime, timedelta

from flask import (Blueprint, abort, g, redirect, render_template,
                   request, url_for)

from .db import get_db

bp = Blueprint("betalning", __name__, url_prefix="/betalning")

TRIAL_DAGAR_STRIPE = 14   # kortet anges direkt, första dragningen efter
MARGINAL_DAGAR = 5        # giltig_till sätts något efter periodslut så
                          # access inte hinner stängas innan nästa faktura


def _stripe():
    """Lat import så appen startar även innan biblioteket installerats."""
    import stripe
    nyckel = os.environ.get("STRIPE_SECRET_KEY")
    if not nyckel:
        return None
    stripe.api_key = nyckel
    return stripe


def _aktuell():
    """Inloggad användare, eller utgånget konto som väntar på betalning."""
    return g.get("anvandare") or g.get("betalvantande")


@bp.route("/uppgradera")
def uppgradera():
    """Sidan för utgångna konton: enda vägen in är att betala. Aktiva
    konton skickas till inställningarna där samma knapp finns."""
    if g.get("anvandare") is not None:
        return redirect(url_for("auth.installningar"))
    if g.get("betalvantande") is None:
        return redirect(url_for("auth.logga_in"))
    return render_template("uppgradera.html", a=g.betalvantande)


@bp.route("/testa")
def testa():
    """Publik ingång: Testa gratis i 14 dagar. Skapar en Checkout-session
    direkt, utan konto; kontot skapas av webhooken när betalningen (med
    trial) är registrerad. Inloggade skickas genom vanliga checkout."""
    from .matchning import for_manga_forsok
    ip = request.headers.get("X-Forwarded-For",
                             request.remote_addr or "?").split(",")[0].strip()
    if for_manga_forsok(get_db(), "testa", ip, 10, 15):
        abort(429)
    stripe = _stripe()
    pris = os.environ.get("STRIPE_PRICE_ID")
    if stripe is None or not pris:
        # Betalning inte konfigurerad: falla tillbaka på ansökan i stället
        # för ett felmeddelande mot en presumtiv kund.
        return redirect(url_for("publikt.ansok"))
    rad = _aktuell()
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": pris, "quantity": 1}],
        subscription_data={"trial_period_days": TRIAL_DAGAR_STRIPE},
        client_reference_id=(str(rad["id"]) if rad else None),
        customer=(rad["stripe_kund"] if rad else None) or None,
        customer_email=(rad["epost"] if rad and not rad["stripe_kund"]
                        else None),
        billing_address_collection="auto",
        phone_number_collection={"enabled": False},
        automatic_tax={"enabled": True},
        allow_promotion_codes=False,
        payment_method_collection="always",
        success_url=url_for("betalning.klart", _external=True)
        + "?session={CHECKOUT_SESSION_ID}",
        cancel_url=url_for("publikt.start", _external=True),
    )
    return redirect(session.url, code=303)


@bp.route("/checkout", methods=("POST",))
def checkout():
    if _aktuell() is None:
        return redirect(url_for("auth.logga_in"))
    stripe = _stripe()
    pris = os.environ.get("STRIPE_PRICE_ID")
    if stripe is None or not pris:
        abort(503, "Betalning är inte konfigurerad ännu.")
    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": pris, "quantity": 1}],
        subscription_data={"trial_period_days": TRIAL_DAGAR_STRIPE},
        client_reference_id=str(_aktuell()["id"]),
        customer_email=(None if _aktuell()["stripe_kund"]
                        else _aktuell()["epost"]),
        customer=_aktuell()["stripe_kund"] or None,
        billing_address_collection="auto",
        phone_number_collection={"enabled": False},
        automatic_tax={"enabled": True},
        allow_promotion_codes=False,
        payment_method_collection="always",
        success_url=url_for("betalning.klart", _external=True)
        + "?session={CHECKOUT_SESSION_ID}",
        cancel_url=url_for("auth.installningar", _external=True),
    )
    return redirect(session.url, code=303)


@bp.route("/klart")
def klart():
    """Landning efter Checkout. Webhooken gör jobbet; här visas besked.
    Öppen även utan inloggning: nya kunder har inget konto ännu när de
    landar här, mejlet med lösenordslänken är på väg."""
    return render_template("betalning_klart.html",
                           inloggad=_aktuell() is not None)


@bp.route("/portal", methods=("POST",))
def portal():
    """Stripes kundportal: kortbyte, kvitton, uppsägning."""
    if g.get("anvandare") is None:
        return redirect(url_for("auth.logga_in"))
    stripe = _stripe()
    kund = g.anvandare["stripe_kund"]
    if stripe is None or not kund:
        abort(404)
    session = stripe.billing_portal.Session.create(
        customer=kund,
        return_url=url_for("auth.installningar", _external=True))
    return redirect(session.url, code=303)


def _skapa_eller_koppla_konto(db, objekt, giltig, trial_slut):
    """Checkout utan inloggat konto: koppla till befintlig användare med
    samma e-post, annars skapa kontot och mejla en lösenordslänk.
    Idempotent: körs webhooken om görs samma sak en gång till."""
    from werkzeug.security import generate_password_hash

    detaljer = objekt.get("customer_details") or {}
    epost = (detaljer.get("email") or "").strip().lower()
    if not epost:
        return
    rad = db.execute("SELECT id FROM anvandare WHERE epost=?",
                     (epost,)).fetchone()
    if rad is None:
        db.execute(
            "INSERT INTO anvandare (epost, namn, losenord_hash, roll,"
            " giltig_till, stripe_kund, stripe_prenumeration,"
            " stripe_trial_slut)"
            " VALUES (?, ?, ?, 'anvandare', ?, ?, ?, ?)",
            (epost, detaljer.get("name"),
             generate_password_hash(secrets.token_urlsafe(32)),
             giltig, objekt.get("customer"), objekt.get("subscription"),
             trial_slut))
        anv_id = db.execute("SELECT id FROM anvandare WHERE epost=?",
                            (epost,)).fetchone()["id"]
        token = secrets.token_urlsafe(32)
        db.execute("INSERT INTO losenordsaterstallning (anvandare_id,"
                   " token) VALUES (?, ?)", (anv_id, token))
        db.commit()
        from .digest import _skicka
        lank = request.url_root.rstrip("/") + url_for("auth.aterstall",
                                                      token=token)
        _skicka(epost, "Välkommen till Consens! Välj ditt lösenord",
                "Hej!\n\nDitt Consens-konto är klart. De första 14 "
                "dagarna är gratis, därefter 595 kr per månad; du kan "
                "avsluta när som helst under Inställningar.\n\nVälj "
                "ditt lösenord här (länken gäller i 60 minuter):\n\n"
                f"{lank}\n\nHinner länken gå ut använder du Glömt "
                "lösenord på inloggningssidan med den här "
                "e-postadressen.\n\nConsens · consens.se")
    else:
        db.execute(
            "UPDATE anvandare SET stripe_kund=?, stripe_prenumeration=?,"
            " roll=CASE WHEN roll IN ('trial','kompis') THEN 'anvandare'"
            " ELSE roll END, giltig_till=?, stripe_trial_slut=? WHERE id=?",
            (objekt.get("customer"), objekt.get("subscription"),
             giltig, trial_slut, rad["id"]))
        db.commit()


@bp.route("/webhook", methods=("POST",))
def webhook():
    """Tar emot händelser från Stripe. Undantagen från CSRF-skyddet i
    create_app; äktheten garanteras i stället av signaturverifieringen
    nedan. Hanteringen är idempotent: samma händelse två gånger ger
    samma sluttillstånd."""
    stripe = _stripe()
    hemlighet = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if stripe is None or not hemlighet:
        abort(503)
    try:
        handelse = stripe.Webhook.construct_event(
            request.get_data(), request.headers.get("Stripe-Signature", ""),
            hemlighet)
    except Exception:
        abort(400)

    db = get_db()
    objekt = handelse["data"]["object"]
    if hasattr(objekt, "to_dict"):
        objekt = objekt.to_dict()
    typ = handelse["type"]

    if typ == "checkout.session.completed":
        anv_id = objekt.get("client_reference_id")
        giltig = (date.today() + timedelta(days=TRIAL_DAGAR_STRIPE
                                           + MARGINAL_DAGAR)).isoformat()
        trial_slut = (date.today()
                      + timedelta(days=TRIAL_DAGAR_STRIPE)).isoformat()
        if anv_id:
            db.execute(
                "UPDATE anvandare SET stripe_kund=?, stripe_prenumeration=?,"
                " roll=CASE WHEN roll IN ('trial','kompis') THEN 'anvandare'"
                " ELSE roll END, giltig_till=?, stripe_trial_slut=?"
                " WHERE id=?",
                (objekt.get("customer"), objekt.get("subscription"),
                 giltig, trial_slut, int(anv_id)))
            db.commit()
        else:
            _skapa_eller_koppla_konto(db, objekt, giltig, trial_slut)

    elif typ == "invoice.paid":
        kund = objekt.get("customer")
        slut = None
        try:
            rader = objekt["lines"]["data"]
            slut = max(r["period"]["end"] for r in rader if r.get("period"))
        except (KeyError, ValueError, TypeError):
            pass
        if slut:
            giltig = (datetime.utcfromtimestamp(slut).date()
                      + timedelta(days=MARGINAL_DAGAR)).isoformat()
        else:
            giltig = (date.today() + timedelta(days=30 + MARGINAL_DAGAR)
                      ).isoformat()
        if kund:
            db.execute(
                "UPDATE anvandare SET giltig_till=?,"
                " roll=CASE WHEN roll IN ('trial','kompis') THEN 'anvandare'"
                " ELSE roll END WHERE stripe_kund=?", (giltig, kund))
            db.commit()

    elif typ == "customer.subscription.deleted":
        kund = objekt.get("customer")
        if kund:
            db.execute(
                "UPDATE anvandare SET giltig_till=?, stripe_prenumeration=NULL"
                " WHERE stripe_kund=? AND roll != 'admin'",
                (date.today().isoformat(), kund))
            db.commit()

    return "", 200


@click.command("paminn-trial")
def paminn_trial_command():
    """Mejlar den vars provperiod tar slut om två dagar (körs dagligen
    av cron kl 07:05; datummatchningen gör att varje konto bara träffas
    en gång, så ingen skickat-flagga behövs)."""
    from .digest import _skicka
    db = get_db()
    brytdag = (date.today() + timedelta(days=2)).isoformat()
    rader = db.execute(
        "SELECT epost, namn FROM anvandare WHERE stripe_trial_slut=?"
        " AND stripe_prenumeration IS NOT NULL", (brytdag,)).fetchall()
    for rad in rader:
        namn = (rad["namn"] or "").split(" ")[0] or "du"
        _skicka(rad["epost"],
                "Din provperiod hos Consens tar slut om två dagar",
                f"Hej {namn}!\n\nOm två dagar går de 14 kostnadsfria "
                "dagarna ut och den första månadsavgiften på 595 kr "
                "dras. Vill du fortsätta behöver du inte göra "
                "någonting.\n\nVill du inte fortsätta säger du upp "
                "kostnadsfritt innan dess: logga in på consens.se, gå "
                "till Inställningar och öppna kundportalen.\n\nFrågor "
                "eller funderingar? Svara på det här mejlet.\n\n"
                "Consens · consens.se")
    print(f"Påminde {len(rader)} konton (trial slut {brytdag}).")
