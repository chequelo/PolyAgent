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
from position_manager import check_positions
from positions import (
    get_open_positions, position_age_hours,
    get_active_market_ids, get_total_pm_exposure, get_category_exposure,
    create_prediction_position,
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

    # ── 1. Polymarket Predictions (Simons: small edge × many bets × low correlation) ──
    PM_MIN_SIZE = 0.50  # Minimum viable bet
    try:
        markets = await scan_prediction_markets()
        stats["predictions"] = len(markets)
        active_ids = get_active_market_ids()
        available_bankroll = cfg.poly_bankroll - get_total_pm_exposure()
        category_exp = get_category_exposure()
        bets_placed = 0

        for market in markets[:cfg.pm_max_markets_per_scan]:
            # Dedup: skip if we already have a position
            if market["id"] in active_ids:
                logger.info(f"Skip (dedup): {market['question'][:60]}")
                continue
            # Category cap: skip if category exposure >= 30% of bankroll
            cat = market.get("category") or "Other"
            if category_exp.get(cat, 0) >= cfg.poly_bankroll * cfg.pm_max_category_exposure:
                logger.info(f"Skip (cat cap {cat}): {market['question'][:60]}")
                continue
            # Bankroll check: stop if no funds left
            if available_bankroll < PM_MIN_SIZE:
                logger.info("Stopping prediction scan: insufficient bankroll")
                break

            research = await research_market(market)
            estimate = await estimate_market(market, research, available_bankroll)
            if estimate and estimate["side"] != "SKIP" and estimate["abs_edge"] >= cfg.pm_min_edge:
                result = await execute_prediction_bet(market, estimate)
                await notify_prediction(bot, market, estimate, research, result)
                # Track position and update exposure
                if result and result.get("success"):
                    bet_size = estimate["kelly_bet"]
                    # Resolve token_id: YES = tokens[0], NO = tokens[1]
                    tokens = market.get("tokens", [])
                    token_id = None
                    if len(tokens) >= 2:
                        token_id = tokens[0] if estimate["side"] == "YES" else tokens[1]
                    create_prediction_position(
                        market_id=market["id"],
                        market_question=market["question"],
                        category=cat,
                        side=estimate["side"],
                        entry_price=market["mid"],
                        size_usd=bet_size,
                        token_id=token_id,
                        estimated_prob=estimate["probability"],
                        original_thesis=estimate.get("thesis", ""),
                    )
                    available_bankroll -= bet_size
                    category_exp[cat] = category_exp.get(cat, 0) + bet_size
                    active_ids.add(market["id"])
                    bets_placed += 1

        logger.info(f"Simons scan: {bets_placed} bets placed, ${available_bankroll:.2f} remaining")
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

    # Collect diagnostics from scanners
    from crypto.spreads import last_scan_diagnostics as spread_diag
    from crypto.funding import last_scan_diagnostics as fund_diag
    diagnostics = {"spreads": spread_diag, "funding": fund_diag}

    await notify_scan_summary(bot, stats, diagnostics)
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
    for ex_name, usd_total in balances.items():
        bal_lines.append(f"  {ex_name}: ${usd_total:.2f}")

    # PM portfolio info
    pm_exposure = get_total_pm_exposure()
    pm_available = cfg.poly_bankroll - pm_exposure
    pm_positions = len(get_active_market_ids())
    cat_exp = get_category_exposure()
    cat_lines = [f"  {cat}: ${amt:.2f}" for cat, amt in sorted(cat_exp.items())] if cat_exp else ["  (none)"]

    text = (
        f"🤖 *PolyAgent v2 Status*\n\n"
        f"💰 *Polymarket:* ${cfg.poly_bankroll} bankroll\n"
        f"  Deployed: ${pm_exposure:.2f} in {pm_positions} markets\n"
        f"  Available: ${pm_available:.2f}\n"
        f"  Min edge: {cfg.pm_min_edge:.0%} | Max/bet: ${cfg.pm_max_bet}\n"
        f"📂 *Category exposure:*\n" + "\n".join(cat_lines) + "\n"
        f"💰 *Exchange balances:*\n" + "\n".join(bal_lines or ["  (none)"]) + "\n"
        f"🔑 PM keys: {'✅' if cfg.poly_private_key else '❌'}\n"
        f"🔑 HL keys: {'✅' if cfg.hl_private_key else '❌'}\n"
        f"🔑 Binance: {'✅' if cfg.binance_api_key else '❌'}\n"
        f"🧠 Claude: {'✅' if cfg.anthropic_key else '❌'}\n"
        f"🔎 Tavily: {'✅' if cfg.tavily_key else '❌'}\n\n"
        f"⏱ PM scan: every {cfg.pm_scan_interval_hours}h (up to {cfg.pm_max_markets_per_scan} markets)\n"
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
        "/positions — Show open positions\n"
        "/status — Balances and configuration\n"
        "/proxytest — Test proxy and CLOB connectivity\n"
        "/help — This message\n\n"
        "Trades are executed and closed automatically.",
        parse_mode="Markdown",
    )


async def manage_positions(context: ContextTypes.DEFAULT_TYPE):
    """Periodic job: check open positions and close when exit criteria met."""
    bot = context.bot
    logger.info("── Position check ──")
    try:
        actions = await check_positions(bot)
        if actions:
            logger.info(f"Position manager closed {len(actions)} positions")
    except Exception as e:
        logger.error(f"Position manager failed: {e}")


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show currently open positions with live monitoring data and totals."""
    positions = get_open_positions()
    if not positions:
        await update.message.reply_text("📭 No open positions.")
        return

    pm_positions = [p for p in positions if p.strategy == "prediction"]
    crypto_positions = [p for p in positions if p.strategy != "prediction"]

    lines = [f"📊 *Open Positions ({len(positions)})*\n"]

    total_deployed = 0.0

    if pm_positions:
        lines.append(f"*Polymarket ({len(pm_positions)}):*")
        for pos in pm_positions:
            age = position_age_hours(pos.entry_time)
            question = pos.market_question or pos.symbol
            cat = pos.category or ""

            side_emoji = '🟢' if pos.side == 'YES' else '🔴'
            lines.append(f"{side_emoji} *{pos.side}* {question[:60]}")

            price_line = f"  💵 Entry: ${pos.entry_price:.3f}"
            if pos.last_check_price and pos.last_check_price != pos.entry_price:
                delta = pos.last_check_price - pos.entry_price
                price_line += f" → Now: ${pos.last_check_price:.3f} ({delta:+.3f})"
            lines.append(price_line)

            if pos.estimated_prob:
                current = pos.last_check_price or pos.entry_price
                if pos.side == "YES":
                    edge = pos.estimated_prob - current
                else:
                    edge = (1 - pos.estimated_prob) - (1 - current)
                edge_emoji = "🟢" if edge >= 0.03 else "🟡" if edge >= 0.01 else "🔴"
                lines.append(
                    f"  {edge_emoji} Prob: {pos.estimated_prob:.1%} | Edge: {edge:+.1%}"
                )

            meta = f"  💰 ${pos.size_usd:.2f}"
            if cat:
                meta += f" | {cat}"
            meta += f" | {age:.1f}h"
            if pos.token_id:
                meta += " | 🔑"
            if pos.last_reeval_time:
                from datetime import datetime, timezone
                try:
                    reeval_ago = (datetime.now(timezone.utc) - datetime.fromisoformat(pos.last_reeval_time)).total_seconds() / 3600
                    meta += f" | Re-eval {reeval_ago:.1f}h ago"
                except Exception:
                    pass
            lines.append(meta)

            if pos.original_thesis:
                lines.append(f"  📝 _{pos.original_thesis[:80]}_")
            lines.append("")

        total_pm = sum(p.size_usd for p in pm_positions)
        total_deployed += total_pm
        lines.append(f"  Subtotal PM: *${total_pm:.2f}* ({len(pm_positions)} pos)\n")

    if crypto_positions:
        lines.append(f"*Crypto ({len(crypto_positions)}):*")
        for pos in crypto_positions:
            age = position_age_hours(pos.entry_time)
            lines.append(
                f"{'🔴' if pos.side == 'short' else '🟢'} "
                f"*{pos.symbol}* {pos.side} on {pos.exchange}\n"
                f"  Entry: ${pos.entry_price:.4f} | Size: ${pos.size_usd:.2f} | {age:.1f}h ago\n"
                f"  Strategy: {pos.strategy}"
            )
            if pos.entry_rate:
                lines.append(f" | Rate: {pos.entry_rate * 100:.4f}%")
            lines.append("")

        total_crypto = sum(p.size_usd for p in crypto_positions)
        total_deployed += total_crypto
        lines.append(f"  Subtotal Crypto: *${total_crypto:.2f}* ({len(crypto_positions)} pos)\n")

    lines.append(f"💼 *Total deployed: ${total_deployed:.2f}* across {len(positions)} positions")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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


async def cmd_balancetest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug command: raw balance output from each exchange."""
    import ccxt.async_support as ccxt_async
    lines = []

    # Hyperliquid
    if cfg.hl_private_key:
        lines.append("*Hyperliquid:*")
        lines.append(f"  wallet: `{cfg.hl_wallet_address}`")

        # Direct API call (bypass CCXT)
        try:
            import httpx as httpx_sync
            resp = httpx_sync.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "clearinghouseState", "user": cfg.hl_wallet_address},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                margin = data.get("marginSummary", {})
                account_value = margin.get("accountValue", "0")
                total_margin = margin.get("totalMarginUsed", "0")
                lines.append(f"  API direct: value=${account_value}, margin=${total_margin}")
            else:
                lines.append(f"  API direct: HTTP {resp.status_code}")
        except Exception as e:
            lines.append(f"  API direct: error ({e})")

        # Also check spot balance via direct API
        try:
            resp = httpx_sync.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "spotClearinghouseState", "user": cfg.hl_wallet_address},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                balances_list = data.get("balances", [])
                spot_bals = {b["coin"]: b["total"] for b in balances_list if float(b.get("total", 0)) > 0}
                lines.append(f"  API spot: {spot_bals or 'empty'}")
            else:
                lines.append(f"  API spot: HTTP {resp.status_code}")
        except Exception as e:
            lines.append(f"  API spot: error ({e})")

        # CCXT check
        try:
            hl = ccxt_async.hyperliquid({
                "privateKey": cfg.hl_private_key,
                "walletAddress": cfg.hl_wallet_address,
                "enableRateLimit": True,
            })
            await hl.load_markets()
            bal = await hl.fetch_balance()
            total = {k: v for k, v in bal.get("total", {}).items() if v and float(v) > 0}
            lines.append(f"  CCXT: {total or 'empty'}")
            await hl.close()
        except Exception as e:
            lines.append(f"  CCXT: error ({e})")
    else:
        lines.append("*Hyperliquid:* no key")

    # Binance
    if cfg.binance_api_key:
        lines.append("*Binance:*")
        try:
            bn = ccxt_async.binance({
                "apiKey": cfg.binance_api_key,
                "secret": cfg.binance_secret,
                "enableRateLimit": True,
            })
            bal = await bn.fetch_balance()
            total = {k: v for k, v in bal.get("total", {}).items() if v and float(v) > 0}
            lines.append(f"  balance: {total or 'empty'}")
            await bn.close()
        except Exception as e:
            lines.append(f"  error: {e}")
    else:
        lines.append("*Binance:* no key")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════

async def post_init(app: Application):
    """Start real-time WebSocket watchers after bot initializes."""
    from watcher import Watcher
    watcher = Watcher(app.bot)
    app.bot_data["watcher"] = watcher
    await watcher.start()
    logger.info("Real-time watchers started (PM + Spread + MicroArb + Funding)")


async def post_shutdown(app: Application):
    """Stop watchers on shutdown."""
    watcher = app.bot_data.get("watcher")
    if watcher:
        await watcher.stop()


def main():
    if not cfg.telegram_token:
        logger.error("TELEGRAM_BOT_TOKEN not set. Exiting.")
        return

    app = (
        Application.builder()
        .token(cfg.telegram_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("crypto", cmd_crypto))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("proxytest", cmd_proxytest))
    app.add_handler(CommandHandler("balancetest", cmd_balancetest))
    app.add_handler(CommandHandler("positions", cmd_positions))

    # Scheduled jobs (fallback — watchers handle real-time monitoring)
    jq = app.job_queue

    # Full scan every N hours (predictions + arbs + crypto)
    jq.run_repeating(
        full_scan,
        interval=cfg.pm_scan_interval_hours * 3600,
        first=30,
        name="full_scan",
    )

    # Quick crypto scan — less frequent now that watchers handle real-time
    jq.run_repeating(
        crypto_scan,
        interval=cfg.fr_scan_interval_min * 60,
        first=120,
        name="crypto_scan",
    )

    # Position manager — fallback checker (watchers handle real-time exits)
    jq.run_repeating(
        manage_positions,
        interval=30 * 60,  # Every 30min (fallback, watchers handle real-time)
        first=180,
        name="position_manager",
    )

    logger.info("🤖 PolyAgent v2 starting...")
    logger.info(f"   PM scan: every {cfg.pm_scan_interval_hours}h")
    logger.info(f"   Crypto scan: every {cfg.fr_scan_interval_min}min")
    logger.info(f"   Position fallback: every 30min (real-time via WebSocket)")
    logger.info(f"   Strategies: Predictions + PM Arb + Funding Rate + Spreads")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
