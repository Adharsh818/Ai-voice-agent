#!/usr/bin/env bash
# Create a Google Compute Engine VM for Emma: static IP, HTTP/HTTPS firewall
# rule, Ubuntu 24.04 with Docker installed on first boot.
#
#   PROJECT=my-gcp-project ./deploy/gcp-create-vm.sh
#
# Override any of the defaults below through the environment.
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT to your GCP project id}"
REGION="${REGION:-asia-south1}"          # Mumbai, closest to the clinic
ZONE="${ZONE:-${REGION}-a}"
NAME="${NAME:-emma-voice}"
MACHINE="${MACHINE:-e2-small}"           # 2 vCPU (shared), 2 GB RAM
DISK_GB="${DISK_GB:-20}"
OPEN_SIP="${OPEN_SIP:-false}"            # true once Asterisk is added

gcloud config set project "$PROJECT" >/dev/null
gcloud services enable compute.googleapis.com

if ! gcloud compute addresses describe "$NAME-ip" --region "$REGION" >/dev/null 2>&1; then
  gcloud compute addresses create "$NAME-ip" --region "$REGION"
fi
IP="$(gcloud compute addresses describe "$NAME-ip" --region "$REGION" --format='value(address)')"

if ! gcloud compute firewall-rules describe emma-allow-web >/dev/null 2>&1; then
  gcloud compute firewall-rules create emma-allow-web \
    --allow tcp:80,tcp:443,udp:443 --target-tags emma-web \
    --description "Caddy HTTPS for the Emma voice server"
fi

if [[ "$OPEN_SIP" == "true" ]] && ! gcloud compute firewall-rules describe emma-allow-sip >/dev/null 2>&1; then
  # Restrict --source-ranges to your SIP provider's addresses before real use.
  gcloud compute firewall-rules create emma-allow-sip \
    --allow udp:5060,udp:10000-20000 --target-tags emma-web \
    --description "Asterisk SIP signalling and RTP media"
fi

STARTUP=$(cat <<'EOF'
#!/bin/bash
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
EOF
)

if ! gcloud compute instances describe "$NAME" --zone "$ZONE" >/dev/null 2>&1; then
  gcloud compute instances create "$NAME" \
    --zone "$ZONE" \
    --machine-type "$MACHINE" \
    --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
    --boot-disk-size "${DISK_GB}GB" \
    --address "$IP" \
    --tags emma-web \
    --metadata startup-script="$STARTUP"
fi

cat <<EOF

VM ready: $NAME ($ZONE)  external IP $IP
Domain without buying one: $IP.sslip.io

Next:
  gcloud compute ssh $NAME --zone $ZONE
  then follow DEPLOY_CLOUD.md step 3.
EOF
