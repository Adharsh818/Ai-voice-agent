# Deploying Emma to a cloud VM

This runs the voice server on a Linux VM on Google Compute Engine, behind
Caddy for HTTPS and a login. Browsers only allow microphone access over HTTPS,
so a certificate is required before the talk page works from anywhere other
than `localhost`.

```text
browser ──HTTPS / WSS──> Caddy (:443, Let's Encrypt, basic auth) ──> emma (:8000, private)
                                                                    ├─ Deepgram STT
                                                                    ├─ Gemini NLU
                                                                    └─ ElevenLabs TTS
```

The same VM is where Asterisk will run later (see `PRODUCTION_CALL_PATH.md`).

## What you need

- A Google Cloud project with billing enabled and the `gcloud` CLI.
- API keys: Gemini, Deepgram, ElevenLabs.
- Optional: a domain name. Without one, `<VM IP>.sslip.io` works and still gets
  a real certificate.

Cost: an `e2-small` in `asia-south1` is roughly USD 15/month plus the static IP.
To stay inside the always-free tier use `MACHINE=e2-micro REGION=us-central1`,
at the cost of ~250 ms extra round trip to callers in India and only 1 GB RAM.

## 1. Create the VM

From your own machine (or Cloud Shell), in the repo:

```bash
PROJECT=your-gcp-project-id ./deploy/gcp-create-vm.sh
```

This reserves a static IP, opens ports 80/443 for the VM only, and creates an
Ubuntu 24.04 VM that installs Docker on first boot. It prints the IP and an
`sslip.io` domain. If you have your own domain, point an `A` record at the IP.

## 2. Log in

```bash
gcloud compute ssh emma-voice --zone asia-south1-a
sudo usermod -aG docker "$USER" && exit   # then ssh in again
docker --version                          # first boot may take a minute
```

## 3. Get the code and configure it

```bash
git clone https://github.com/adharsh818/ai-voice-agent.git
cd ai-voice-agent/deploy
cp .env.example .env
docker run --rm caddy:2 caddy hash-password --plaintext 'choose-a-password'
nano .env
```

In `deploy/.env` set `EMMA_DOMAIN`, `EMMA_BASIC_AUTH_HASH` (the output above,
inside single quotes) and the API keys. A private repo needs a GitHub token
or deploy key for the clone.

For Google Calendar, copy a **service-account** key to
`deploy/secrets/credentials.json` and share the clinic calendar with the
service account's email. The OAuth browser flow cannot complete on a headless
VM. Leave `USE_MOCK_APIS=True` until you have checked a staging booking.

```bash
mkdir -p secrets
# from your machine:
# gcloud compute scp credentials.json emma-voice:~/ai-voice-agent/deploy/secrets/ --zone asia-south1-a
chmod 600 secrets/credentials.json
```

## 4. Start it

```bash
docker compose up -d --build
docker compose logs -f emma     # wait for "Emma is listening"
```

Open `https://<EMMA_DOMAIN>/`, log in, and press the circle. Check
`https://<EMMA_DOMAIN>/health` for key status and `/metrics` for latency.

## Operating it

| Task | Command (in `deploy/`) |
| --- | --- |
| Update to latest code | `git pull && docker compose up -d --build` |
| Logs | `docker compose logs -f emma` |
| Restart | `docker compose restart emma` |
| Stop | `docker compose down` |
| Change keys | edit `.env`, then `docker compose up -d` |

Both containers restart on reboot. Prompt cache, latency logs and the mock
booking database live in the `emma-data` volume and survive rebuilds.

## Security notes

- Only Caddy is published. Port 8000 is reachable only inside the compose network.
- Every request, including the `/ws/voice` WebSocket, needs the login. The
  server also caps itself at one concurrent call (`MAX_CONCURRENT_CALLS`).
- Secrets stay on the VM in `deploy/.env` and `deploy/secrets/`, which are
  git-ignored and excluded from the Docker image.
- When Asterisk is added, run `OPEN_SIP=true ./deploy/gcp-create-vm.sh` and then
  restrict the `emma-allow-sip` rule's source ranges to your SIP provider.
