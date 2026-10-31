#!/usr/bin/env bash
# One-shot server setup for Crawler on a fresh Ubuntu/Debian VPS. Run as root:
#   bash setup.sh
# Safe to run again: it updates the code and restarts, keeping your settings and data.
set -euo pipefail

DIR=/opt/crawler
REPO=https://github.com/Noki3x/Crawler.git

ask() {  # ask "Prompt" [silent]
  local reply
  if [[ "${2:-}" == silent ]]; then read -rsp "$1" reply </dev/tty; echo >&2; else read -rp "$1" reply </dev/tty; fi
  printf '%s' "$reply"
}
set_env() {  # set_env KEY VALUE
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}

echo "==> Installing Docker, git and the firewall"
if ! command -v docker >/dev/null; then curl -fsSL https://get.docker.com | sh; fi
apt-get install -y -qq git ufw >/dev/null
ufw allow OpenSSH >/dev/null && ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

echo "==> Getting the code"
if [[ -d $DIR/.git ]]; then
  git -C "$DIR" pull --ff-only
else
  # The repo is private, so cloning needs a GitHub token with read access to it.
  url=$REPO
  if [[ -n "${GITHUB_TOKEN:-}" ]] || ! git ls-remote "$REPO" >/dev/null 2>&1; then
    token=${GITHUB_TOKEN:-$(ask "GitHub token with read access to Noki3x/Crawler: " silent)}
    url="https://x-access-token:${token}@github.com/Noki3x/Crawler.git"
  fi
  git clone -q "$url" "$DIR"
  git -C "$DIR" remote set-url origin "$url"
fi
cd "$DIR"

if [[ ! -f .env ]]; then
  echo "==> First run: creating settings"
  cp .env.example .env
  chmod 600 .env
  key=$(ask "Anthropic API key (input hidden): " silent)
  set_env ANTHROPIC_API_KEY "$key"
  set_env DASHBOARD_PASSWORD "$(openssl rand -base64 18 | tr -d '/+=')"
  set_env POSTGRES_PASSWORD "$(openssl rand -hex 16)"
  set_env NTFY_TOPIC "crawler-$(openssl rand -hex 8)"
fi

# HTTPS without buying a domain: sslip.io maps 1-2-3-4.sslip.io to 1.2.3.4, and Caddy gets a free certificate.
ip=$(curl -fsS4 https://api.ipify.org)
site=${SITE_ADDRESS:-${ip//./-}.sslip.io}
set_env SITE_ADDRESS "$site"

echo "==> Building and starting (first build takes a few minutes)"
docker compose -f docker-compose.yml -f deploy/docker-compose.https.yml up -d --build

# shellcheck disable=SC1091
source <(grep -E '^(DASHBOARD_PASSWORD|NTFY_TOPIC)=' .env)
cat <<DONE

  Crawler is running.

  Terminal:   https://$site
  Username:   anything
  Password:   $DASHBOARD_PASSWORD

  Phone alerts: in the ntfy app, tap + and subscribe to this topic:
              $NTFY_TOPIC

  Logs:       cd $DIR && docker compose logs -f crawler
  Update:     bash $DIR/deploy/setup.sh

  The certificate can take a minute on first start. Keep the password and topic private.
DONE
