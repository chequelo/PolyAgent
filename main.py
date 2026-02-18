"""PolyAgent v2 — Unified AI Trading Agent
Combines Polymarket predictions, PM arbitrage, funding rate arb, and cross-exchange spreads.
"""
import asyncio
import logging
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
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
                result = await execute_prediction_bet(market, estimate)
                await notify_prediction(bot, market, estimate, research, result)
    except Exception as e:
        logger.error(f"Prediction scan failed: {e}")

    # ── 2. Polymarket YES+NO Arbitrage ──
    try:
        arbs = await scan_arb_opportunities()
        stats["pm_arbs"] = len(arbs)
        for opp in arbs[:3]:
            result = await execute_arb(opp)
            await notify_pm_arb(bot, opp, result)
    except Exception as e:
        logger.error(f"PM arb scan failed: {e}")

    # ── 2.5. Polymarket Micro-Arbitrage (15-min/5-min crypto) ──
    try:
        micro_opps = await scan_micro_arb()
        stats["micro_arbs"] = len(micro_opps)
        for opp in micro_opps[:3]:
            result = await execute_micro_arb(opp)
            await notify_micro_arb(bot, opp, result)
    except Exception as e:
        logger.error(f"Micro-arb scan failed: {e}")

    # ── 3. Funding Rate Arbitrage ──
    try:
        funding_opps = await scan_funding_rates()
        stats["funding"] = len(funding_opps)
        for opp in funding_opps[:3]:
            result = await execute_funding_arb(opp)
            await notify_funding(bot, opp, result)
    except Exception as e:
        logger.error(f"Funding scan failed: {e}")

    # ── 4. Cross-Exchange Spreads ──
    try:
        spread_opps = await scan_spreads()
        stats["spreads"] = len(spread_opps)
        for opp in spread_opps[:3]:
            result = await execute_spread_trade(opp)
            await notify_spread(bot, opp, result)
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
            result = await execute_micro_arb(opp)
            await notify_micro_arb(bot, opp, result)
    except Exception as e:
        logger.error(f"Quick micro-arb scan failed: {e}")

    try:
        funding_opps = await scan_funding_rates()
        for opp in funding_opps[:2]:
            result = await execute_funding_arb(opp)
            await notify_funding(bot, opp, result)
    except Exception as e:
        logger.error(f"Quick funding scan failed: {e}")

    try:
        spread_opps = await scan_spreads()
        for opp in spread_opps[:2]:
            result = await execute_spread_trade(opp)
            await notify_spread(bot, opp, result)
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
    balances = await get_balances()

    bal_lines = []
    for ex_name, ex_bals in balances.items():
        total = sum(ex_bals.values())
        bal_lines.append(f"  {ex_name}: ${total:.2f}")

    text = (
        f"🤖 *PolyAgent v2 Status*\n\n"
        f"💰 *Polymarket bankroll:* ${cfg.poly_bankroll}\n"
        f"💰 *Exchange balances:*\n" + "\n".join(bal_lines or ["  (none)"]) + "\n"
        f"🔑 PM keys: {'✅' if cfg.poly_private_key else '❌'}\n"
        f"🔑 HL keys: {'✅' if cfg.hl_private_key else '❌'}\n"
        f"🔑 Binance: {'✅' if cfg.binance_api_key else '❌'}\n"
        f"🧠 Claude: {'✅' if cfg.anthropic_key else '❌'}\n"
        f"🔎 Tavily: {'✅' if cfg.tavily_key else '❌'}\n\n"
        f"⏱ PM scan: every {cfg.pm_scan_interval_hours}h\n"
        f"⏱ Crypto scan: every {cfg.fr_scan_interval_min}min\n"
        f"📊 Spread pairs: {len(cfg.spread_pairs)}\n"
        f"🤖 Mode: Auto-execute"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Commands*\n\n"
        "/scan — Full scan (PM predictions + arbs + crypto)\n"
        "/crypto — Quick crypto scan (funding + spreads)\n"
        "/status — Balances and configuration\n"
        "/proxytest — Test proxy and CLOB connectivity\n"
        "/help — This message\n\n"
        "Trades are executed automatically when opportunities are found.",
        parse_mode="Markdown",
    )


async def cmd_proxytest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug command: test proxy, IP, and CLOB connectivity."""
    import httpx
    lines = []

    # 1. Direct IP (no proxy)
    try:
        direct_ip = httpx.get("https://api.ipify.org?format=json", timeout=10).json().get("ip")
        lines.append(f"🌐 Direct IP: `{direct_ip}`")
    except Exception as e:
        lines.append(f"🌐 Direct IP: error ({e})")

    # 2. Proxy config
    lines.append(f"🔧 Proxy configured: {'✅' if cfg.poly_proxy_url else '❌'}")
    if cfg.poly_proxy_url:
        lines.append(f"🔧 Proxy host: `{cfg.poly_proxy_url.split('@')[-1]}`")

    # 3. Proxy IP
    if cfg.poly_proxy_url:
        try:
            proxy_ip = httpx.Client(proxy=cfg.poly_proxy_url).get(
                "https://api.ipify.org?format=json", timeout=10
            ).json().get("ip")
            lines.append(f"🏠 Proxy IP: `{proxy_ip}`")
        except Exception as e:
            lines.append(f"🏠 Proxy IP: ❌ error ({e})")

    # 4. CLOB via proxy
    try:
        import py_clob_client.http_helpers.helpers as helpers
        has_proxy = hasattr(helpers._http_client, '_transport') and helpers._http_client._transport is not None
        lines.append(f"🔌 httpx client patched: {'✅' if has_proxy else '❌'}")
        resp = helpers._http_client.get("https://clob.polymarket.com/")
        lines.append(f"📡 CLOB GET /: {resp.status_code}")
    except Exception as e:
        lines.append(f"📡 CLOB GET /: ❌ ({e})")

    # 5. CLOB base URL
    lines.append(f"🔗 CLOB URL: `{cfg.poly_clob_url}`")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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
    app.add_handler(CommandHandler("proxytest", cmd_proxytest))

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
