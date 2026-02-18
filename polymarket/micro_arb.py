"""Polymarket Micro-Arbitrage — 15-min & 5-min BTC/ETH/SOL crypto markets.

Strategy: MAKER-ONLY latency arbitrage
1. Monitor BTC/ETH/SOL spot prices on Binance via recent 1-minute candles
2. Detect sharp moves (>0.20% in last 5 minutes)
3. Place LIMIT (maker) orders on Polymarket 15-min/5-min markets BEFORE
   the Polymarket odds adjust — makers pay ZERO fees and earn daily rebates
4. When Polymarket odds catch up, our orders get filled at favorable prices

Why maker-only:
- Taker fees: up to 3.15% near 50/50 -> kills taker arb profits
- Maker fees: 0% + daily USDC rebates from the taker fee pool
"""
import json
import logging
import httpx
import ccxt.async_support as ccxt
from config import cfg

logger = logging.getLogger("polyagent.pm.micro_arb")

# Configuration
MICRO_ARB_CONFIG = {
    "spot_exchange": "binance",
    "assets": ["BTC", "ETH", "SOL"],
    "spot_pairs": {
        "BTC": "BTC/USDT",
        "ETH": "ETH/USDT",
        "SOL": "SOL/USDT",
    },
    "move_threshold_pct": 0.20,     # 0.20% move triggers signal
    "candle_count": 5,              # Look at last 5 one-minute candles
    "max_bet_per_trade": 3.0,
    "min_edge_pct": 0.8,            # Min 0.8% edge over implied probability
    "market_durations": ["15M", "5M", "1H"],
}

GAMMA_API = "https://gamma-api.polymarket.com"


async def _detect_spot_moves() -> dict[str, dict]:
    """Detect recent spot price moves using 1-minute candles from Binance."""
    exchange = ccxt.binance({"enableRateLimit": True})
    moves = {}
    try:
        await exchange.load_markets()
        for asset, pair in MICRO_ARB_CONFIG["spot_pairs"].items():
            try:
                # Fetch last N 1-minute candles
                candles = await exchange.fetch_ohlcv(
                    pair, timeframe="1m", limit=MICRO_ARB_CONFIG["candle_count"]
                )
                if not candles or len(candles) < 2:
                    continue

                # candle format: [timestamp, open, high, low, close, volume]
                oldest_close = candles[0][4]
                newest_close = candles[-1][4]
                move_pct = (newest_close - oldest_close) / oldest_close * 100

                if abs(move_pct) >= MICRO_ARB_CONFIG["move_threshold_pct"]:
                    moves[asset] = {
                        "asset": asset,
                        "direction": "UP" if move_pct > 0 else "DOWN",
                        "move_pct": move_pct,
                        "old_price": oldest_close,
                        "new_price": newest_close,
                        "candles": len(candles),
                    }
                    logger.info(f"Spot move: {asset} {move_pct:+.3f}% over {len(candles)} min")
                else:
                    logger.debug(f"No move: {asset} {move_pct:+.3f}% (threshold: {MICRO_ARB_CONFIG['move_threshold_pct']}%)")

            except Exception as e:
                logger.debug(f"Failed to fetch candles for {asset}: {e}")
    finally:
        await exchange.close()

    return moves


async def _fetch_active_crypto_markets(duration: str = "15M") -> list[dict]:
    """Fetch active short-duration crypto prediction markets from Gamma API."""
    async with httpx.AsyncClient(timeout=15) as client:
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
        is_duration_match = any(kw in question for kw in keywords)
        is_up_down = any(word in question for word in ["up", "down", "above", "below"])
        is_crypto = any(asset.lower() in question for asset in MICRO_ARB_CONFIG["assets"])

        if is_duration_match and is_up_down and is_crypto:
            try:
                prices_str = m.get("outcomePrices", "")
                prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
                if len(prices) < 2:
                    continue
                yes_price = float(prices[0])
                no_price = float(prices[1])

                filtered.append({
                    "id": m.get("id"),
                    "question": m.get("question", ""),
                    "slug": m.get("slug", ""),
                    "yes_price": yes_price,
                    "no_price": no_price,
                    "tokens": json.loads(m["clobTokenIds"]) if isinstance(m.get("clobTokenIds"), str) else m.get("clobTokenIds", []),
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


async def scan_micro_arb() -> list[dict]:
    """Full micro-arb scan: detect spot moves via candles + find mispriced PM markets."""
    opportunities = []

    # Detect moves using candle data (no sleep needed)
    moves = await _detect_spot_moves()

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

                if move["direction"] == "UP":
                    implied_prob = yes_price
                    prob_boost = min(abs(move["move_pct"]) * 15, 20) / 100
                    estimated_fair = min(implied_prob + prob_boost, 0.95)
                    edge = estimated_fair - yes_price
                    side = "YES"
                    entry_price = yes_price
                else:
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
                        cfg.poly_bankroll * 0.15,
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
                        "note": f"Spot {asset} moved {move['move_pct']:+.3f}% -> "
                                f"Buy {side} @ ${entry_price:.3f} (fair: ${estimated_fair:.3f})",
                    })

        except Exception as e:
            logger.error(f"Micro-arb scan failed for {duration}: {e}")

    opportunities.sort(key=lambda x: x["edge_pct"], reverse=True)
    logger.info(f"Micro-arb scan: {len(opportunities)} opportunities from {len(moves)} spot moves")
    return opportunities


async def execute_micro_arb(opportunity: dict) -> dict:
    """Execute micro-arb via MAKER limit order on Polymarket."""
    from polymarket.trader import _get_client, _validate_order

    client = _get_client()
    if not client:
        return {"success": False, "error": "CLOB client not configured"}

    tokens = opportunity["tokens"]
    if len(tokens) < 2:
        return {"success": False, "error": "No token IDs"}

    token_id = tokens[0] if opportunity["side"] == "YES" else tokens[1]
    price = round(opportunity["entry_price"], 2)
    size = round(opportunity["bet_size"] / price, 2) if price > 0 else 0

    err = _validate_order(price, size)
    if err:
        return {"success": False, "error": err}

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        order = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
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
