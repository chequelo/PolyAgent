# 🤖 PolyAgent v2 — AI Trading Agent

Multi-strategy AI agent that trades Polymarket predictions, exploits YES/NO arbitrage, captures crypto funding rates, and detects cross-exchange spreads.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  TELEGRAM BOT                        │
│  /scan  /crypto  /status  [Execute] [Skip] buttons  │
└──────────────┬──────────────────────┬───────────────┘
               │                      │
    ┌──────────▼──────────┐ ┌────────▼─────────────┐
    │   POLYMARKET ($20)   │ │    CRYPTO ($20)       │
    │                      │ │                       │
    │ ① Prediction Bets    │ │ ③ Funding Rate Arb    │
    │   Scanner → Research │ │   CCXT multi-exchange  │
    │   → Superforecaster  │ │   Hyperliquid exec     │
    │   → Kelly sizing     │ │                       │
    │                      │ │ ④ Cross-Exchange      │
    │ ② YES+NO Arbitrage   │ │   Spread Detection    │
    │   Buy both sides     │ │   Alert + exec        │
    │   when sum < $0.98   │ │                       │
    └──────────────────────┘ └───────────────────────┘
               │                      │
    ┌──────────▼──────────┐ ┌────────▼─────────────┐
    │  py-clob-client      │ │  CCXT → Hyperliquid   │
    │  Polygon (USDC)      │ │  Arbitrum (USDC)      │
    └──────────────────────┘ └───────────────────────┘
```

## 4 Strategies

| # | Strategy | Source | Budget | Edge |
|---|----------|--------|--------|------|
| 1 | **Prediction Bets** | AI superforecasting + multi-source research | $20 PM | 8%+ edge via Kelly |
| 2 | **PM YES+NO Arb** | Buy both outcomes when YES+NO < $0.98 | $20 PM | Risk-free after fees |
| 3 | **Funding Rate Arb** | Long spot + short perp when funding is high | $20 HL | 10%+ annualized |
| 4 | **Cross-Exchange Spreads** | Price differences across CEXs | $20 HL | 0.3%+ per trade |

## Budget Breakdown

| Item | Cost/month |
|------|-----------|
| Hetzner CX23 Amsterdam (2vCPU, 4GB, 40GB) | ~$3.80 |
| Anthropic API (Claude Sonnet, ~6 scans/day) | ~$5.00 |
| Tavily (free tier, 1000 searches/mo) | $0.00 |
| Telegram Bot | $0.00 |
| **Total infrastructure** | **~$9/mo** |
| Trading: Polymarket wallet | $20 (one-time) |
| Trading: Hyperliquid wallet | $20 (one-time) |

---

## Step-by-Step Setup Guide

### Step 1: Create Accounts & API Keys

#### 1.1 Telegram Bot
```
1. Open Telegram, search @BotFather
2. Send /newbot, name it "PolyAgent"
3. Save the bot token: 7000000000:AAxxxx...
4. Search @userinfobot, send /start
5. Save your chat ID: 123456789
```

#### 1.2 Anthropic API Key
```
1. Go to https://console.anthropic.com
2. Create API key
3. Add $10 credit (lasts ~2 months)
```

#### 1.3 Tavily API Key
```
1. Go to https://app.tavily.com
2. Sign up (free tier = 1000 searches/month)
3. Copy API key
```

#### 1.4 Polymarket Wallet
```
1. Go to https://polymarket.com, create account
2. Deposit $20 USDC
3. Export private key:
   - Email login: https://reveal.magic.link/polymarket
   - MetaMask: Settings → Security → Export Private Key
4. Your funder address = your Polymarket profile address
```

#### 1.5 Hyperliquid Wallet
```
1. Create an ETH wallet (MetaMask or any wallet)
2. Save private key and public address
3. Go to https://app.hyperliquid.xyz
4. Connect wallet
5. Deposit $20 USDC via Arbitrum bridge
   - Bridge USDC to Arbitrum: https://bridge.arbitrum.io
   - Then deposit to Hyperliquid from the app
```

### Step 2: Create Hetzner VPS

```bash
# 1. Go to https://console.hetzner.cloud
# 2. Sign up (they accept credit card)
# 3. Create new project → "PolyAgent"
# 4. Add Server:
#    - Location: Falkenstein or Helsinki (Amsterdam not available for CX)
#      OR use CPX11 in Amsterdam for ~€4.85/mo
#    - Image: Ubuntu 24.04
#    - Type: CX23 (2 vCPU, 4GB RAM, 40GB) = €3.49/mo
#    - SSH Key: Add your public key
#    - Create

# 5. Note the IP address
```

### Step 3: Setup VPS

```bash
# From your local machine:

# Upload and run the setup script
scp setup_vps.sh root@YOUR_VPS_IP:~
ssh root@YOUR_VPS_IP 'chmod +x setup_vps.sh && ./setup_vps.sh'
```

### Step 4: Deploy the Agent

```bash
# Option A: Upload directly
scp -r ./* polyagent@YOUR_VPS_IP:~/app/
ssh polyagent@YOUR_VPS_IP 'nano ~/app/.env'  # Fill in your keys
ssh polyagent@YOUR_VPS_IP '~/deploy.sh'

# Option B: Via GitHub
ssh polyagent@YOUR_VPS_IP
cd ~/app
git clone https://github.com/chequelo/prediction_markets.git .
nano .env  # Fill in your keys
~/deploy.sh
```

### Step 5: Verify

```bash
# Check logs
ssh polyagent@YOUR_VPS_IP '~/logs.sh'

# Or in Telegram, send:
/status  — Check all connections
/scan    — Trigger manual full scan
/crypto  — Trigger crypto-only scan
```

---

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/scan` | Full scan: predictions + arbs + crypto |
| `/crypto` | Quick scan: funding rates + spreads only |
| `/status` | Show balances, API status, pending signals |
| `/help` | List commands |

Each opportunity shows **Execute** / **Skip** inline buttons.

## Scan Schedule

- **Full scan** (predictions + all crypto): Every 4 hours
- **Crypto scan** (funding + spreads): Every 30 minutes

## How the Superforecaster Works

The prediction engine uses Claude to run 3 independent estimation methods:

1. **Base Rate Analysis** — Historical frequency of similar events
2. **Evidence Weighing** — Current news, data, momentum from multiple sources
3. **Market Analysis + Contrarian Check** — Is the market biased? Research shows Polymarket slightly overestimates probabilities

The final probability is synthesized and compared to market price. Only bets with **8%+ edge** and positive Kelly criterion are recommended.

## Risk Management

- Max $5 per prediction bet (Kelly-sized)
- Max $10 per PM arbitrage
- Max $10 per funding rate position (per side)
- Max 30% of bankroll per spread trade
- All trades require manual confirmation via Telegram buttons

## Updating

```bash
ssh polyagent@YOUR_VPS_IP
cd ~/app
git pull
~/deploy.sh
```

## Monitoring

```bash
# Live logs
ssh polyagent@YOUR_VPS_IP '~/logs.sh'

# Restart
ssh polyagent@YOUR_VPS_IP 'cd ~/app && docker compose restart'

# Stop
ssh polyagent@YOUR_VPS_IP 'cd ~/app && docker compose down'
```
