"""Multi-Exchange Executor — Trade execution via CCXT (Hyperliquid + Binance)"""
import logging
import ccxt.async_support as ccxt
from config import cfg

logger = logging.getLogger("polyagent.crypto.executor")

_clients: dict[str, ccxt.Exchange] = {}


async def _get_client(exchange: str) -> ccxt.Exchange | None:
    """Get or create an authenticated CCXT client for the given exchange."""
    if exchange in _clients:
        return _clients[exchange]

    if exchange == "hyperliquid" and cfg.hl_private_key:
        client = ccxt.hyperliquid({
            "privateKey": cfg.hl_private_key,
            "walletAddress": cfg.hl_wallet_address,
            "enableRateLimit": True,
        })
        await client.load_markets()
        _clients[exchange] = client
        return client

    if exchange == "binance" and cfg.binance_api_key:
        client = ccxt.binance({
            "apiKey": cfg.binance_api_key,
            "secret": cfg.binance_secret,
            "enableRateLimit": True,
        })
        await client.load_markets()
        _clients[exchange] = client
        return client

    return None


def _get_bankroll(exchange: str) -> float:
    """Get the configured bankroll for an exchange."""
    if exchange == "hyperliquid":
        return cfg.hl_bankroll
    if exchange == "binance":
        return cfg.binance_bankroll
    return 0.0


def _available_exchanges() -> list[str]:
    """List exchanges that have credentials configured."""
    available = []
    if cfg.hl_private_key:
        available.append("hyperliquid")
    if cfg.binance_api_key:
        available.append("binance")
    return available


async def execute_funding_arb(opportunity: dict) -> dict:
    """Execute funding rate arbitrage: spot + perp hedge on Hyperliquid."""
    client = await _get_client("hyperliquid")
    if not client:
        return {"success": False, "error": "Hyperliquid client not configured"}

    pair = opportunity["pair"]
    direction = opportunity["direction"]
    size_usd = opportunity["position_size"]

    try:
        ticker = await client.fetch_ticker(opportunity["hl_symbol"])
        if not ticker or not ticker.get("last"):
            return {"success": False, "error": "Could not fetch price"}

        price = ticker["last"]
        quantity = size_usd / price

        results = []

        if direction == "short_perp":
            spot_symbol = pair
            if spot_symbol in client.symbols:
                spot_order = await client.create_order(
                    spot_symbol, "market", "buy", quantity
                )
                results.append({"leg": "spot_buy", "order": str(spot_order.get("id", ""))})

            perp_order = await client.create_order(
                opportunity["hl_symbol"], "market", "sell", quantity,
                params={"reduceOnly": False}
            )
            results.append({"leg": "perp_short", "order": str(perp_order.get("id", ""))})

        elif direction == "long_perp":
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
    """Execute BOTH legs of a spread trade across exchanges.

    Buy on the cheaper exchange, sell on the more expensive one.
    Both exchanges must have authenticated clients.
    """
    if not opportunity.get("executable"):
        return {"success": False, "error": "Not executable"}

    buy_exchange = opportunity["buy_exchange"]
    sell_exchange = opportunity["sell_exchange"]

    buy_client = await _get_client(buy_exchange)
    sell_client = await _get_client(sell_exchange)

    if not buy_client:
        return {"success": False, "error": f"No client for {buy_exchange}"}
    if not sell_client:
        # One-sided execution: only execute on the exchange we have
        return await _execute_one_leg(opportunity)

    pair = opportunity["pair"]
    buy_price = opportunity["buy_price"]
    sell_price = opportunity["sell_price"]

    # Size: min of both bankrolls, capped at spread_max_position
    min_bankroll = min(_get_bankroll(buy_exchange), _get_bankroll(sell_exchange))
    size_usd = min(min_bankroll * 0.3, cfg.spread_max_position)

    buy_qty = size_usd / buy_price
    sell_qty = size_usd / sell_price
    # Use same quantity for both legs (use the smaller to avoid overselling)
    quantity = min(buy_qty, sell_qty)

    try:
        # Find the right symbol on each exchange
        buy_symbol = _find_symbol(buy_client, pair)
        sell_symbol = _find_symbol(sell_client, pair)

        if not buy_symbol:
            return {"success": False, "error": f"{pair} not found on {buy_exchange}"}
        if not sell_symbol:
            return {"success": False, "error": f"{pair} not found on {sell_exchange}"}

        # Execute both legs
        buy_order = await buy_client.create_order(buy_symbol, "market", "buy", quantity)
        sell_order = await sell_client.create_order(sell_symbol, "market", "sell", quantity)

        logger.info(
            f"Spread arb: BUY {pair} on {buy_exchange} @ ${buy_price:.4f}, "
            f"SELL on {sell_exchange} @ ${sell_price:.4f}, "
            f"qty={quantity:.6f}, spread={opportunity['spread_pct']:.3f}%"
        )
        return {
            "success": True,
            "buy_exchange": buy_exchange,
            "sell_exchange": sell_exchange,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "quantity": quantity,
            "buy_order_id": str(buy_order.get("id", "")),
            "sell_order_id": str(sell_order.get("id", "")),
            "note": "Both legs executed",
        }

    except Exception as e:
        logger.error(f"Spread trade failed: {e}")
        return {"success": False, "error": str(e)}


async def _execute_one_leg(opportunity: dict) -> dict:
    """Fallback: execute only one leg on the exchange we have a client for."""
    available = _available_exchanges()
    buy_ex = opportunity["buy_exchange"]
    sell_ex = opportunity["sell_exchange"]

    # Determine which side we can execute
    if buy_ex in available:
        exchange = buy_ex
        side = "buy"
        price = opportunity["buy_price"]
        other = sell_ex
    elif sell_ex in available:
        exchange = sell_ex
        side = "sell"
        price = opportunity["sell_price"]
        other = buy_ex
    else:
        return {"success": False, "error": "No authenticated exchange available"}

    client = await _get_client(exchange)
    if not client:
        return {"success": False, "error": f"Client for {exchange} not ready"}

    pair = opportunity["pair"]
    size_usd = min(_get_bankroll(exchange) * 0.3, cfg.spread_max_position)
    quantity = size_usd / price

    try:
        symbol = _find_symbol(client, pair)
        if not symbol:
            return {"success": False, "error": f"{pair} not found on {exchange}"}

        order = await client.create_order(symbol, "market", side, quantity)
        logger.info(f"Spread (1-leg): {side} {pair} on {exchange} @ ${price:.4f}")
        return {
            "success": True,
            "side": side,
            "price": price,
            "quantity": quantity,
            "order_id": str(order.get("id", "")),
            "note": f"One-leg only. Execute {('sell' if side == 'buy' else 'buy')} on {other} manually.",
        }

    except Exception as e:
        logger.error(f"One-leg spread failed: {e}")
        return {"success": False, "error": str(e)}


def _find_symbol(client: ccxt.Exchange, pair: str) -> str | None:
    """Find a tradeable symbol on an exchange for a given pair."""
    if pair in client.symbols:
        return pair

    base, quote = pair.split("/")
    # Search for spot match
    for symbol, market in client.markets.items():
        if (market.get("base") == base and
            market.get("quote") == quote and
            market.get("spot", False)):
            return symbol

    # Fall back to linear perp (for Hyperliquid)
    for symbol, market in client.markets.items():
        if (market.get("base") == base and
            (market.get("swap") or market.get("linear")) and
            market.get("quote") in ("USDT", "USDC", "USD")):
            return symbol

    return None


async def get_balances() -> dict:
    """Get current balances from all configured exchanges."""
    balances = {}

    for name in _available_exchanges():
        try:
            client = await _get_client(name)
            if client:
                balance = await client.fetch_balance()
                total = balance.get("total", {})
                # Filter to non-zero balances
                non_zero = {k: float(v) for k, v in total.items() if v and float(v) > 0}
                balances[name] = non_zero
        except Exception as e:
            logger.error(f"Balance fetch failed for {name}: {e}")

    return balances


async def close():
    """Close all exchange connections."""
    global _clients
    for name, client in _clients.items():
        try:
            await client.close()
        except Exception:
            pass
    _clients = {}
