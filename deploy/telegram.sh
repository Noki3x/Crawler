#!/usr/bin/env bash
# Connects the bot to your Telegram channel. Run as root on the server, after setup.sh:
#   bash /opt/crawler/deploy/telegram.sh
# Before running it: create a bot with @BotFather and add it to your channel as an admin
# that can post messages. The token is typed here and saved only in /opt/crawler/.env.
set -euo pipefail

DIR=/opt/crawler
cd "$DIR"

ask() {  # ask "Prompt" [silent]
  local reply
  if [[ "${2:-}" == silent ]]; then read -rsp "$1" reply </dev/tty; echo >&2; else read -rp "$1" reply </dev/tty; fi
  printf '%s' "$reply"
}
set_env() {  # set_env KEY VALUE
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
api() {  # api METHOD [curl args...]
  local method=$1; shift
  curl -fsS "https://api.telegram.org/bot${token}/${method}" "$@"
}
field() {  # field PYTHON_EXPR < json
  python3 -c "import json, sys; d = json.load(sys.stdin); print($1)"
}

token=$(ask "Bot token from @BotFather (input hidden): " silent)
if ! name=$(api getMe | field 'd["result"]["username"]'); then
  echo "Telegram didn't accept that token. Copy it again from @BotFather and rerun this script." >&2
  exit 1
fi
echo "==> Token works: @$name"

cat <<STEP

  Make sure @$name is an admin of your channel with permission to post.
  Then post any message in the channel (for example "hello").

STEP
ask "Press Enter once you've posted the message... " >/dev/null

chat=""
for _ in 1 2 3 4 5 6; do
  chat=$(api getUpdates | field 'next((str(u["channel_post"]["chat"]["id"]) for u in reversed(d["result"]) if "channel_post" in u), "")')
  [[ -n $chat ]] && break
  echo "    waiting for the channel post..."
  sleep 5
done
if [[ -z $chat ]]; then
  echo "Didn't see a post from a channel. Check the bot is an admin there, post again, and rerun." >&2
  exit 1
fi
echo "==> Found the channel: $chat"

api sendMessage -H 'Content-Type: application/json' \
  -d "{\"chat_id\": \"$chat\", \"text\": \"✅ Crawler connected. Rug pulls, possible rug pulls, hacks and the daily runners will be posted here.\"}" >/dev/null

set_env TELEGRAM_BOT_TOKEN "$token"
set_env TELEGRAM_CHAT_ID "$chat"
grep -q '^TELEGRAM_KINDS=' .env || set_env TELEGRAM_KINDS "rug_risk,hack,picks"

echo "==> Restarting the bot"
docker compose -f docker-compose.yml -f deploy/docker-compose.https.yml up -d crawler

echo
echo "  Done. A test message was posted to your channel, and new alerts will follow."
