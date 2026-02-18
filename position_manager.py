"""Position Manager — Periodic checker that closes positions when exit criteria are met."""
import logging
from config import cfg
from positions import get_open_positions, close_position, position_age_hours
from crypto.executor import _get_client

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

    Exit criteria:
    1. Funding rate flipped sign → close
    2. Funding rate dropped below half of entry rate → close
    3. Unrealized PnL < -5% of position size → stop loss
    4. Position open > 24h → timeout close
    """
    client = await _get_client("hyperliquid")
    if not client:
        return None

    try:
        # Check age first (cheapest check)
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

        # Check unrealized PnL via position data
        positions = await client.fetch_positions([pos.symbol])
        for p in positions:
            if p.get("symbol") == pos.symbol:
                unrealized = p.get("unrealizedPnl", 0) or 0
                if unrealized < -(pos.size_usd * cfg.pos_stop_loss_pct):
                    return await _close_funding_position(client, pos, f"stop_loss (PnL: ${unrealized:.2f})")
                break

    except Exception as e:
        logger.error(f"Error checking funding position {pos.id}: {e}")

    return None


async def _close_funding_position(client, pos, reason: str) -> dict:
    """Close a funding arb perp position on Hyperliquid."""
    try:
        # Get current price for slippage
        ticker = await client.fetch_ticker(pos.symbol)
        price = ticker["last"]

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

    Exit criteria:
    1. Spread closed (< 0.03%) → close both legs
    2. Spread reversed → close both legs
    3. Position open > 1h → timeout close
    """
    buy_client = await _get_client(pos.exchange)
    sell_client = await _get_client(pos.other_exchange) if pos.other_exchange else None

    if not buy_client or not sell_client:
        return None

    try:
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
        entry_spread = (pos.entry_price - (pos.close_price or pos.entry_price))  # approximate

        if current_spread <= 0.03:
            return await _close_spread_position(buy_client, sell_client, pos, "spread_closed")

        # Spread reversed: the side we bought is now more expensive than where we sold
        # This is actually good — means our long side appreciated
        # But if the reverse is large, lock in profit
        if current_spread >= 0.5:  # 0.5% reverse = good time to close
            return await _close_spread_position(buy_client, sell_client, pos, "profit_take")

    except Exception as e:
        logger.error(f"Error checking spread position {pos.id}: {e}")

    return None


async def _close_spread_position(buy_client, sell_client, pos, reason: str) -> dict:
    """Close both legs of a spread position."""
    try:
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


