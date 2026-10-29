# Consens

Bevakning av offentliga upphandlingar och avtalsutgångar för leverantörer
(consens.se). Serverrenderad Flask-app med SQLite. Nodtech AB.

## Struktur

    consens/
      __init__.py       appfabrik
      db.py             SQLite + CLI (init-db, seed-demo)
      schema.sql        datamodell
      cpv.py            bransch -> CPV-prefix
      kategorier.py, yrken.py
      matchning.py      köparmatchning
      bevakar.py        bevakningar
      digest.py         e-postdigest (Resend)
      rapport.py, cache.py
      betalning.py      Stripe-prenumerationer + webhook
      ingest/           datakällor: TED, KommersAnnons, Avropa, Adda, e-Avrop,
                        Kolada, Sinfra, HBV, avtalsutgångar m.fl.
                        (bas.py = körningshistorik + torrkörning)
      vyer/             publikt, auth, flöde, insikter, leverantörer, admin, api
      templates/, static/
    deploy/             systemd-tjänst, cron, install-skript
    tester/             pytest

## Köra lokalt

    python3 -m venv venv && source venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env        # fyll i värden
    flask --app consens init-db
    flask --app consens seed-demo
    python run.py               # http://localhost:5000

## Konfiguration

Alla hemligheter läses från `.env` (se `.env.example`): sessionsnyckel,
Resend-nyckel och avsändare samt Stripe-nycklar. `.env` och databasen i
`instance/` versionshanteras aldrig. Se STRIPE_INTEGRATION_TODO.md för Stripe.

## Tester

    pytest tester/

## Drift

Körs på VPS som systemd-tjänst (gunicorn) bakom reverse proxy med HTTPS.
Schemalagda inläsningar och digest körs via `deploy/consens-cron`.
