"""Polymarket Scanner — Market discovery + YES/NO arbitrage detection"""
import json
import httpx
import logging
from config import cfg

logger = logging.getLogger("polyagent.pm.scanner")
GAMMA_API = "https://gamma-api.polymarket.com"


async def scan_prediction_markets() -> list[dict]:
    """Scan for prediction markets with edge potential."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{GAMMA_API}/markets", params={
            "active": "true", "closed": "false",
            "limit": 200, "order": "volume24hr", "ascending": "false",
        })
        resp.raise_for_status()
        markets = resp.json()

    filtered = []
    for m in markets:
        try:
            volume = float(m.get("volume", 0) or 0)
            liquidity = float(m.get("liquidity", 0) or 0)
            spread = float(m.get("spread", 1) or 1)
            best_bid = float(m.get("bestBid", 0) or 0)
            best_ask = float(m.get("bestAsk", 1) or 1)
        except (ValueError, TypeError):
            continue

        if volume < cfg.pm_min_volume:
            continue
        if liquidity < cfg.pm_min_liquidity:
            continue
        if spread > cfg.pm_max_spread:
            continue
        # Skip markets too close to resolved (>95% or <5%)
        mid = (best_bid + best_ask) / 2 if best_ask > 0 else 0
        if mid > 0.95 or mid < 0.05:
            continue

        filtered.append({
            "id": m.get("id"),
            "question": m.get("question", ""),
            "slug": m.get("slug", ""),
            "volume": volume,
            "liquidity": liquidity,
            "spread": spread,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid": mid,
            "end_date": m.get("endDate"),
            "outcomes": m.get("outcomes", []),
            "tokens": json.loads(m["clobTokenIds"]) if isinstance(m.get("clobTokenIds"), str) else m.get("clobTokenIds", []),
            "category": m.get("category", ""),
        })

    logger.info(f"Prediction scan: {len(filtered)} markets from {len(markets)} total")
    return filtered


async def scan_arb_opportunities() -> list[dict]:
    """Scan for YES+NO arbitrage: buy both sides when sum < $1 - fees."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{GAMMA_API}/markets", params={
            "active": "true", "closed": "false",
            "limit": 200, "order": "volume24hr", "ascending": "false",
        })
        resp.raise_for_status()
        markets = resp.json()

    opportunities = []
    for m in markets:
        raw_tokens = m.get("clobTokenIds", [])
        tokens = json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
        if not isinstance(tokens, list) or len(tokens) != 2:
            continue

        try:
            # For arb we need the ASK prices (cost to buy)
            best_ask_yes = float(m.get("bestAsk", 0) or 0)
            best_bid_yes = float(m.get("bestBid", 0) or 0)

            # The NO side ask can be inferred: NO_ask ≈ 1 - YES_bid
            # But better to check actual orderbook. For now use gamma data.
            # In a binary market: YES_ask + NO_ask should be ~$1.00
            # If < $1.00, there's arb. Polymarket takes 2% fee on winner.

            # Gamma gives us outcomePrices for both sides
            prices_str = m.get("outcomePrices", "")
            if not prices_str:
                continue

            # outcomePrices is a JSON string like "[0.55, 0.45]"
            prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
            if len(prices) != 2:
                continue

            yes_price = float(prices[0])
            no_price = float(prices[1])
            total_cost = yes_price + no_price

            # After fees: profit = $1.00 - total_cost - fee
            # Fee is 2% on the winning side = 0.02 * 1.00 = $0.02
            profit_per_dollar = 1.0 - total_cost - cfg.pm_arb_fee

            liquidity = float(m.get("liquidity", 0) or 0)
            volume = float(m.get("volume", 0) or 0)

            if profit_per_dollar > cfg.pm_arb_min_profit and liquidity > 1000:
                opportunities.append({
                    "id": m.get("id"),
                    "question": m.get("question", ""),
                    "yes_price": yes_price,
                    "no_price": no_price,
                    "total_cost": total_cost,
                    "profit_per_dollar": profit_per_dollar,
                    "profit_pct": profit_per_dollar / total_cost * 100,
                    "liquidity": liquidity,
                    "volume": volume,
                    "tokens": tokens,
                })
        except (ValueError, TypeError, json.JSONDecodeError):
            continue

    opportunities.sort(key=lambda x: x["profit_per_dollar"], reverse=True)
    logger.info(f"Arb scan: {len(opportunities)} opportunities found")
    return opportunities[:10]
