"""Position Manager — Periodic checker that closes positions when exit criteria are met.

Hybrid approach:
- Exchange-native TP/SL orders handle price-based exits instantly (24/7)
- This polling checker handles data-based exits (rate flip, timeout, spread convergence)
  and detects when TP/SL already fired on the exchange
"""
import logging
from config import cfg
from positions import get_open_positions, close_position, position_age_hours
from crypto.executor import _get_client, cancel_hl_order, _symbol_to_coin

logger = logging.getLogger("polyagent.position_manager")


async def check_positions(bot) -> list[dict]:
    """Check all open positions and close those that meet exit criteria.
    Returns list of close actions taken.
    """
    actions = []

    funding_positions = get_open_positions(strategy="funding_arb")
    for pos in funding_positions:
        result = await _check_funding_position(pos)
        if result:
            actions.append(result)
            from notifier import notify_position_closed
            await notify_position_closed(bot, pos, result)

    spread_positions = get_open_positions(strategy="spread")
    for pos in spread_positions:
        result = await _check_spread_position(pos)
        if result:
            actions.append(result)
            from notifier import notify_position_closed
            await notify_position_closed(bot, pos, result)

    if actions:
        logger.info(f"Position manager: {len(actions)} positions closed")
    return actions


async def _check_funding_position(pos) -> dict | None:
    """Check if a funding arb position should be closed.

    Exit criteria (polling):
    1. TP/SL already executed on exchange → mark as closed (no trade needed)
    2. Funding rate flipped sign → close + cancel TP/SL orders
    3. Funding rate dropped below half of entry rate → close + cancel TP/SL orders
    4. Position open > 24h → timeout close + cancel TP/SL orders

    Note: Price-based stop-loss is handled by exchange-native SL order (instant).
    """
    client = await _get_client("hyperliquid")
    if not client:
        return None

    try:
        # Check if TP/SL already executed (position no longer exists on exchange)
        positions = await client.fetch_positions([pos.symbol])
        has_position = False
        for p in positions:
            if p.get("symbol") == pos.symbol:
                size = abs(float(p.get("contracts", 0) or 0))
                if size > 0:
                    has_position = True
                break

        if not has_position and (pos.tp_order_id or pos.sl_order_id):
            # Position is gone — TP or SL was triggered by the exchange
            ticker = await client.fetch_ticker(pos.symbol)
            price = ticker["last"]
            if pos.side == "short":
                pnl = (pos.entry_price - price) * pos.quantity
            else:
                pnl = (price - pos.entry_price) * pos.quantity
            close_position(pos.id, price, "tp_sl_triggered", pnl)
            logger.info(f"Funding position {pos.id} closed by exchange TP/SL, PnL~${pnl:.2f}")
            return {
                "success": True,
                "position_id": pos.id,
                "reason": "tp_sl_triggered",
                "close_price": price,
                "pnl": pnl,
            }

        # Check age (cheapest data-based check)
        age_hours = position_age_hours(pos.entry_time)
        if age_hours >= cfg.pos_funding_timeout_hours:
            return await _close_funding_position(client, pos, "timeout_24h")

        # Fetch current funding rate
        funding = await client.fetch_funding_rate(pos.symbol)
        current_rate = funding.get("fundingRate", 0) or 0

        entry_rate = pos.entry_rate or 0

        # Rate flipped sign
        if entry_rate != 0 and (current_rate * entry_rate) < 0:
            return await _close_funding_position(client, pos, "rate_flipped")

        # Rate dropped below half of entry
        if entry_rate != 0 and abs(current_rate) < abs(entry_rate) * 0.5:
            return await _close_funding_position(client, pos, "rate_dropped")

    except Exception as e:
        logger.error(f"Error checking funding position {pos.id}: {e}")

    return None


async def _close_funding_position(client, pos, reason: str) -> dict:
    """Close a funding arb perp position on Hyperliquid.

    Also cancels any remaining TP/SL orders on the exchange.
    """
    try:
        # Get current price for slippage
        ticker = await client.fetch_ticker(pos.symbol)
        price = ticker["last"]

        # Cancel exchange-native TP/SL orders before closing
        coin = _symbol_to_coin(pos.symbol)
        if pos.tp_order_id:
            await cancel_hl_order(pos.tp_order_id, coin)
        if pos.sl_order_id:
            await cancel_hl_order(pos.sl_order_id, coin)

        # Close perp with opposite side + reduceOnly
        close_side = "buy" if pos.side == "short" else "sell"
        order = await client.create_order(
            pos.symbol, "market", close_side, pos.quantity, price,
            params={"reduceOnly": True}
        )

        # Calculate approximate PnL
        if pos.side == "short":
            pnl = (pos.entry_price - price) * pos.quantity
        else:
            pnl = (price - pos.entry_price) * pos.quantity

        close_position(pos.id, price, reason, pnl)

        logger.info(f"Closed funding position {pos.id}: {reason}, PnL=${pnl:.2f}")
        return {
            "success": True,
            "position_id": pos.id,
            "reason": reason,
            "close_price": price,
            "pnl": pnl,
            "order_id": str(order.get("id", "")),
        }

    except Exception as e:
        logger.error(f"Failed to close funding position {pos.id}: {e}")
        return {
            "success": False,
            "position_id": pos.id,
            "reason": reason,
            "error": str(e),
        }


async def _check_spread_position(pos) -> dict | None:
    """Check if a spread position should be closed.

    Exit criteria (polling):
    1. One leg's SL fired → close the other leg immediately
    2. Spread closed (< 0.03%) → close both legs
    3. Spread reversed enough for profit (>= 0.5%) → close both legs
    4. Position open > 1h → timeout close
    """
    buy_client = await _get_client(pos.exchange)
    sell_client = await _get_client(pos.other_exchange) if pos.other_exchange else None

    if not buy_client or not sell_client:
        return None

    try:
        # Check if one leg's SL has been triggered (partial position)
        sl_result = await _check_spread_sl_triggered(buy_client, sell_client, pos)
        if sl_result:
            return sl_result

        # Check age
        age_hours = position_age_hours(pos.entry_time)
        if age_hours >= cfg.pos_spread_timeout_hours:
            return await _close_spread_position(buy_client, sell_client, pos, "timeout_1h")

        # Fetch current prices
        buy_ticker = await buy_client.fetch_ticker(pos.symbol)
        sell_ticker = await sell_client.fetch_ticker(pos.other_symbol) if pos.other_symbol else None

        if not buy_ticker or not sell_ticker:
            return None

        buy_bid = buy_ticker.get("bid", 0)
        sell_ask = sell_ticker.get("ask", 0)

        if not buy_bid or not sell_ask:
            return None

        # Current spread: to close, we'd sell on buy_exchange and buy on sell_exchange
        # If current spread is <= 0.03%, the opportunity is gone
        current_spread = (buy_bid - sell_ask) / sell_ask * 100

        if current_spread <= 0.03:
            return await _close_spread_position(buy_client, sell_client, pos, "spread_closed")

        # Spread reversed: lock in profit if large enough
        if current_spread >= 0.5:
            return await _close_spread_position(buy_client, sell_client, pos, "profit_take")

    except Exception as e:
        logger.error(f"Error checking spread position {pos.id}: {e}")

    return None


async def _check_spread_sl_triggered(buy_client, sell_client, pos) -> dict | None:
    """Check if one leg of a spread had its SL triggered, and close the other."""
    # Check buy leg (long side) — does position still exist?
    buy_has_position = await _has_open_position(buy_client, pos.symbol, pos.exchange)
    # Check sell leg (short side) — does position still exist?
    sell_has_position = await _has_open_position(
        sell_client, pos.other_symbol, pos.other_exchange
    ) if pos.other_symbol else True

    if buy_has_position and sell_has_position:
        return None  # Both legs still open, no SL triggered

    if not buy_has_position and not sell_has_position:
        # Both legs gone — both SLs triggered or something else happened
        ticker = await buy_client.fetch_ticker(pos.symbol)
        price = ticker["last"]
        pnl = (price - pos.entry_price) * pos.quantity
        close_position(pos.id, price, "both_sl_triggered", pnl)
        logger.info(f"Spread {pos.id}: both legs closed by SL")
        return {
            "success": True,
            "position_id": pos.id,
            "reason": "both_sl_triggered",
            "close_price": price,
            "pnl": pnl,
        }

    # One leg's SL triggered — close the surviving leg
    if not buy_has_position:
        # Buy leg SL triggered → close sell leg
        logger.info(f"Spread {pos.id}: buy leg SL triggered, closing sell leg")
        try:
            sell_ticker = await sell_client.fetch_ticker(pos.other_symbol)
            price = sell_ticker["ask"]
            order = await sell_client.create_order(
                pos.other_symbol, "market", "buy", pos.quantity, price
            )
            pnl = (pos.entry_price - price) * pos.quantity  # approximate
            close_position(pos.id, price, "buy_sl_triggered", pnl)
            return {
                "success": True,
                "position_id": pos.id,
                "reason": "buy_sl_triggered",
                "close_price": price,
                "pnl": pnl,
                "order_id": str(order.get("id", "")),
            }
        except Exception as e:
            logger.error(f"Failed to close sell leg after buy SL: {e}")
            return {"success": False, "position_id": pos.id, "reason": "buy_sl_triggered", "error": str(e)}

    # Sell leg SL triggered → close buy leg
    logger.info(f"Spread {pos.id}: sell leg SL triggered, closing buy leg")
    try:
        buy_ticker = await buy_client.fetch_ticker(pos.symbol)
        price = buy_ticker["bid"]
        order = await buy_client.create_order(
            pos.symbol, "market", "sell", pos.quantity, price
        )
        pnl = (price - pos.entry_price) * pos.quantity
        close_position(pos.id, price, "sell_sl_triggered", pnl)
        return {
            "success": True,
            "position_id": pos.id,
            "reason": "sell_sl_triggered",
            "close_price": price,
            "pnl": pnl,
            "order_id": str(order.get("id", "")),
        }
    except Exception as e:
        logger.error(f"Failed to close buy leg after sell SL: {e}")
        return {"success": False, "position_id": pos.id, "reason": "sell_sl_triggered", "error": str(e)}


async def _has_open_position(client, symbol: str, exchange: str) -> bool:
    """Check if a position still exists on the exchange."""
    try:
        if exchange == "binance":
            positions = await client.fetch_positions([symbol])
            for p in positions:
                if p.get("symbol") == symbol:
                    size = abs(float(p.get("contracts", 0) or 0))
                    if size > 0:
                        return True
            return False
        else:
            # Hyperliquid or others via CCXT
            positions = await client.fetch_positions([symbol])
            for p in positions:
                if p.get("symbol") == symbol:
                    size = abs(float(p.get("contracts", 0) or 0))
                    if size > 0:
                        return True
            return False
    except Exception as e:
        logger.warning(f"Could not check position on {exchange} for {symbol}: {e}")
        return True  # Assume still open if we can't check


async def _close_spread_position(buy_client, sell_client, pos, reason: str) -> dict:
    """Close both legs of a spread position. Also cancels any remaining SL orders."""
    try:
        # Cancel SL orders before closing
        if pos.sl_order_id and pos.exchange == "hyperliquid":
            await cancel_hl_order(pos.sl_order_id, _symbol_to_coin(pos.symbol))
        elif pos.sl_order_id and pos.exchange == "binance":
            try:
                await buy_client.cancel_order(pos.sl_order_id, pos.symbol)
            except Exception:
                pass

        if pos.other_sl_order_id and pos.other_exchange == "hyperliquid":
            await cancel_hl_order(pos.other_sl_order_id, _symbol_to_coin(pos.other_symbol))
        elif pos.other_sl_order_id and pos.other_exchange == "binance":
            try:
                await sell_client.cancel_order(pos.other_sl_order_id, pos.other_symbol)
            except Exception:
                pass

        # Get current prices
        buy_ticker = await buy_client.fetch_ticker(pos.symbol)
        sell_ticker = await sell_client.fetch_ticker(pos.other_symbol)
        buy_price = buy_ticker["bid"]  # Sell at bid on buy exchange
        sell_price = sell_ticker["ask"]  # Buy at ask on sell exchange

        # Close: sell on the exchange we bought, buy on the exchange we sold
        sell_order = await buy_client.create_order(
            pos.symbol, "market", "sell", pos.quantity, buy_price
        )
        buy_order = await sell_client.create_order(
            pos.other_symbol, "market", "buy", pos.quantity, sell_price
        )

        # PnL: (sell_price_now - buy_price_entry) + (sell_price_entry - buy_price_now)
        # Simplified: entry_spread_profit - close_spread_cost
        pnl = (buy_price - pos.entry_price) * pos.quantity  # Long leg PnL
        # Note: short leg PnL would need original sell price, which is approximate

        close_position(pos.id, buy_price, reason, pnl)

        logger.info(f"Closed spread position {pos.id}: {reason}, PnL~${pnl:.2f}")
        return {
            "success": True,
            "position_id": pos.id,
            "reason": reason,
            "close_price": buy_price,
            "pnl": pnl,
            "sell_order_id": str(sell_order.get("id", "")),
            "buy_order_id": str(buy_order.get("id", "")),
        }

    except Exception as e:
        logger.error(f"Failed to close spread position {pos.id}: {e}")
        return {
            "success": False,
            "position_id": pos.id,
            "reason": reason,
            "error": str(e),
        }
