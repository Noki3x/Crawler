# Crawler

A bot that crawls the web for news and data that move the crypto market, built for personal trading decisions.

The plan has four steps:

1. **Collectors and server** (this code): pull news, social posts, prices, DEX liquidity, hacks and regulator notices into one database, 24/7.
2. **AI analysis**: Claude tags each item as major or minor and bullish or bearish, and names the coins it affects. Token-safety checks drive rug-pull and low-liquidity warnings.
3. **Terminal app**: a live dashboard to control the bot, see what it's reading, and track your wallets by public address.
4. **Predictions and alerts**: daily "top runners" and market-direction signals, scored against what happened, with phone alerts.

## What it collects

| Collector | Source | Every | Stored as |
|---|---|---|---|
| `rss` | CoinDesk, Cointelegraph, Decrypt, The Block, Bitcoin Magazine, CryptoSlate, Rekt (hacks), SEC and CFTC press releases | 2 min | `items` |
| `reddit` | r/CryptoCurrency, r/CryptoMarkets, r/Bitcoin, r/ethereum, r/solana, r/SatoshiStreetBets (needs Reddit API keys, see below) | 3 min | `items` (kind `social`) |
| `coingecko` | Top 250 coins: price, market cap, volume, 1h/24h/7d change | 2 min | `coin_ticks` |
| `dexscreener` | Trending and newly listed DEX tokens: price, liquidity, volume, pair age | 2 min | `dex_ticks` |
| `defillama_hacks` | DeFiLlama hack database: amount stolen, technique, chains | 15 min | `items` (kind `hack`) |

Every run is logged in `collector_runs`, so later the terminal can show what the bot is doing and what's failing. News is de-duplicated by URL. All sources are free. Only Reddit needs a key: it blocks anonymous requests from servers, so create a free "script" app at https://www.reddit.com/prefs/apps and set `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` in `.env`. Without them the Reddit collector is skipped. A free CoinGecko Demo key raises its rate limit.

## What it analyses

| Job | What it does | Every |
|---|---|---|
| `classifier` | Claude reads each new item and tags it major / minor / noise, bullish / bearish / neutral, with a category, the coins it affects and a one-line summary. Major news and hacks become alerts. Needs `ANTHROPIC_API_KEY`. | 1 min |
| `token_watch` | Flags trending DEX tokens with thin liquidity, liquidity pulled by 50%+ in 6h, or a 70%+ crash. It also runs each token through RugCheck (Solana) or GoPlus (EVM chains) once a day and alerts on honeypots, mint/freeze authority, hidden owners, high taxes and similar. | 2 min |
| `hack_alerts` | Turns hack reports from the last 3 days into alerts straight away. Hacks over $10M are critical. | 2 min |

Results land in `item_analyses`, `alerts` and `token_safety`. Claude batches 25 items per request and caches its instructions, so a busy day costs a few dollars on Opus. Set `ANTHROPIC_MODEL=claude-haiku-5-5` to make it much cheaper.

## Signals and predictions

The `signals` job runs every hour (type `run signals` to force it):

- **Market call:** bullish, bearish or neutral for the next 24h. It's built from cap-weighted 1h and 24h momentum, how many coins are up, the bullish/bearish balance of the last 6h of news, and recent hacks.
- **Top runners:** coins with at least $5M daily volume, ranked on 1h and 24h momentum, trading volume relative to size, volume acceleration over 6h, and news sentiment. Stablecoins and wrapped tokens are excluded, and coins with a recent hack or rug alert are marked down.
- **DEX runners:** trending DEX tokens that are rising, have at least $100K liquidity, and passed RugCheck or GoPlus. They're still high risk.
- **Track record:** every call is saved and scored 24h later. A runner counts as a hit only if it went up and beat the market over the same window. The terminal's RECORD tab shows the hit rates over the last 30 days, so you can judge how much to trust the signals before acting on them.

These are signals from public data, not financial advice. No model reliably predicts crypto prices.

## Phone alerts

Set `NTFY_TOPIC` to get push notifications through the free [ntfy](https://ntfy.sh) app. Install it, subscribe to a long random topic name (anyone who knows the name can read it), and put the same name in `.env`. 
By default you get critical alerts (big hacks, major news, rug pulls), every move in or out of your wallets, the day's top runners at 12:00 UTC, and any time the market call flips. Change this with `NOTIFY_MIN_SEVERITY` and `NOTIFY_KINDS`.

### Telegram channel

The bot can also post to a Telegram channel: rug pulls (liquidity pulled out of a pool), possible rug pulls (tokens flagged by RugCheck or GoPlus), hacks of any size, and the daily top runners. Change the list with `TELEGRAM_KINDS`.

1. In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot`, and follow the prompts. It gives you a token.
2. Open your channel, go to Administrators, add your new bot, and let it post messages.
3. On the server, run `bash /opt/crawler/deploy/telegram.sh`. Paste the token when asked (it's hidden and stays in `/opt/crawler/.env`), then post any message in the channel. The script finds the channel, posts a test message, and restarts the bot.

## The terminal

`crawler serve` runs the bot and a web terminal on port 8000, protected by `DASHBOARD_PASSWORD` (any username).

- **Feed:** every item with its AI tags, live. Filter by major, minor, hacks, regulatory, social or noise, or search headlines.
- **Alerts:** rug risks, thin liquidity, crashes, hacks, major news and wallet moves as they happen.
- **Market:** top gainers over 1h and 24h, losers, majors, and trending DEX tokens with their liquidity. The header shows total market cap, how many coins are up, and the bullish/bearish split of the last 6h of news.
- **Wallets:** balances and USD value for your wallets, plus an alert whenever something comes in or goes out.
- **Bot:** each job's state with pause, resume and run-now buttons, a live log of what the bot is doing, and a command line (`help` lists the commands; press `/` to jump to it).

### Adding your wallets

Use public addresses only. Never paste a seed phrase or private key anywhere; the terminal refuses anything that looks like one.

```
wallet add solana <address> Phantom
wallet add solana <address> Axiom
wallet add solana <address> FOMO
wallet add ethereum <address> Ledger ETH
wallet add bitcoin <address> Ledger BTC
```

Axiom and FOMO use Solana wallets under the hood, so add the Solana address each app shows you. For a Ledger, add the receive address for each coin from Ledger Live. Solana wallets show SOL and every SPL token. EVM wallets show the native coin (ETH, BNB, POL) and Bitcoin shows BTC. ERC-20 tokens aren't tracked yet.

## Run it locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env   # set DASHBOARD_PASSWORD at least
crawler serve     # bot + terminal at http://localhost:8000
crawler once      # one round of every collector
crawler status    # what's been stored and the latest runs
crawler run       # keep collecting on a schedule
```

`crawler once --only rss coingecko` runs just the named collectors.

## Run it 24/7 on a server

On a fresh Ubuntu VPS (1 vCPU, 2 GB RAM is plenty), open a root shell (on DigitalOcean: Droplet → Access → Launch Droplet Console). Then run:

```bash
export GITHUB_TOKEN=<read-only token for this repo>
curl -fsSL -H "Authorization: token $GITHUB_TOKEN" \
  https://raw.githubusercontent.com/Noki3x/Crawler/main/deploy/setup.sh -o setup.sh
bash setup.sh
```

The script installs Docker and a firewall, asks for your Anthropic API key, and generates the terminal password, database password and ntfy topic. It serves the terminal over HTTPS at `https://<your-ip-with-dashes>.sslip.io` with a free certificate, so no domain is needed. At the end it prints the URL, password and ntfy topic. Run `bash /opt/crawler/deploy/setup.sh` again at any time to update to the latest code; your settings and data are kept.

Settings live in `/opt/crawler/.env`. After editing it, run `cd /opt/crawler && docker compose -f docker-compose.yml -f deploy/docker-compose.https.yml up -d`.

## Tests

```bash
pytest
```
