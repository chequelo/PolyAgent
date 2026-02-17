"""Hyperliquid Executor — Trade execution via CCXT"""
import logging
import ccxt.async_support as ccxt
from config import cfg

logger = logging.getLogger("polyagent.crypto.executor")

_hl_client = None


async def _get_client() -> ccxt.hyperliquid | None:
    global _hl_client
    if _hl_client is None and cfg.hl_private_key:
        _hl_client = ccxt.hyperliquid({
            "privateKey": cfg.hl_private_key,
            "walletAddress": cfg.hl_wallet_address,
            "enableRateLimit": True,
        })
        await _hl_client.load_markets()
    return _hl_client


async def execute_funding_arb(opportunity: dict) -> dict:
    """Execute funding rate arbitrage: spot + perp hedge on Hyperliquid."""
    client = await _get_client()
    if not client:
        return {"success": False, "error": "Hyperliquid client not configured"}

    pair = opportunity["pair"]
    direction = opportunity["direction"]
    size_usd = opportunity["position_size"]

    try:
        # Get current price for sizing
        ticker = await client.fetch_ticker(opportunity["hl_symbol"])
        if not ticker or not ticker.get("last"):
            return {"success": False, "error": "Could not fetch price"}

        price = ticker["last"]
        quantity = size_usd / price

        results = []

        if direction == "short_perp":
            # Strategy: Buy spot + Short perp
            # 1. Buy spot
            spot_symbol = pair  # e.g., "BTC/USDT"
            if spot_symbol in client.symbols:
                spot_order = await client.create_order(
                    spot_symbol, "market", "buy", quantity
                )
                results.append({"leg": "spot_buy", "order": str(spot_order.get("id", ""))})

            # 2. Short perpetual
            perp_order = await client.create_order(
                opportunity["hl_symbol"], "market", "sell", quantity,
                params={"reduceOnly": False}
            )
            results.append({"leg": "perp_short", "order": str(perp_order.get("id", ""))})

        elif direction == "long_perp":
            # Strategy: Long perp (collect negative funding)
            perp_order = await client.create_order(
                opportunity["hl_symbol"], "market", "buy", quantity,
                params={"reduceOnly": False}
            )
            results.append({"leg": "perp_long", "order": str(perp_order.get("id", ""))})

        logger.info(
            f"Funding arb executed: {pair} {direction} "
            f"size=${size_usd:.2f} rate={opportunity['funding_rate_pct']:.4f}%"
        )
        return {"success": True, "results": results, "size_usd": size_usd}

    except Exception as e:
        logger.error(f"Funding arb execution failed: {e}")
        return {"success": False, "error": str(e)}


async def execute_spread_trade(opportunity: dict) -> dict:
    """Execute one leg of a spread trade on Hyperliquid."""
    client = await _get_client()
    if not client:
        return {"success": False, "error": "Hyperliquid client not configured"}

    if not opportunity.get("executable"):
        return {"success": False, "error": "Hyperliquid not on either side of this spread"}

    pair = opportunity["pair"]
    size_usd = min(cfg.hl_bankroll * 0.3, 10.0)

    try:
        # Determine which side to execute on Hyperliquid
        if opportunity["buy_exchange"] == "hyperliquid":
            side = "buy"
            price = opportunity["buy_price"]
        else:
            side = "sell"
            price = opportunity["sell_price"]

        quantity = size_usd / price
        if pair in client.symbols:
            order = await client.create_order(pair, "market", side, quantity)
            logger.info(f"Spread trade: {side} {pair} @ ${price:.4f} qty={quantity:.6f}")
            return {
                "success": True,
                "side": side,
                "price": price,
                "quantity": quantity,
                "order_id": str(order.get("id", "")),
                "note": f"Execute {('sell' if side == 'buy' else 'buy')} on {opportunity['sell_exchange' if side == 'buy' else 'buy_exchange']} manually",
            }

        return {"success": False, "error": f"{pair} not found on Hyperliquid"}

    except Exception as e:
        logger.error(f"Spread trade failed: {e}")
        return {"success": False, "error": str(e)}


async def get_balances() -> dict:
    """Get current Hyperliquid wallet balances."""
    client = await _get_client()
    if not client:
        return {}
    try:
        balance = await client.fetch_balance()
        return {
            "total": balance.get("total", {}),
            "free": balance.get("free", {}),
            "used": balance.get("used", {}),
        }
    except Exception as e:
        logger.error(f"Balance fetch failed: {e}")
        return {}


async def close():
    """Close the Hyperliquid connection."""
    global _hl_client
    if _hl_client:
        try:
            await _hl_client.close()
        except Exception:
            pass
        _hl_client = None
