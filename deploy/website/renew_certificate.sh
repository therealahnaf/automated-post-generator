#!/bin/sh
set -eu
cd /opt/thebitstoday/app/deploy/website
docker run --rm \
  -v "$PWD/letsencrypt:/etc/letsencrypt" \
  -v "$PWD/acme:/var/www/acme" \
  certbot/certbot:latest renew --quiet --webroot -w /var/www/acme
python3 /opt/thebitstoday/app/deploy/website/install_certificate.py
