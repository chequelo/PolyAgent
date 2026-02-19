# 🤖 PolyAgent v2 — AI Trading Agent

Multi-strategy AI agent that trades Polymarket predictions, exploits YES/NO arbitrage, captures crypto funding rates, and detects cross-exchange spreads. Real-time WebSocket monitoring with automatic position management.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  TELEGRAM BOT                        │
│  /scan  /crypto  /positions  /status  /help          │
└──────────────┬──────────────────────┬───────────────┘
               │                      │
    ┌──────────▼──────────┐ ┌────────▼─────────────┐
    │   POLYMARKET ($20)   │ │    CRYPTO ($20)       │
    │                      │ │                       │
    │ ① Prediction Bets    │ │ ④ Funding Rate Arb    │
    │   Scanner → Research │ │   CCXT multi-exchange  │
    │   → Superforecaster  │ │   Hyperliquid exec     │
    │   → Kelly sizing     │ │                       │
    │                      │ │ ⑤ Cross-Exchange      │
    │ ② YES+NO Arbitrage   │ │   Spread Detection    │
    │   Buy both sides     │ │   Auto-execute         │
    │   when sum < $0.98   │ │                       │
    │                      │ │                       │
    │ ③ Micro-Arbitrage    │ │                       │
    │   Spot move → PM     │ │                       │
    │   Maker-only orders  │ │                       │
    └──────────────────────┘ └───────────────────────┘
               │                      │
    ┌──────────▼──────────┐ ┌────────▼─────────────┐
    │  py-clob-client      │ │  CCXT → Hyperliquid   │
    │  Polygon (USDC)      │ │  Arbitrum (USDC)      │
    └──────────────────────┘ └───────────────────────┘
```

## File Structure

```
main.py                  — Telegram bot, scheduled jobs, commands
config.py                — Unified config from environment variables
notifier.py              — Telegram message formatting
positions.py             — JSON-based position tracking (data/positions.json)
position_manager.py      — Periodic exit checker (funding/spread/prediction)
watcher.py               — Real-time WebSocket monitors (4 watchers)

polymarket/
  scanner.py             — Market discovery + YES/NO arb detection via Gamma API
  research.py            — Multi-source research (Tavily + market data + price history)
  estimator.py           — Superforecasting probability estimation (Claude + Kelly)
  trader.py              — Order execution via py-clob-client (buy + sell)
  micro_arb.py           — Crypto market latency arbitrage (maker-only)

crypto/
  funding.py             — Funding rate arbitrage scanner via CCXT
  spreads.py             — Cross-exchange spot spread detection via CCXT
  executor.py            — Hyperliquid/Binance trade execution
```

## 5 Strategies

| # | Strategy | How | Edge |
|---|----------|-----|------|
| 1 | **Prediction Bets** | AI superforecasting (Tetlock 5-step) + Kelly criterion | 3%+ edge, many small bets |
| 2 | **PM YES+NO Arb** | Buy both outcomes when YES+NO < $0.98 | Risk-free after 2% fee |
| 3 | **Micro-Arbitrage** | Binance spot move → PM maker limit order before odds adjust | 0% maker fee + rebates |
| 4 | **Funding Rate Arb** | Short perp when funding rate is high (Hyperliquid) | 5%+ annualized |
| 5 | **Cross-Exchange Spreads** | Price differences across CEXs (Binance/Bybit/OKX) | 0.1%+ per trade |

## Real-Time Monitoring (WebSocket)

All position monitoring is event-driven via WebSocket — no more 5-minute polling delays.

```
Bot starts
  └─ Watcher.start() → 4 asyncio tasks
      │
      ├─ ① PM WebSocket (wss://ws-subscriptions-clob.polymarket.com)
      │     Subscribe to token_ids of open prediction positions
      │     On price_change/book → Level 1 check (free, instant)
      │     If price move >5% or edge inverted → Level 2 re-eval (Claude ~$0.05)
      │       ├─ edge >= 3% → HOLD (silent)
      │       ├─ 1% <= edge < 3% → ALERT notification
      │       └─ edge < 1% → auto-SELL + notification with PnL
      │     Auto-subscribe/unsubscribe as positions open/close
      │     PING/PONG heartbeat every 10s
      │
      ├─ ② Spread Watcher (ccxt.pro.watch_ticker)
      │     Watch tickers on both exchanges per spread position
      │     spread ≤ 0.03% → close both legs (spread converged)
      │     spread ≥ 0.5% → close both legs (profit take)
      │
      ├─ ③ Micro-Arb Watcher (ccxt.pro.watch_ticker on Binance)
      │     Stream BTC/ETH/SOL spot prices
      │     Detect >0.2% move in 5-min window
      │     → Find mispriced PM markets → execute maker limit order
      │     5-min cooldown per asset after execution
      │
      └─ ④ Funding Watcher (ccxt.pro.watch_positions on Hyperliquid)
            Detect when TP/SL orders execute (position disappears)
            → Mark position closed + notify with PnL
```

### Why WebSocket > Polling

| | Old (polling) | New (WebSocket) |
|---|---|---|
| Latency | Up to 5 min delay | < 1 second |
| API calls | 12/hour per position | 0 (push-based) |
| Cost | ~$6/day for 10 positions | ~$0.15/day (Level 2 only on trigger) |
| Spot move detection | Every 30 min via candles | Real-time ticker stream |
| TP/SL detection | Poll every 5 min | Instant via watch_positions |

The polling cron still runs every 30 min as a fallback in case a WebSocket disconnects.

## Prediction Position Lifecycle

```
/scan → Scanner finds markets → Research (Tavily) → Estimator (Claude)
  │
  ├─ edge < 3% → SKIP
  └─ edge >= 3% → Execute bet on CLOB
       │
       └─ Position created with:
            token_id, estimated_prob, original_thesis, entry_price
            │
            └─ Watcher monitors in real-time:
                 ├─ Price stable → HOLD (free, no API calls)
                 ├─ Price moves >5% → Re-research + Re-estimate (~$0.05)
                 │    ├─ Edge still healthy → HOLD
                 │    ├─ Edge thinning → ALERT notification
                 │    └─ Edge gone → Auto-SELL on CLOB
                 └─ Market resolves → Position settles automatically
```

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/scan` | Full scan: predictions + arbs + crypto |
| `/crypto` | Quick scan: funding rates + spreads only |
| `/positions` | Show all open positions with edge, PnL, totals |
| `/status` | Balances, API status, configuration |
| `/proxytest` | Test proxy and CLOB connectivity |
| `/balancetest` | Raw balance output from each exchange |
| `/help` | List commands |

### `/positions` output

Shows per-position: side, entry price → current price, estimated probability, current edge (color-coded), size, age, last re-eval time, thesis. Subtotals per strategy and grand total.

## Tech Stack

- **Python 3.12**
- **python-telegram-bot 21+** (async, job_queue)
- **anthropic SDK** (Claude Sonnet 4 for superforecasting)
- **py-clob-client 0.34+** (Polymarket CLOB API on Polygon)
- **ccxt 4.4+** (REST + WebSocket via ccxt.pro)
- **websockets 12+** (Polymarket CLOB WebSocket)
- **tavily-python** (web search for research)
- **httpx** (async HTTP for Gamma API)
- **hyperliquid-python-sdk** (native HL execution with atomic TP/SL)
- **Docker + docker-compose** for deployment

## Budget

| Item | Cost/month |
|------|-----------|
| Hetzner CX23 Amsterdam (2vCPU, 4GB, 40GB) | ~$3.80 |
| Anthropic API (Claude Sonnet, scans + re-evals) | ~$5.00 |
| Tavily (free tier, 1000 searches/mo) | $0.00 |
| Telegram Bot | $0.00 |
| **Total infrastructure** | **~$9/mo** |
| Trading: Polymarket wallet | $20 (one-time) |
| Trading: Hyperliquid wallet | $20 (one-time) |

## Setup

### Prerequisites

1. **Telegram Bot** — create via @BotFather, save token + chat ID
2. **Anthropic API Key** — from console.anthropic.com
3. **Tavily API Key** — from app.tavily.com (free tier)
4. **Polymarket Wallet** — deposit $20 USDC, export private key
5. **Hyperliquid Wallet** — ETH wallet funded via Arbitrum with $20 USDC

### Environment Variables

```bash
# Required
TELEGRAM_BOT_TOKEN=7000000000:AAxxxx...
TELEGRAM_CHAT_ID=123456789
ANTHROPIC_API_KEY=sk-ant-...
TAVILY_API_KEY=tvly-...
POLYMARKET_PRIVATE_KEY=0x...
POLYMARKET_FUNDER_ADDRESS=0x...
HYPERLIQUID_PRIVATE_KEY=0x...
HYPERLIQUID_WALLET_ADDRESS=0x...

# Optional
POLY_BANKROLL=20
HL_BANKROLL=20
POLY_CLOB_PROXY_URL=https://clob.polymarket.com
POLY_PROXY_URL=http://user:pass@proxy:port
BINANCE_API_KEY=...
BINANCE_SECRET=...
```

### Run Locally

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in your keys
python main.py
```

### Deploy (Docker)

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

### Deploy (VPS + GitHub Actions)

Push to `main` triggers auto-deploy via SSH to the Hetzner VPS.

```bash
git push origin main  # deploys automatically
```

## Risk Management

- Prediction bets: max $2/bet, Kelly-sized, 15% fractional Kelly
- PM arbitrage: max $10 per arb
- Funding positions: max $10/side, exchange-native TP/SL (3% TP, 5% SL)
- Spread positions: max $10/side, SL at -3% per leg
- Category exposure cap: max 30% of bankroll in one category
- All positions monitored in real-time via WebSocket
- Auto-sell predictions when edge drops below 1%

## Monitoring

```bash
# Live logs
docker compose logs -f

# Restart
docker compose restart

# Stop
docker compose down
```

In Telegram:
- `/status` — verify all API connections
- `/positions` — check all open positions with live edge data
- `/scan` — trigger manual full scan
