"""PolyAgent v2 — Unified AI Trading Agent
Combines Polymarket predictions, PM arbitrage, funding rate arb, and cross-exchange spreads.
"""
import asyncio
import json
import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
)
from config import cfg

# ── Modules ──
from polymarket.scanner import scan_prediction_markets, scan_arb_opportunities
from polymarket.research import research_market
from polymarket.estimator import estimate_market
from polymarket.trader import execute_prediction_bet, execute_arb
from polymarket.micro_arb import scan_micro_arb, execute_micro_arb
from crypto.funding import scan_funding_rates
from crypto.spreads import scan_spreads
from crypto.executor import execute_funding_arb, execute_spread_trade, get_balances
from notifier import (
    notify_prediction, notify_pm_arb, notify_funding,
    notify_spread, notify_micro_arb, notify_scan_summary, send_message,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("polyagent")

# ── In-memory state for callback matching ──
_pending_markets = {}    # market_id -> (market, estimate)
_pending_arbs = {}       # market_id -> opportunity
_pending_funding = {}    # pair -> opportunity
_pending_spreads = {}    # pair -> opportunity
_pending_micro = {}      # market_id -> opportunity


# ═══════════════════════════════════════════════════
# SCAN JOBS
# ═══════════════════════════════════════════════════

async def full_scan(context: ContextTypes.DEFAULT_TYPE):
    """Run all scanners and send opportunities."""
    bot = context.bot
    logger.info("═══ Starting full scan ═══")
    stats = {"predictions": 0, "pm_arbs": 0, "micro_arbs": 0, "funding": 0, "spreads": 0}

    # ── 1. Polymarket Predictions ──
    try:
        markets = await scan_prediction_markets()
        stats["predictions"] = len(markets)
        for market in markets[:5]:  # Analyze top 5
            research = await research_market(market)
            estimate = await estimate_market(market, research)
            if estimate and estimate["side"] != "SKIP" and estimate["abs_edge"] >= cfg.pm_min_edge:
                _pending_markets[market["id"]] = (market, estimate)
                await notify_prediction(bot, market, estimate, research)
    except Exception as e:
        logger.error(f"Prediction scan failed: {e}")

    # ── 2. Polymarket YES+NO Arbitrage ──
    try:
        arbs = await scan_arb_opportunities()
        stats["pm_arbs"] = len(arbs)
        for opp in arbs[:3]:
            _pending_arbs[opp["id"]] = opp
            await notify_pm_arb(bot, opp)
    except Exception as e:
        logger.error(f"PM arb scan failed: {e}")

    # ── 2.5. Polymarket Micro-Arbitrage (15-min/5-min crypto) ──
    try:
        micro_opps = await scan_micro_arb()
        stats["micro_arbs"] = len(micro_opps)
        for opp in micro_opps[:3]:
            _pending_micro[opp["market_id"]] = opp
            await notify_micro_arb(bot, opp)
    except Exception as e:
        logger.error(f"Micro-arb scan failed: {e}")

    # ── 3. Funding Rate Arbitrage ──
    try:
        funding_opps = await scan_funding_rates()
        stats["funding"] = len(funding_opps)
        for opp in funding_opps[:3]:
            _pending_funding[opp["pair"]] = opp
            await notify_funding(bot, opp)
    except Exception as e:
        logger.error(f"Funding scan failed: {e}")

    # ── 4. Cross-Exchange Spreads ──
    try:
        spread_opps = await scan_spreads()
        stats["spreads"] = len(spread_opps)
        for opp in spread_opps[:3]:
            _pending_spreads[opp["pair"]] = opp
            await notify_spread(bot, opp)
    except Exception as e:
        logger.error(f"Spread scan failed: {e}")

    await notify_scan_summary(bot, stats)
    logger.info(f"═══ Scan complete: {stats} ═══")


async def crypto_scan(context: ContextTypes.DEFAULT_TYPE):
    """Quick crypto-only scan (micro-arb + funding + spreads) — runs more frequently."""
    bot = context.bot
    logger.info("── Quick crypto scan ──")

    # Micro-arb runs frequently since it depends on spot price moves
    try:
        micro_opps = await scan_micro_arb()
        for opp in micro_opps[:2]:
            _pending_micro[opp["market_id"]] = opp
            await notify_micro_arb(bot, opp)
    except Exception as e:
        logger.error(f"Quick micro-arb scan failed: {e}")

    try:
        funding_opps = await scan_funding_rates()
        for opp in funding_opps[:2]:
            _pending_funding[opp["pair"]] = opp
            await notify_funding(bot, opp)
    except Exception as e:
        logger.error(f"Quick funding scan failed: {e}")

    try:
        spread_opps = await scan_spreads()
        for opp in spread_opps[:2]:
            _pending_spreads[opp["pair"]] = opp
            await notify_spread(bot, opp)
    except Exception as e:
        logger.error(f"Quick spread scan failed: {e}")


# ═══════════════════════════════════════════════════
# TELEGRAM COMMANDS
# ═══════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *PolyAgent v2* — AI Trading Agent\n\n"
        "📊 /scan — Full scan (predictions + crypto)\n"
        "💹 /crypto — Quick crypto scan (funding + spreads)\n"
        "📋 /status — Show balances and config\n"
        "❓ /help — Show commands",
        parse_mode="Markdown",
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Running full scan...")
    await full_scan(context)


async def cmd_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💹 Running crypto scan...")
    await crypto_scan(context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hl_bal = await get_balances()
    hl_total = sum(float(v) for v in hl_bal.get("total", {}).values() if v)

    text = (
        f"🤖 *PolyAgent v2 Status*\n\n"
        f"💰 *Polymarket bankroll:* ${cfg.poly_bankroll}\n"
        f"💰 *Hyperliquid balance:* ${hl_total:.2f}\n"
        f"🔑 PM keys: {'✅' if cfg.poly_private_key else '❌'}\n"
        f"🔑 HL keys: {'✅' if cfg.hl_private_key else '❌'}\n"
        f"🧠 Claude: {'✅' if cfg.anthropic_key else '❌'}\n"
        f"🔎 Tavily: {'✅' if cfg.tavily_key else '❌'}\n\n"
        f"⏱ PM scan: every {cfg.pm_scan_interval_hours}h\n"
        f"⏱ Crypto scan: every {cfg.fr_scan_interval_min}min\n"
        f"📊 Spread pairs: {len(cfg.spread_pairs)}\n"
        f"📊 Pending signals: {len(_pending_markets)} PM, "
        f"{len(_pending_funding)} FR, {len(_pending_spreads)} spreads"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Commands*\n\n"
        "/scan — Full scan (PM predictions + arbs + crypto)\n"
        "/crypto — Quick crypto scan (funding + spreads)\n"
        "/status — Balances and configuration\n"
        "/help — This message\n\n"
        "Use inline buttons to execute or skip opportunities.",
        parse_mode="Markdown",
    )


# ═══════════════════════════════════════════════════
# CALLBACK HANDLER (inline buttons)
# ═══════════════════════════════════════════════════

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "skip":
        await query.edit_message_reply_markup(reply_markup=None)
        return

    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return

    action = payload.get("action")

    if action == "pm_bet":
        market_id = payload.get("market_id")
        if market_id in _pending_markets:
            market, estimate = _pending_markets.pop(market_id)
            result = await execute_prediction_bet(market, estimate)
            status = "✅ Executed" if result["success"] else f"❌ Failed: {result.get('error', '')}"
            await query.edit_message_reply_markup(reply_markup=None)
            await send_message(context.bot, f"{status}\n{market['question'][:60]}")

    elif action == "pm_arb":
        opp_id = payload.get("id")
        if opp_id in _pending_arbs:
            opp = _pending_arbs.pop(opp_id)
            result = await execute_arb(opp)
            status = "✅ Arb executed" if result["success"] else f"❌ Failed: {result.get('error', '')}"
            await query.edit_message_reply_markup(reply_markup=None)
            await send_message(context.bot, status)

    elif action == "fr_arb":
        pair = payload.get("pair")
        if pair in _pending_funding:
            opp = _pending_funding.pop(pair)
            result = await execute_funding_arb(opp)
            status = "✅ Funding arb executed" if result["success"] else f"❌ Failed: {result.get('error', '')}"
            await query.edit_message_reply_markup(reply_markup=None)
            await send_message(context.bot, status)

    elif action == "spread":
        pair = payload.get("pair")
        if pair in _pending_spreads:
            opp = _pending_spreads.pop(pair)
            result = await execute_spread_trade(opp)
            if result["success"]:
                note = result.get("note", "")
                status = f"✅ Spread trade executed\n{note}"
            else:
                status = f"❌ Failed: {result.get('error', '')}"
            await query.edit_message_reply_markup(reply_markup=None)
            await send_message(context.bot, status)

    elif action == "micro":
        mid = payload.get("id")
        if mid in _pending_micro:
            opp = _pending_micro.pop(mid)
            result = await execute_micro_arb(opp)
            if result["success"]:
                status = f"✅ Micro-arb: {result['side']} @ ${result['price']:.3f} (edge {result['edge_pct']:.1f}%)"
            else:
                status = f"❌ Failed: {result.get('error', '')}"
            await query.edit_message_reply_markup(reply_markup=None)
            await send_message(context.bot, status)


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════

def main():
    if not cfg.telegram_token:
        logger.error("TELEGRAM_BOT_TOKEN not set. Exiting.")
        return

    app = Application.builder().token(cfg.telegram_token).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("crypto", cmd_crypto))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(button_handler))

    # Scheduled jobs
    jq = app.job_queue

    # Full scan every N hours (predictions + arbs + crypto)
    jq.run_repeating(
        full_scan,
        interval=cfg.pm_scan_interval_hours * 3600,
        first=30,  # Start 30s after boot
        name="full_scan",
    )

    # Quick crypto scan every N minutes
    jq.run_repeating(
        crypto_scan,
        interval=cfg.fr_scan_interval_min * 60,
        first=120,  # Start 2min after boot
        name="crypto_scan",
    )

    logger.info("🤖 PolyAgent v2 starting...")
    logger.info(f"   PM scan: every {cfg.pm_scan_interval_hours}h")
    logger.info(f"   Crypto scan: every {cfg.fr_scan_interval_min}min")
    logger.info(f"   Strategies: Predictions + PM Arb + Funding Rate + Spreads")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
