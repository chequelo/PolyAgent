"""Multi-Exchange Executor — Trade execution via CCXT + Hyperliquid SDK.

CCXT is used for market data (tickers, funding rates, positions, balances).
Hyperliquid native SDK is used for order execution with atomic TP/SL support.
Binance uses CCXT for both data and execution (including STOP_MARKET orders).
"""
import asyncio
import logging

import ccxt.async_support as ccxt
import eth_account
from hyperliquid.exchange import Exchange as HLExchange
from hyperliquid.utils import constants as hl_constants

from config import cfg
from positions import create_funding_position, create_spread_position

logger = logging.getLogger("polyagent.crypto.executor")

_clients: dict[str, ccxt.Exchange] = {}
_hl_sdk: HLExchange | None = None


def _get_hl_sdk() -> HLExchange | None:
    """Get or create the native Hyperliquid SDK exchange client (synchronous)."""
    global _hl_sdk
    if _hl_sdk is not None:
        return _hl_sdk

    if not cfg.hl_private_key:
        return None

    account = eth_account.Account.from_key(cfg.hl_private_key)
    _hl_sdk = HLExchange(
        wallet=account,
        base_url=hl_constants.MAINNET_API_URL,
        account_address=cfg.hl_wallet_address or None,
    )
    return _hl_sdk


def _symbol_to_coin(symbol: str) -> str:
    """Convert CCXT symbol (e.g. 'SEI/USDT:USDT') to HL SDK coin name (e.g. 'SEI')."""
    return symbol.split("/")[0]


async def _get_client(exchange: str) -> ccxt.Exchange | None:
    """Get or create an authenticated CCXT client for the given exchange."""
    if exchange in _clients:
        return _clients[exchange]

    if exchange == "hyperliquid" and cfg.hl_private_key:
        client = ccxt.hyperliquid({
            "privateKey": cfg.hl_private_key,
            "walletAddress": cfg.hl_wallet_address,
            "enableRateLimit": True,
            "options": {"builderFee": False},
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
    """Execute funding rate arbitrage on Hyperliquid with atomic TP/SL orders.

    Uses the native HL SDK's bulk_orders with 'normalTpsl' grouping to place
    the entry order + take-profit + stop-loss in a single atomic transaction.
    The exchange monitors TP/SL 24/7 — no polling needed for price exits.
    """
    hl_sdk = _get_hl_sdk()
    ccxt_client = await _get_client("hyperliquid")
    if not hl_sdk or not ccxt_client:
        return {"success": False, "error": "Hyperliquid client not configured"}

    pair = opportunity["pair"]
    direction = opportunity["direction"]
    size_usd = opportunity["position_size"]

    try:
        # Use CCXT for market data
        ticker = await ccxt_client.fetch_ticker(opportunity["hl_symbol"])
        if not ticker or not ticker.get("last"):
            return {"success": False, "error": "Could not fetch price"}

        price = ticker["last"]
        quantity = size_usd / price
        coin = _symbol_to_coin(opportunity["hl_symbol"])

        # Determine direction
        is_short = direction == "short_perp"
        is_buy_entry = not is_short  # short_perp → sell entry, long_perp → buy entry

        # Calculate TP/SL trigger prices
        if is_short:
            # Short perp: profit when price drops, loss when price rises
            tp_trigger = round(price * (1 - cfg.pos_funding_tp_pct), 6)
            sl_trigger = round(price * (1 + cfg.pos_funding_sl_pct), 6)
        else:
            # Long perp: profit when price rises, loss when price drops
            tp_trigger = round(price * (1 + cfg.pos_funding_tp_pct), 6)
            sl_trigger = round(price * (1 - cfg.pos_funding_sl_pct), 6)

        # Build atomic order group: entry + TP + SL
        orders = [
            # Entry order (IOC market-like)
            {
                "coin": coin,
                "is_buy": is_buy_entry,
                "sz": quantity,
                "limit_px": price,
                "order_type": {"limit": {"tif": "Ioc"}},
                "reduce_only": False,
            },
            # Take-profit (opposite side, reduce-only)
            {
                "coin": coin,
                "is_buy": not is_buy_entry,
                "sz": quantity,
                "limit_px": tp_trigger,
                "order_type": {
                    "trigger": {
                        "isMarket": True,
                        "triggerPx": tp_trigger,
                        "tpsl": "tp",
                    }
                },
                "reduce_only": True,
            },
            # Stop-loss (opposite side, reduce-only)
            {
                "coin": coin,
                "is_buy": not is_buy_entry,
                "sz": quantity,
                "limit_px": sl_trigger,
                "order_type": {
                    "trigger": {
                        "isMarket": True,
                        "triggerPx": sl_trigger,
                        "tpsl": "sl",
                    }
                },
                "reduce_only": True,
            },
        ]

        # Execute atomically via SDK (synchronous → run in thread)
        result = await asyncio.to_thread(
            hl_sdk.bulk_orders, orders, grouping="normalTpsl"
        )

        # Parse response for order IDs
        statuses = result.get("response", {}).get("data", {}).get("statuses", [])
        entry_oid = ""
        tp_oid = ""
        sl_oid = ""
        for i, status in enumerate(statuses):
            if isinstance(status, dict) and "resting" in status:
                oid = str(status["resting"].get("oid", ""))
            elif isinstance(status, dict) and "filled" in status:
                oid = str(status["filled"].get("oid", ""))
            else:
                oid = ""
            if i == 0:
                entry_oid = oid
            elif i == 1:
                tp_oid = oid
            elif i == 2:
                sl_oid = oid

        results = [{"leg": "perp_entry", "order": entry_oid}]

        logger.info(
            f"Funding arb executed: {pair} {direction} "
            f"size=${size_usd:.2f} rate={opportunity['funding_rate_pct']:.4f}% "
            f"TP@{tp_trigger} SL@{sl_trigger}"
        )

        # Track position with TP/SL info
        perp_side = "short" if is_short else "long"
        create_funding_position(
            symbol=opportunity["hl_symbol"],
            side=perp_side,
            quantity=quantity,
            entry_price=price,
            size_usd=size_usd,
            entry_rate=opportunity["funding_rate"],
            direction=direction,
            pair=pair,
            order_ids=[entry_oid],
            tp_order_id=tp_oid,
            sl_order_id=sl_oid,
            tp_price=tp_trigger,
            sl_price=sl_trigger,
        )

        return {
            "success": True,
            "results": results,
            "size_usd": size_usd,
            "tp_price": tp_trigger,
            "sl_price": sl_trigger,
        }

    except Exception as e:
        logger.error(f"Funding arb execution failed: {e}")
        return {"success": False, "error": str(e)}


async def _place_hl_sl_order(
    coin: str, is_buy: bool, size: float, trigger_price: float
) -> str:
    """Place a standalone stop-loss trigger order on Hyperliquid via SDK."""
    hl_sdk = _get_hl_sdk()
    if not hl_sdk:
        return ""

    order = {
        "coin": coin,
        "is_buy": is_buy,
        "sz": size,
        "limit_px": trigger_price,
        "order_type": {
            "trigger": {
                "isMarket": True,
                "triggerPx": trigger_price,
                "tpsl": "sl",
            }
        },
        "reduce_only": True,
    }

    result = await asyncio.to_thread(hl_sdk.bulk_orders, [order], grouping="na")
    statuses = result.get("response", {}).get("data", {}).get("statuses", [])
    if statuses and isinstance(statuses[0], dict):
        if "resting" in statuses[0]:
            return str(statuses[0]["resting"].get("oid", ""))
    return ""


async def _place_binance_sl_order(
    client: ccxt.Exchange, symbol: str, side: str, quantity: float, stop_price: float
) -> str:
    """Place a STOP_MARKET order on Binance via CCXT."""
    order = await client.create_order(
        symbol, "STOP_MARKET", side, quantity,
        params={"stopPrice": stop_price}
    )
    return str(order.get("id", ""))


async def execute_spread_trade(opportunity: dict) -> dict:
    """Execute BOTH legs of a spread trade across exchanges.

    Buy on the cheaper exchange, sell on the more expensive one.
    After execution, places SL orders on each leg for protection:
    - Long leg (Binance): STOP_MARKET sell at -3%
    - Short leg (HL): trigger buy at +3%
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
    size_usd = max(10.0, min(min_bankroll * 0.5, cfg.spread_max_position))

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

        buy_order_id = str(buy_order.get("id", ""))
        sell_order_id = str(sell_order.get("id", ""))

        # Place SL orders on each leg for protection against sharp moves
        buy_sl_oid = ""
        buy_sl_price = round(buy_price * 0.97, 6)  # -3% on long leg
        sell_sl_oid = ""
        sell_sl_price = round(sell_price * 1.03, 6)  # +3% on short leg

        try:
            if buy_exchange == "binance":
                buy_sl_oid = await _place_binance_sl_order(
                    buy_client, buy_symbol, "sell", quantity, buy_sl_price
                )
            elif buy_exchange == "hyperliquid":
                buy_sl_oid = await _place_hl_sl_order(
                    _symbol_to_coin(buy_symbol), False, quantity, buy_sl_price
                )
        except Exception as e:
            logger.warning(f"Failed to place SL on buy leg ({buy_exchange}): {e}")

        try:
            if sell_exchange == "hyperliquid":
                sell_sl_oid = await _place_hl_sl_order(
                    _symbol_to_coin(sell_symbol), True, quantity, sell_sl_price
                )
            elif sell_exchange == "binance":
                sell_sl_oid = await _place_binance_sl_order(
                    sell_client, sell_symbol, "buy", quantity, sell_sl_price
                )
        except Exception as e:
            logger.warning(f"Failed to place SL on sell leg ({sell_exchange}): {e}")

        sl_info = ""
        if buy_sl_oid or sell_sl_oid:
            sl_info = f" SL: buy@{buy_sl_price} sell@{sell_sl_price}"

        logger.info(
            f"Spread arb: BUY {pair} on {buy_exchange} @ ${buy_price:.4f}, "
            f"SELL on {sell_exchange} @ ${sell_price:.4f}, "
            f"qty={quantity:.6f}, spread={opportunity['spread_pct']:.3f}%{sl_info}"
        )

        # Track position with SL info
        create_spread_position(
            buy_exchange=buy_exchange,
            buy_symbol=buy_symbol,
            sell_exchange=sell_exchange,
            sell_symbol=sell_symbol,
            quantity=quantity,
            buy_price=buy_price,
            sell_price=sell_price,
            size_usd=size_usd,
            buy_order_id=buy_order_id,
            sell_order_id=sell_order_id,
            sl_order_id=buy_sl_oid or None,
            sl_price=buy_sl_price if buy_sl_oid else None,
            other_sl_order_id=sell_sl_oid or None,
            other_sl_price=sell_sl_price if sell_sl_oid else None,
        )

        return {
            "success": True,
            "buy_exchange": buy_exchange,
            "sell_exchange": sell_exchange,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "quantity": quantity,
            "buy_order_id": buy_order_id,
            "sell_order_id": sell_order_id,
            "buy_sl_price": buy_sl_price if buy_sl_oid else None,
            "sell_sl_price": sell_sl_price if sell_sl_oid else None,
            "note": "Both legs executed" + (" + SL orders placed" if buy_sl_oid or sell_sl_oid else ""),
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


async def cancel_hl_order(order_id: str, coin: str) -> bool:
    """Cancel a trigger order on Hyperliquid (used when closing positions manually)."""
    hl_sdk = _get_hl_sdk()
    if not hl_sdk or not order_id:
        return False
    try:
        result = await asyncio.to_thread(
            hl_sdk.cancel, coin, int(order_id)
        )
        return result.get("status") == "ok"
    except Exception as e:
        logger.warning(f"Failed to cancel HL order {order_id}: {e}")
        return False


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
