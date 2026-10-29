# Stripe: återstående steg

Detta är enda källan för vad som återstår innan betalningar fungerar.

## Värden att sätta (miljövariabler, inte kod)

Inga platshållare ligger i koden. Allt konfigureras i `.env` i
projektroten på servern (`/opt/consens/.env`):

| Variabel | Värde | Var du hittar det |
|---|---|---|
| STRIPE_SECRET_KEY | sk_test_... (byt till sk_live_... vid skarp drift) | Dashboard → Utvecklare → API-nycklar |
| STRIPE_WEBHOOK_SECRET | whsec_... | Skapas när webhook-endpointen läggs upp, se nedan |
| STRIPE_PRICE_ID | price_... | Produktkatalog → Consens → priset 595 kr/mån |

Kommando på servern (fyll i värdena):

```bash
cat >> /opt/consens/.env <<'EOF'
STRIPE_SECRET_KEY=sk_test_FYLL_I
STRIPE_WEBHOOK_SECRET=whsec_FYLL_I
STRIPE_PRICE_ID=price_FYLL_I
EOF
chmod 600 /opt/consens/.env && systemctl restart consens
```

## Konfigurerade parametrar (från Checkout Studio)

**Fil:** [consens/betalning.py](consens/betalning.py)

| Parameter | Värde |
|---|---|
| mode | subscription |
| billing_address_collection | auto |
| phone_number_collection | enabled: false |
| automatic_tax | enabled: true |
| allow_promotion_codes | false |
| payment_method_collection | always |
| subscription_data.trial_period_days | 14 |

Avsiktligt utelämnade: `submit_type` (gäller inte prenumerationsläge),
`origin_context: mobile_app` och den mobilmärkta integrations-
identifieraren (Studio-konfigurationen var satt till mobilapp;
Consens är en webbtjänst). `ui_mode` utelämnad = hostad sida, vilket
är standard.

## Uppsättning, i ordning

1. **Installera biblioteket på servern** (deployskriptet installerar
   inte pip-paket):
   ```bash
   cd /opt/consens && sudo -u consens venv/bin/pip install stripe
   ```
2. **Skapa produkt och pris i Stripe** (testläge): Produktkatalog →
   Skapa produkt. Namn Consens, återkommande, 595 kr/månad, momsbeteende
   "inklusive". Kopiera price_... till `.env`.
3. **Aktivera Stripe Tax** (Inställningar → Skatt) och registrera
   Sverige som skatteregion.
4. **Skapa webhook-endpointen**: Utvecklare → Webhookar → Lägg till.
   URL `https://consens.se/betalning/webhook`. Händelser:
   `checkout.session.completed`, `invoice.paid`,
   `customer.subscription.deleted`. Kopiera whsec_... till `.env`.
5. **Fyll i `.env`** enligt ovan och starta om tjänsten.
6. **Testa i testläge**: logga in med ett trial-konto →
   Inställningar → Uppgradera kontot. Kort 4242 4242 4242 4242,
   valfritt framtida utgångsdatum och CVC. Kontrollera att rollen
   blir Normalanvändare och att giltig_till sätts.
   Avbrutet kort: 4000 0000 0000 0002.
7. **Skarpt läge**: byt till live-nycklar i `.env`, skapa produkt,
   pris och webhook igen i live-läget (test och live delar ingenting),
   uppdatera whsec och price i `.env`, starta om.

## Så funkar flödet

Inställningar → Uppgradera kontot → POST `/betalning/checkout` skapar
en Checkout-session och skickar användaren till Stripes betalsida.
14 dagar gratis, kortet dras först efter trialperioden. Stripe anropar
`/betalning/webhook` (signaturverifierad, undantagen CSRF):

- `checkout.session.completed`: kopplar Stripe-kund till kontot,
  roll trial/kompis → anvandare, giltig_till sätts preliminärt.
- `invoice.paid`: giltig_till förlängs till fakturaperiodens slut
  plus fem dagars marginal. Detta täcker även fakturor du skapar
  manuellt i dashboarden för kunder som vill betala mot faktura.
- `customer.subscription.deleted`: giltig_till sätts till i dag,
  kontot stängs dagen efter (admin undantas alltid).

Kundportalen (Inställningar → Öppna kundportalen) sköter kortbyte,
kvitton och uppsägning. Aktivera portalen första gången under
Inställningar → Kundportal i dashboarden.

## Kända begränsningar, medvetna val

- ~~Utgången trial låser ute~~ Löst: utgångna konton loggas in till
  en spärrad betalsida (/betalning/uppgradera) där enda vägen vidare
  är att uppgradera. Övriga sidor förblir stängda tills webhooken
  aktiverat kontot.
- En prenumeration per användare (kvantitet 1). Fleranvändarkonton
  med samlad faktura är inte byggt.
- Sidan `/betalning/klart` litar på webhooken; kommer den inte fram
  (fel whsec, nätfel) förblir kontot trial. Kontrollera då
  webhook-loggen i Stripe-dashboarden, händelser kan skickas om
  därifrån med en knapp.

## Resurser

- https://support.stripe.com
- https://docs.stripe.com/mcp
