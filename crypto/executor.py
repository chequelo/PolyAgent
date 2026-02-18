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
                    spot_symbol, "market", "buy", quantity, price
                )
                results.append({"leg": "spot_buy", "order": str(spot_order.get("id", ""))})

            perp_order = await client.create_order(
                opportunity["hl_symbol"], "market", "sell", quantity, price,
                params={"reduceOnly": False}
            )
            results.append({"leg": "perp_short", "order": str(perp_order.get("id", ""))})

        elif direction == "long_perp":
            perp_order = await client.create_order(
                opportunity["hl_symbol"], "market", "buy", quantity, price,
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
        return {"success": False, "error": f"No client for {sell_exchange} — skipping (no 1-leg trades)"}

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

        # Execute both legs (pass price for HL slippage calculation)
        buy_order = await buy_client.create_order(buy_symbol, "market", "buy", quantity, buy_price)
        sell_order = await sell_client.create_order(sell_symbol, "market", "sell", quantity, sell_price)

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
            if not client:
                logger.warning(f"No client for {name}")
                continue

            combined = {}

            # Try default fetch first (works for most exchanges)
            try:
                balance = await client.fetch_balance()
                total = balance.get("total", {})
                for k, v in total.items():
                    try:
                        fv = float(v) if v else 0
                        if fv > 0:
                            combined[k] = combined.get(k, 0) + fv
                    except (ValueError, TypeError):
                        pass
            except Exception as e:
                logger.warning(f"Default fetch_balance failed for {name}: {e}")

            # For Hyperliquid, also try swap account if default didn't find much
            if name == "hyperliquid" and sum(combined.values()) < 1:
                for account_type in ["swap", "spot"]:
                    try:
                        bal = await client.fetch_balance({"type": account_type})
                        total = bal.get("total", {})
                        for k, v in total.items():
                            try:
                                fv = float(v) if v else 0
                                if fv > 0:
                                    combined[k] = combined.get(k, 0) + fv
                            except (ValueError, TypeError):
                                pass
                    except Exception as e:
                        logger.debug(f"HL {account_type} balance: {e}")

            if combined:
                balances[name] = combined
                logger.info(f"Balance {name}: {combined}")
            else:
                balances[name] = {}
                logger.warning(f"No balance found for {name}")

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
