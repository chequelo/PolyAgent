"""Unified Telegram Notifier — Auto-execution alerts for all strategies"""
import logging
from config import cfg

logger = logging.getLogger("polyagent.notifier")


async def send_message(bot, text: str):
    """Send a message to the configured chat."""
    try:
        await bot.send_message(
            chat_id=cfg.telegram_chat_id,
            text=text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


async def notify_prediction(bot, market: dict, estimate: dict, research: dict, result: dict):
    """Send prediction market opportunity with execution result."""
    if not estimate or estimate.get("side") == "SKIP":
        return

    edge_emoji = "🟢" if estimate["abs_edge"] > 0.10 else "🟡"
    conf_emoji = {"high": "🔥", "medium": "⚡", "low": "💭"}.get(estimate["confidence"], "💭")

    status = "✅ Executed" if result.get("success") else f"❌ Failed: {result.get('error', 'unknown')}"

    text = (
        f"📊 *PREDICTION — AUTO-EXECUTED*\n\n"
        f"❓ {market['question'][:100]}\n\n"
        f"{edge_emoji} Side: *{estimate['side']}*\n"
        f"📈 Market: {market['mid']:.1%} → My estimate: {estimate['probability']:.1%}\n"
        f"🎯 Edge: {estimate['abs_edge']:.1%}\n"
        f"{conf_emoji} Confidence: {estimate['confidence']}\n"
        f"💰 Kelly bet: *${estimate['kelly_bet']:.2f}*\n\n"
        f"📝 _{estimate['thesis']}_\n\n"
        f"Methods: Base={estimate.get('base_rate', 0):.0%} | "
        f"Evidence={estimate.get('evidence', 0):.0%} | "
        f"Market={estimate.get('market_analysis', 0):.0%}\n\n"
        f"🤖 {status}"
    )

    await send_message(bot, text)


async def notify_pm_arb(bot, opp: dict, result: dict):
    """Send Polymarket YES+NO arbitrage with execution result."""
    status = "✅ Executed" if result.get("success") else f"❌ Failed: {result.get('error', 'unknown')}"

    text = (
        f"🔄 *PM ARBITRAGE — AUTO-EXECUTED*\n\n"
        f"❓ {opp['question'][:100]}\n\n"
        f"YES: ${opp['yes_price']:.3f} + NO: ${opp['no_price']:.3f} = "
        f"${opp['total_cost']:.3f}\n"
        f"💰 Profit/dollar: *${opp['profit_per_dollar']:.4f}* ({opp['profit_pct']:.2f}%)\n"
        f"📊 Liquidity: ${opp['liquidity']:,.0f}\n\n"
        f"🤖 {status}"
    )

    await send_message(bot, text)


async def notify_funding(bot, opp: dict, result: dict):
    """Send funding rate arbitrage with execution result."""
    comp_lines = ""
    for name, data in opp.get("comparison", {}).items():
        comp_lines += f"  {name}: {data['rate'] * 100:.4f}%\n"

    status = "✅ Executed" if result.get("success") else f"❌ Failed: {result.get('error', 'unknown')}"

    text = (
        f"💹 *FUNDING RATE ARB — AUTO-EXECUTED*\n\n"
        f"📍 {opp['pair']} on Hyperliquid\n"
        f"📊 Rate: *{opp['funding_rate_pct']:.4f}%* per hour\n"
        f"📅 Annualized: *{opp['annualized_pct']:.1f}%*\n"
        f"🎯 Strategy: {opp['strategy']}\n"
        f"💰 Position: ${opp['position_size']:.2f}\n"
    )
    if comp_lines:
        text += f"\n📋 Other exchanges:\n{comp_lines}"
    text += f"\n🤖 {status}"

    await send_message(bot, text)


async def notify_spread(bot, opp: dict, result: dict):
    """Send cross-exchange spread with execution result."""
    if result.get("success"):
        note = result.get("note", "")
        both = "BOTH LEGS" if "buy_order_id" in result and "sell_order_id" in result else "1 LEG"
        status = f"✅ {both} executed"
        if note:
            status += f"\n{note}"
    else:
        status = f"❌ Failed: {result.get('error', 'unknown')}"

    both_exec = "DUAL" if opp.get("both_executable") else "SINGLE"

    text = (
        f"📊 *SPREAD — AUTO-EXECUTED ({both_exec})*\n\n"
        f"📍 {opp['pair']}\n"
        f"🟢 Buy on *{opp['buy_exchange']}*: ${opp['buy_price']:.4f}\n"
        f"🔴 Sell on *{opp['sell_exchange']}*: ${opp['sell_price']:.4f}\n"
        f"📈 Spread: *{opp['spread_pct']:.3f}%* (net: {opp['net_profit_pct']:.3f}%)\n"
        f"💰 Est. profit: ${opp['est_profit_usd']:.4f}\n\n"
        f"🤖 {status}"
    )

    await send_message(bot, text)


async def notify_micro_arb(bot, opp: dict, result: dict):
    """Send micro-arbitrage with execution result."""
    move = opp.get("spot_move", {})

    if result.get("success"):
        status = f"✅ {result['side']} @ ${result['price']:.3f} (edge {result['edge_pct']:.1f}%)"
    else:
        status = f"❌ Failed: {result.get('error', 'unknown')}"

    text = (
        f"⚡ *MICRO-ARB — AUTO-EXECUTED ({opp['duration']})*\n\n"
        f"📍 {opp['asset']} spot moved *{move.get('move_pct', 0):+.3f}%* in {move.get('seconds', 0):.0f}s\n"
        f"📊 {opp['question'][:80]}\n\n"
        f"🎯 Buy *{opp['side']}* @ ${opp['entry_price']:.3f}\n"
        f"📈 Fair value: ${opp['estimated_fair']:.3f}\n"
        f"💎 Edge: *{opp['edge_pct']:.1f}%*\n"
        f"💰 Bet: ${opp['bet_size']:.2f}\n"
        f"📝 Strategy: MAKER limit (0% fee + rebates)\n\n"
        f"🤖 {status}"
    )

    await send_message(bot, text)


async def notify_prediction_reeval(bot, position, result: dict):
    """Send notification when a prediction position is re-evaluated."""
    action = result.get("action", "HOLD")
    new_prob = result.get("new_probability")
    new_edge = result.get("new_edge", 0)
    current_price = result.get("current_price", 0)
    pnl = result.get("pnl")

    question = position.market_question or position.symbol

    action_emoji = {"HOLD": "🟢", "SOLD": "🔴", "ALERT": "🟡"}.get(action, "⚪")

    lines = [
        f"🔄 *PM RE-EVALUATION — {action}*\n",
        f"❓ {question[:100]}\n",
        f"📍 Side: *{position.side}*",
        f"💵 Entry: ${position.entry_price:.3f} → Now: ${current_price:.3f}",
    ]

    if position.estimated_prob and new_prob:
        lines.append(
            f"🎯 Prob: {position.estimated_prob:.1%} → {new_prob:.1%}"
        )

    if new_edge is not None:
        lines.append(f"📈 Current edge: {new_edge:+.1%}")

    if result.get("reason"):
        lines.append(f"📝 _{result['reason']}_")

    if action == "SOLD" and pnl is not None:
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
        lines.append(f"{pnl_emoji} PnL: *${pnl:+.2f}*")

    from positions import position_age_hours
    age = position_age_hours(position.entry_time)
    lines.append(f"⏱ Position age: {age:.1f}h")
    lines.append(f"\n{action_emoji} Action: *{action}*")

    await send_message(bot, "\n".join(lines))


async def notify_scan_summary(bot, stats: dict, diagnostics: dict | None = None):
    """Send periodic scan summary with diagnostics."""
    text = (
        f"🔍 *SCAN COMPLETE*\n\n"
        f"📊 Predictions analyzed: {stats.get('predictions', 0)}\n"
        f"🔄 PM Arb opportunities: {stats.get('pm_arbs', 0)}\n"
        f"⚡ Micro-arb signals: {stats.get('micro_arbs', 0)}\n"
        f"💹 Funding opportunities: {stats.get('funding', 0)}\n"
        f"📈 Spread opportunities: {stats.get('spreads', 0)}\n"
    )

    if diagnostics:
        # Spread diagnostics
        spread_diag = diagnostics.get("spreads", {})
        if spread_diag:
            best = spread_diag.get("best_spread", 0)
            checked = spread_diag.get("pairs_checked", 0)
            near = spread_diag.get("near_misses", [])
            text += f"\n📉 *Spread diagnostics:*\n"
            text += f"  Best: {best}% | Pairs: {checked}\n"
            if near:
                top = near[:4]
                text += "  Near: " + ", ".join(f"{n['pair']} {n['spread']}%" for n in top) + "\n"

        # Funding diagnostics
        fund_diag = diagnostics.get("funding", {})
        if fund_diag:
            rates = fund_diag.get("rates", [])
            if rates:
                text += f"\n💹 *Top funding rates:*\n"
                for r in rates[:4]:
                    parts = [f"{r['pair']}: HL {r['hl_ann']:+.1f}%"]
                    for k, v in r.items():
                        if k.endswith("_ann") and k != "hl_ann":
                            ex = k.replace("_ann", "")
                            parts.append(f"{ex} {v:+.1f}%")
                    text += "  " + " | ".join(parts) + "\n"

    text += f"\n⏱ Next scan in {cfg.pm_scan_interval_hours}h"
    await send_message(bot, text)


async def notify_position_closed(bot, position, result: dict):
    """Send notification when a position is automatically closed."""
    pnl = result.get("pnl", 0) or 0
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    status = "✅ Closed" if result.get("success") else f"❌ Close failed: {result.get('error', 'unknown')}"

    reason_labels = {
        "rate_flipped": "Funding rate flipped sign",
        "rate_dropped": "Funding rate dropped >50%",
        "timeout_24h": "Position timeout (24h)",
        "timeout_1h": "Position timeout (1h)",
        "spread_closed": "Spread closed (<0.03%)",
        "profit_take": "Profit taking (spread reversed)",
    }
    reason = result.get("reason", "unknown")
    reason_text = reason_labels.get(reason, reason)

    from positions import position_age_hours  # avoid circular at module level
    age = position_age_hours(position.entry_time) if hasattr(position, 'entry_time') else 0

    text = (
        f"🔒 *POSITION CLOSED — {position.strategy.upper()}*\n\n"
        f"📍 {position.symbol} on {position.exchange}\n"
        f"📊 Side: {position.side} | Qty: {position.quantity:.4f}\n"
        f"💵 Entry: ${position.entry_price:.4f} → Exit: ${result.get('close_price', 0):.4f}\n"
        f"{pnl_emoji} PnL: *${pnl:+.2f}*\n"
        f"⏱ Duration: {age:.1f}h\n"
        f"📝 Reason: {reason_text}\n\n"
        f"🤖 {status}"
    )

    await send_message(bot, text)
