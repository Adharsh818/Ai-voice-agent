#!/usr/bin/env bash
# One-time setup for GitHub's "deploy" workflow (.github/workflows/deploy.yml). Run it yourself in Git Bash:
#
#   bash tools/setup_deploy_secrets.sh
#
# It makes a key used only by GitHub's deploys (not your own emma_oracle key), allows it on the server,
# and stores it and the server's host key as the repository secrets EMMA_SSH_KEY and EMMA_KNOWN_HOSTS.
# Nothing secret is printed. To revoke later: delete the "github-actions-deploy" line from
# ~/.ssh/authorized_keys on the server, and the two secrets on GitHub.
set -euo pipefail
HOST="${EMMA_HOST:-ubuntu@3.27.254.11}"
REPO="${EMMA_REPO:-Adharsh818/Ai-voice-agent}"
OWN_KEY="$HOME/.ssh/emma_oracle"
KEY="$HOME/.ssh/emma_github_deploy"

command -v gh >/dev/null || { echo "Install the GitHub CLI (gh) and run 'gh auth login' first." >&2; exit 1; }
[ -f "$OWN_KEY" ] || { echo "Your server key $OWN_KEY is missing." >&2; exit 1; }

if [ ! -f "$KEY" ]; then
    ssh-keygen -q -t ed25519 -N "" -C "github-actions-deploy@Ai-voice-agent" -f "$KEY"
    echo "1/4 Made a deploy-only key: $KEY"
else
    echo "1/4 Deploy-only key already there: $KEY"
fi

ssh -i "$OWN_KEY" -o BatchMode=yes "$HOST" 'mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
read -r pub; grep -qxF "$pub" ~/.ssh/authorized_keys || echo "$pub" >> ~/.ssh/authorized_keys' < "$KEY.pub"
ssh -i "$KEY" -o BatchMode=yes -o IdentitiesOnly=yes "$HOST" true
echo "2/4 The server accepts the deploy key"

gh secret set EMMA_SSH_KEY --repo "$REPO" < "$KEY"
echo "3/4 Secret EMMA_SSH_KEY set on $REPO"

ssh-keyscan -t ed25519 "${HOST#*@}" 2>/dev/null | grep -v '^#' | gh secret set EMMA_KNOWN_HOSTS --repo "$REPO"
echo "4/4 Secret EMMA_KNOWN_HOSTS set on $REPO"
echo "Done. Deploy from GitHub: Actions > deploy > Run workflow (on main), or: gh workflow run deploy --repo $REPO"
