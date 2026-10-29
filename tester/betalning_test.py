"""Webhooktester: avvisad förfalskning, aktivering, förlängning,
uppsägning. Signaturen byggs för hand enligt Stripes schema
(t=tid, v1=HMAC-SHA256 över "tid.kropp") så inget nätverk behövs."""
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

HEMLIGHET = "whsec_testhemlighet"
os.environ["STRIPE_SECRET_KEY"] = "sk_test_dummy"
os.environ["STRIPE_WEBHOOK_SECRET"] = HEMLIGHET

from consens import create_app  # noqa: E402
from consens.db import get_db  # noqa: E402

_app = None


def app_med_anvandare():
    global _app
    if _app is None:
        import tempfile
        dbfil = os.path.join(tempfile.mkdtemp(), "test.sqlite")
        _app = create_app({"TESTING": True, "DATABASE": dbfil})
        with _app.app_context():
            db = get_db()
            rot = os.path.join(os.path.dirname(__file__), "..", "consens")
            db.executescript(open(os.path.join(rot, "schema.sql")).read())
            db.execute(
                "INSERT INTO anvandare (epost, namn, losenord_hash, roll)"
                " VALUES ('kund@test.se', 'Kund', 'x', 'trial')")
            db.commit()
    return _app


def signera(kropp: bytes) -> str:
    t = int(time.time())
    mac = hmac.new(HEMLIGHET.encode(), f"{t}.".encode() + kropp,
                   hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def skicka(client, handelse: dict, signatur=None):
    kropp = json.dumps(handelse).encode()
    return client.post(
        "/betalning/webhook", data=kropp,
        headers={"Stripe-Signature": signatur or signera(kropp)})


def test_forfalskad_signatur_avvisas():
    app = app_med_anvandare()
    with app.app_context():
        svar = skicka(app.test_client(),
                      {"type": "invoice.paid", "data": {"object": {}}},
                      signatur="t=1,v1=fel")
        assert svar.status_code == 400


def test_checkout_aktiverar_kontot():
    app = app_med_anvandare()
    with app.app_context():
        svar = skicka(app.test_client(), {
            "type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": "1",
                                "customer": "cus_test",
                                "subscription": "sub_test"}}})
        assert svar.status_code == 200
        rad = get_db().execute(
            "SELECT * FROM anvandare WHERE id=1").fetchone()
        assert rad["roll"] == "anvandare"
        assert rad["stripe_kund"] == "cus_test"
        assert rad["giltig_till"] > date.today().isoformat()


def test_betald_faktura_forlanger():
    app = app_med_anvandare()
    with app.app_context():
        slut = int(time.time()) + 30 * 86400
        svar = skicka(app.test_client(), {
            "type": "invoice.paid",
            "data": {"object": {
                "customer": "cus_test",
                "lines": {"data": [{"period": {"end": slut}}]}}}})
        assert svar.status_code == 200
        rad = get_db().execute(
            "SELECT giltig_till FROM anvandare WHERE id=1").fetchone()
        assert rad["giltig_till"] > (date.today()
                                     + timedelta(days=30)).isoformat()


def test_uppsagning_stanger_vid_dagens_datum():
    app = app_med_anvandare()
    with app.app_context():
        svar = skicka(app.test_client(), {
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_test"}}})
        assert svar.status_code == 200
        rad = get_db().execute(
            "SELECT giltig_till, stripe_prenumeration FROM anvandare"
            " WHERE id=1").fetchone()
        assert rad["giltig_till"] == date.today().isoformat()
        assert rad["stripe_prenumeration"] is None


def test_utgangen_trial_far_uppgradera():
    """Utgången trial: inloggning leder till betalsidan, flödet stängt."""
    app = app_med_anvandare()
    with app.app_context():
        from werkzeug.security import generate_password_hash
        db = get_db()
        start = (date.today() - timedelta(days=20)).isoformat()
        db.execute(
            "INSERT INTO anvandare (epost, namn, losenord_hash, roll,"
            " trial_start) VALUES ('slut@test.se','S',?,'trial',?)",
            (generate_password_hash("hemligt12"), start))
        db.commit()
        c = app.test_client()
        svar = c.post("/logga-in", data={"epost": "slut@test.se",
                                         "losenord": "hemligt12"})
        assert svar.headers["Location"].endswith("/betalning/uppgradera")
        assert c.get("/betalning/uppgradera").status_code == 200
        assert c.get("/flode").status_code == 302


def test_anonym_checkout_skapar_konto():
    """Webhook utan client_reference_id: kontot skapas ur e-posten,
    lösenordslänk läggs upp, och en omkörning dubblerar inget."""
    app = app_med_anvandare()
    with app.app_context():
        handelse = {
            "type": "checkout.session.completed",
            "data": {"object": {
                "customer": "cus_ny", "subscription": "sub_ny",
                "customer_details": {"email": "ny@kund.se",
                                     "name": "Ny Kund"}}}}
        assert skicka(app.test_client(), handelse).status_code == 200
        db = get_db()
        rad = db.execute("SELECT * FROM anvandare WHERE epost='ny@kund.se'"
                         ).fetchone()
        assert rad is not None and rad["roll"] == "anvandare"
        assert rad["stripe_kund"] == "cus_ny"
        assert db.execute("SELECT COUNT(*) c FROM losenordsaterstallning"
                          " WHERE anvandare_id=?", (rad["id"],)
                          ).fetchone()["c"] == 1
        # Idempotens: samma händelse igen ger inte två konton
        assert skicka(app.test_client(), handelse).status_code == 200
        assert db.execute("SELECT COUNT(*) c FROM anvandare WHERE"
                          " epost='ny@kund.se'").fetchone()["c"] == 1
