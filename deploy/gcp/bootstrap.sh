#!/bin/bash
set -eu
# Initialization runs once; later boots must keep the configured services enabled.
if [ -f /var/lib/unstuckery-bootstrap-ready ]; then exit 0; fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3-venv caddy ca-certificates unattended-upgrades
id unstuckery >/dev/null 2>&1 || useradd --system --home /srv/unstuckery --shell /usr/sbin/nologin unstuckery
install -d -o unstuckery -g unstuckery -m 700 /srv/unstuckery/data /srv/unstuckery/private
install -d -m 755 /opt/unstuckery
# Deployment supplies the authenticated app and the hostname configuration.
systemctl disable --now caddy
printf 'ready\n' > /var/lib/unstuckery-bootstrap-ready
