"""Polymarket Micro-Arbitrage — 15-min & 5-min BTC/ETH/SOL crypto markets.

Based on the Clawdbot/Moltbot strategy ($313 → $414K) but adapted for
Polymarket's new dynamic taker fee structure (up to 3.15% near 50/50).

Strategy: MAKER-ONLY latency arbitrage
─────────────────────────────────────
1. Monitor BTC/ETH/SOL spot prices on Binance/Bybit in real-time via CCXT
2. Detect sharp moves (>0.3% in 60 seconds)
3. Place LIMIT (maker) orders on Polymarket 15-min/5-min markets BEFORE
   the Polymarket odds adjust — makers pay ZERO fees and earn daily rebates
4. When Polymarket odds catch up, our orders get filled at favorable prices

Why maker-only:
- Taker fees: up to 3.15% near 50/50 → kills taker arb profits
- Maker fees: 0% + daily USDC rebates from the taker fee pool
- We provide liquidity on the "correct" side after detecting the spot move

Market types monitored:
- 15-minute Up/Down: BTC, ETH, SOL, XRP
- 5-minute Up/Down: BTC (newer, potentially less efficient)
- Hourly Up/Down: BTC, ETH (wider windows, less competition)

Risk: Orders may not fill if Polymarket adjusts too fast.
      Spot reversal before 15-min/5-min window closes.
"""
import asyncio
import time
import logging
import httpx
import ccxt.async_support as ccxt
from config import cfg

logger = logging.getLogger("polyagent.pm.micro_arb")

# ── Configuration ──
MICRO_ARB_CONFIG = {
    "spot_exchange": "binance",
    "assets": ["BTC", "ETH", "SOL"],
    "spot_pairs": {
        "BTC": "BTC/USDT",
        "ETH": "ETH/USDT",
        "SOL": "SOL/USDT",
    },
    "move_threshold_pct": 0.30,     # 0.30% move triggers signal
    "lookback_seconds": 60,         # Detect moves within last 60 seconds
    "scan_interval_seconds": 10,    # Check every 10 seconds
    "max_bet_per_trade": 3.0,       # Max $3 per micro-arb trade
    "min_edge_pct": 1.0,            # Min 1% edge over implied probability
    "market_durations": ["15M", "5M", "1H"],  # Market windows to scan
}

# ── Gamma API helpers ──
GAMMA_API = "https://gamma-api.polymarket.com"


async def _fetch_active_crypto_markets(duration: str = "15M") -> list[dict]:
    """Fetch active short-duration crypto prediction markets from Gamma API."""
    async with httpx.AsyncClient(timeout=15) as client:
        # Search for active crypto up/down markets
        resp = await client.get(f"{GAMMA_API}/markets", params={
            "active": "true",
            "closed": "false",
            "limit": 50,
            "tag": "crypto",
        })
        if resp.status_code != 200:
            return []
        markets = resp.json()

    # Filter for short-duration crypto price markets
    filtered = []
    duration_keywords = {
        "5M": ["5-minute", "5 minute", "5min"],
        "15M": ["15-minute", "15 minute", "15min"],
        "1H": ["hourly", "1-hour", "1 hour", "1hr"],
    }
    keywords = duration_keywords.get(duration, [duration.lower()])

    for m in markets:
        question = (m.get("question", "") or "").lower()
        # Check if it's a short-duration up/down market
        is_duration_match = any(kw in question for kw in keywords)
        is_up_down = any(word in question for word in ["up", "down", "above", "below"])
        is_crypto = any(asset.lower() in question for asset in MICRO_ARB_CONFIG["assets"])

        if is_duration_match and is_up_down and is_crypto:
            try:
                import json
                prices_str = m.get("outcomePrices", "")
                prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
                if len(prices) >= 2:
                    yes_price = float(prices[0])
                    no_price = float(prices[1])
                else:
                    continue

                filtered.append({
                    "id": m.get("id"),
                    "question": m.get("question", ""),
                    "slug": m.get("slug", ""),
                    "yes_price": yes_price,
                    "no_price": no_price,
                    "tokens": m.get("clobTokenIds", []),
                    "end_date": m.get("endDate"),
                    "volume": float(m.get("volume", 0) or 0),
                    "liquidity": float(m.get("liquidity", 0) or 0),
                    "duration": duration,
                    "asset": next(
                        (a for a in MICRO_ARB_CONFIG["assets"] if a.lower() in question),
                        "BTC"
                    ),
                })
            except (ValueError, TypeError):
                continue

    return filtered


async def _get_spot_prices() -> dict[str, float]:
    """Get current spot prices from Binance."""
    exchange = ccxt.binance({"enableRateLimit": True})
    prices = {}
    try:
        await exchange.load_markets()
        for asset, pair in MICRO_ARB_CONFIG["spot_pairs"].items():
            try:
                ticker = await exchange.fetch_ticker(pair)
                if ticker and ticker.get("last"):
                    prices[asset] = ticker["last"]
            except Exception:
                pass
    finally:
        await exchange.close()
    return prices


class SpotMonitor:
    """Tracks recent spot price history to detect sharp moves."""

    def __init__(self):
        self.history: dict[str, list[tuple[float, float]]] = {}  # asset -> [(timestamp, price)]
        self.max_history = 120  # Keep 2 minutes of data

    def add_price(self, asset: str, price: float):
        ts = time.time()
        if asset not in self.history:
            self.history[asset] = []
        self.history[asset].append((ts, price))
        # Trim old data
        cutoff = ts - self.max_history
        self.history[asset] = [(t, p) for t, p in self.history[asset] if t > cutoff]

    def detect_move(self, asset: str) -> dict | None:
        """Detect if asset moved > threshold in lookback window."""
        if asset not in self.history or len(self.history[asset]) < 2:
            return None

        now = time.time()
        lookback = MICRO_ARB_CONFIG["lookback_seconds"]
        recent = [(t, p) for t, p in self.history[asset] if t > now - lookback]

        if len(recent) < 2:
            return None

        oldest_price = recent[0][1]
        newest_price = recent[-1][1]
        move_pct = (newest_price - oldest_price) / oldest_price * 100

        if abs(move_pct) >= MICRO_ARB_CONFIG["move_threshold_pct"]:
            return {
                "asset": asset,
                "direction": "UP" if move_pct > 0 else "DOWN",
                "move_pct": move_pct,
                "old_price": oldest_price,
                "new_price": newest_price,
                "seconds": now - recent[0][0],
            }
        return None


async def scan_micro_arb() -> list[dict]:
    """Full micro-arb scan: detect spot moves + find mispriced PM markets."""
    opportunities = []
    monitor = SpotMonitor()

    # Get current + recent spot prices (2 samples, 10s apart)
    prices_1 = await _get_spot_prices()
    for asset, price in prices_1.items():
        monitor.add_price(asset, price)

    await asyncio.sleep(5)

    prices_2 = await _get_spot_prices()
    for asset, price in prices_2.items():
        monitor.add_price(asset, price)

    # Check for moves
    moves = {}
    for asset in MICRO_ARB_CONFIG["assets"]:
        move = monitor.detect_move(asset)
        if move:
            moves[asset] = move
            logger.info(f"Spot move detected: {asset} {move['direction']} {move['move_pct']:.3f}%")

    if not moves:
        logger.debug("No significant spot moves detected")
        return []

    # Find active PM markets for moved assets
    for duration in MICRO_ARB_CONFIG["market_durations"]:
        try:
            markets = await _fetch_active_crypto_markets(duration)
            for market in markets:
                asset = market["asset"]
                if asset not in moves:
                    continue

                move = moves[asset]
                yes_price = market["yes_price"]
                no_price = market["no_price"]

                # If spot moved UP, "Up" (YES) should be worth more
                # If PM still shows old odds, there's an edge
                if move["direction"] == "UP":
                    # We want to BUY YES (before odds adjust up)
                    # Fair value of YES should be higher than current price
                    implied_prob = yes_price
                    # Simple model: if spot moved 0.5% up in 60s, UP is more likely
                    # Rough heuristic: 0.3% move ≈ 5% probability shift
                    prob_boost = min(abs(move["move_pct"]) * 15, 20) / 100  # Max 20% boost
                    estimated_fair = min(implied_prob + prob_boost, 0.95)
                    edge = estimated_fair - yes_price
                    side = "YES"
                    entry_price = yes_price

                else:  # DOWN
                    # We want to BUY NO (before odds adjust)
                    implied_prob = no_price
                    prob_boost = min(abs(move["move_pct"]) * 15, 20) / 100
                    estimated_fair = min(implied_prob + prob_boost, 0.95)
                    edge = estimated_fair - no_price
                    side = "NO"
                    entry_price = no_price

                edge_pct = edge * 100

                if edge_pct >= MICRO_ARB_CONFIG["min_edge_pct"]:
                    bet_size = min(
                        MICRO_ARB_CONFIG["max_bet_per_trade"],
                        cfg.poly_bankroll * 0.15,  # Max 15% of bankroll
                    )

                    opportunities.append({
                        "type": "micro_arb",
                        "market_id": market["id"],
                        "question": market["question"],
                        "asset": asset,
                        "duration": duration,
                        "side": side,
                        "entry_price": entry_price,
                        "estimated_fair": round(estimated_fair, 4),
                        "edge_pct": round(edge_pct, 2),
                        "spot_move": move,
                        "bet_size": bet_size,
                        "tokens": market["tokens"],
                        "strategy": "MAKER_LIMIT",
                        "note": f"Spot {asset} moved {move['move_pct']:+.3f}% → "
                                f"Buy {side} @ ${entry_price:.3f} (fair: ${estimated_fair:.3f})",
                    })

        except Exception as e:
            logger.error(f"Micro-arb scan failed for {duration}: {e}")

    opportunities.sort(key=lambda x: x["edge_pct"], reverse=True)
    logger.info(f"Micro-arb scan: {len(opportunities)} opportunities from {len(moves)} spot moves")
    return opportunities


async def execute_micro_arb(opportunity: dict) -> dict:
    """Execute micro-arb via MAKER limit order on Polymarket."""
    if not cfg.poly_private_key:
        return {"success": False, "error": "Polymarket keys not configured"}

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        client = ClobClient(
            "https://clob.polymarket.com",
            key=cfg.poly_private_key,
            chain_id=137,
            signature_type=1,
            funder=cfg.poly_funder_address,
        )
        client.set_api_creds(client.create_or_derive_api_creds())

        tokens = opportunity["tokens"]
        token_id = tokens[0] if opportunity["side"] == "YES" else tokens[1]
        price = opportunity["entry_price"]
        size = opportunity["bet_size"] / price if price > 0 else 0

        # Place as GTC LIMIT order (maker = no fees + rebates)
        order = OrderArgs(
            token_id=token_id,
            price=round(price, 3),
            size=round(size, 2),
            side=BUY,
        )
        signed = client.create_order(order)
        resp = client.post_order(signed, OrderType.GTC)

        logger.info(
            f"Micro-arb order: {opportunity['side']} {opportunity['asset']} "
            f"{opportunity['duration']} @ ${price:.3f} x {size:.1f} "
            f"(edge: {opportunity['edge_pct']:.1f}%)"
        )
        return {
            "success": True,
            "response": str(resp),
            "side": opportunity["side"],
            "price": price,
            "size": size,
            "edge_pct": opportunity["edge_pct"],
        }

    except Exception as e:
        logger.error(f"Micro-arb execution failed: {e}")
        return {"success": False, "error": str(e)}
