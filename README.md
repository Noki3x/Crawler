# Crawler

A bot that crawls the web for news and data that move the crypto market, built for personal trading decisions.

The plan has four steps:

1. **Collectors and server** (this code): pull news, social posts, prices, DEX liquidity, hacks and regulator notices into one database, 24/7.
2. **AI analysis**: Claude tags each item as major or minor and bullish or bearish, and names the coins it affects. Token-safety checks drive rug-pull and low-liquidity warnings.
3. **Terminal app**: a live dashboard to control the bot, see what it's reading, and track your wallets by public address.
4. **Predictions and alerts**: daily "top runners" and market-direction signals, backtested, with phone alerts.

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

## Run it locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
crawler once      # one round of every collector
crawler status    # what's been stored and the latest runs
crawler run       # keep collecting on a schedule
```

`crawler once --only rss coingecko` runs just the named collectors.

## Run it 24/7 on a server

Any small Linux VPS works (1 vCPU, 1–2 GB RAM). Install Docker, then:

```bash
git clone https://github.com/Noki3x/Crawler.git && cd Crawler
cp .env.example .env    # then set a real POSTGRES_PASSWORD
docker compose up -d --build
docker compose logs -f crawler
docker compose exec crawler crawler status
```

Both containers restart automatically if they crash or the server reboots.

## Tests

```bash
pytest
```
