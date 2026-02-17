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
        status = f"✅ Executed\n{note}" if note else "✅ Executed"
    else:
        status = f"❌ Failed: {result.get('error', 'unknown')}"

    text = (
        f"📊 *SPREAD — AUTO-EXECUTED*\n\n"
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


async def notify_scan_summary(bot, stats: dict):
    """Send periodic scan summary."""
    text = (
        f"🔍 *SCAN COMPLETE*\n\n"
        f"📊 Predictions analyzed: {stats.get('predictions', 0)}\n"
        f"🔄 PM Arb opportunities: {stats.get('pm_arbs', 0)}\n"
        f"⚡ Micro-arb signals: {stats.get('micro_arbs', 0)}\n"
        f"💹 Funding opportunities: {stats.get('funding', 0)}\n"
        f"📈 Spread opportunities: {stats.get('spreads', 0)}\n"
        f"⏱ Next scan in {cfg.pm_scan_interval_hours}h"
    )
    await send_message(bot, text)
