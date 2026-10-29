#!/usr/bin/env bash
# Consens-installation/uppgradering. Kör som root från /opt/consens.
set -euo pipefail

# Städa bort gamla Convero-tjänsten om den finns
if systemctl list-unit-files | grep -q '^convero.service'; then
  systemctl disable --now convero || true
  rm -f /etc/systemd/system/convero.service
  systemctl daemon-reload
  echo "Gamla convero-tjänsten borttagen."
fi

apt-get update -qq
apt-get install -y -qq python3-venv

id -u consens &>/dev/null || useradd -r -m -s /usr/sbin/nologin consens

cd /opt/consens
python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt

FLASK_APP=consens ./venv/bin/flask init-db
FLASK_APP=consens ./venv/bin/flask seed-demo
chown -R consens:consens /opt/consens

cp deploy/consens.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now consens

echo ""
echo "Klart! Consens svarar på http://$(hostname -I | awk '{print $1}'):8100/"
echo "Skapa din adminanvändare nu:"
echo "  cd /opt/consens && sudo -u consens FLASK_APP=consens ./venv/bin/flask skapa-anvandare din@epost.se --admin"
