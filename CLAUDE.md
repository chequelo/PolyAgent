# PolyAgent v2 — AI Trading Agent

## Project Overview
Multi-strategy AI trading agent that combines Polymarket prediction betting, YES/NO arbitrage, crypto funding rate arbitrage, and cross-exchange spread detection. Runs as a Telegram bot on a Hetzner VPS in Amsterdam.

## Architecture
```
main.py                  — Telegram bot entry point, scheduled jobs, callback handlers
config.py                — Unified config from environment variables
notifier.py              — Telegram message formatting + inline buttons

polymarket/
  scanner.py             — Market discovery + YES/NO arb detection via Gamma API
  research.py            — Multi-source research (Tavily + market data + price history)
  estimator.py           — Superforecasting probability estimation via Claude API + Kelly sizing
  trader.py              — Order execution via py-clob-client (Polygon CLOB)
  micro_arb.py           — 15-min/5-min crypto market latency arbitrage (maker-only)

crypto/
  funding.py             — Funding rate arbitrage scanner via CCXT (Hyperliquid + comparison exchanges)
  spreads.py             — Cross-exchange spot spread detection via CCXT
  executor.py            — Hyperliquid trade execution via CCXT
```

## Tech Stack
- Python 3.12
- python-telegram-bot 21+ (async, with job_queue)
- anthropic SDK (Claude Sonnet 4 for predictions)
- py-clob-client 0.34+ (Polymarket CLOB API on Polygon)
- ccxt 4.4+ (Hyperliquid + Binance/Bybit/OKX for comparison)
- tavily-python (web search for research)
- httpx (async HTTP for Gamma API)
- Docker + docker-compose for deployment

## Key APIs
- Polymarket CLOB: https://clob.polymarket.com (chain_id=137, Polygon)
- Polymarket Gamma: https://gamma-api.polymarket.com (market discovery)
- Hyperliquid: via CCXT (no KYC, ETH wallet only)
- Anthropic: Claude Sonnet for superforecasting analysis
- Tavily: web search for market research
- Telegram Bot API: user interface

## 5 Strategies
1. **Prediction Bets** — AI superforecasting + Kelly criterion, min 8% edge
2. **PM YES+NO Arb** — Buy both outcomes when sum < $0.98 (risk-free after 2% fee)
3. **Micro-Arb** — 15-min/5-min crypto markets: detect spot price moves on Binance,
   place MAKER limit orders before PM odds adjust (0% fee + rebates).
   Inspired by Clawdbot ($313→$414K). Adapted for new taker fee structure.
4. **Funding Rate Arb** — Long spot + short perp when annualized > 10%
5. **Cross-Exchange Spreads** — Price diffs across CEXs, min 0.3% net spread

## Deployment
- VPS: Hetzner CX23 Amsterdam (~€3.49/mo), Ubuntu 24.04
- Container: Docker + docker-compose
- CI/CD: GitHub Actions → SSH deploy on push to main
- Auto-restart: docker compose restart policy

## Environment Variables (see .env.example)
Required: ANTHROPIC_API_KEY, TAVILY_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
POLYMARKET_PRIVATE_KEY, POLYMARKET_FUNDER_ADDRESS, HYPERLIQUID_PRIVATE_KEY, HYPERLIQUID_WALLET_ADDRESS

## Common Tasks
- Run locally: `python main.py` (needs .env)
- Build: `docker compose build`
- Deploy: `docker compose up -d`
- Logs: `docker compose logs -f`
- Test scan: Send `/scan` to the Telegram bot

## Budget
- Infra: ~$9/mo (VPS $3.80 + Anthropic API ~$5)
- Trading capital: $20 Polymarket + $20 Hyperliquid

## Important Notes
- Polymarket blocks datacenter IPs via Cloudflare — Amsterdam/EU VPS works
- Hyperliquid has no KYC, just needs an ETH wallet funded via Arbitrum
- py-clob-client signature_type=1 for email/Magic wallets, 0 for EOA/MetaMask
- Funding rates on Hyperliquid are hourly (not 8h like Binance)
- All trades require manual confirmation via Telegram inline buttons
