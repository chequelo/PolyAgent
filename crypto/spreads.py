"""Cross-Exchange Spread Scanner — Detect price discrepancies across CEXs.

Monitors price differences between Hyperliquid and major CEXs.
Compares both spot and perpetual prices (Hyperliquid is mainly a perp exchange).
Only executes on Hyperliquid (the only exchange with API keys).
"""
import asyncio
import logging
import ccxt.async_support as ccxt
from config import cfg

logger = logging.getLogger("polyagent.crypto.spreads")

# Reject spreads above this as likely data errors (stale price, wrong symbol match)
MAX_PLAUSIBLE_SPREAD_PCT = 3.0


async def scan_spreads() -> list[dict]:
    """Scan for cross-exchange price spreads (spot + perp)."""
    opportunities = []

    # Initialize exchanges (all public API, no keys)
    exchanges = {}
    all_names = ["hyperliquid"] + cfg.spread_exchanges
    for name in all_names:
        try:
            exchange_class = getattr(ccxt, name)
            exchanges[name] = exchange_class({"enableRateLimit": True})
        except Exception as e:
            logger.debug(f"Failed to init {name}: {e}")

    try:
        # Load all markets in parallel
        load_tasks = [ex.load_markets() for ex in exchanges.values()]
        await asyncio.gather(*load_tasks, return_exceptions=True)

        near_misses = []  # Track close-to-threshold spreads for logging

        for pair in cfg.spread_pairs:
            prices = {}

            # Fetch from each exchange — try spot first, then perp
            for name, ex in exchanges.items():
                symbol = _find_best_symbol(ex, pair)
                if not symbol:
                    continue
                try:
                    ticker = await ex.fetch_ticker(symbol)
                    if ticker and ticker.get("bid") and ticker.get("ask"):
                        prices[name] = {
                            "bid": ticker["bid"],
                            "ask": ticker["ask"],
                            "mid": (ticker["bid"] + ticker["ask"]) / 2,
                            "spread": ticker["ask"] - ticker["bid"],
                            "symbol": symbol,
                        }
                except Exception:
                    pass

            if len(prices) < 2:
                continue

            # Find best buy (lowest ask) and best sell (highest bid)
            best_buy_exchange = min(prices.keys(), key=lambda k: prices[k]["ask"])
            best_sell_exchange = max(prices.keys(), key=lambda k: prices[k]["bid"])

            if best_buy_exchange == best_sell_exchange:
                continue

            buy_price = prices[best_buy_exchange]["ask"]
            sell_price = prices[best_sell_exchange]["bid"]
            spread_pct = (sell_price - buy_price) / buy_price * 100

            # Sanity check: reject implausible spreads (likely data error)
            if spread_pct > MAX_PLAUSIBLE_SPREAD_PCT:
                logger.warning(
                    f"Rejecting implausible spread: {pair} {spread_pct:.2f}% "
                    f"({best_buy_exchange} {buy_price} vs {best_sell_exchange} {sell_price})"
                )
                continue

            executable = "hyperliquid" in [best_buy_exchange, best_sell_exchange]

            if spread_pct >= cfg.spread_min_pct:
                fee_pct = 0.20  # 0.1% maker on each side
                net_profit_pct = spread_pct - fee_pct

                if net_profit_pct > 0 and executable:
                    position = min(cfg.hl_bankroll * 0.3, 10.0)
                    est_profit = position * net_profit_pct / 100

                    opportunities.append({
                        "pair": pair,
                        "buy_exchange": best_buy_exchange,
                        "buy_price": buy_price,
                        "sell_exchange": best_sell_exchange,
                        "sell_price": sell_price,
                        "spread_pct": round(spread_pct, 4),
                        "net_profit_pct": round(net_profit_pct, 4),
                        "est_profit_usd": round(est_profit, 4),
                        "all_prices": {k: {"bid": v["bid"], "ask": v["ask"]} for k, v in prices.items()},
                        "executable": True,
                    })
            elif spread_pct > 0.05 and executable:
                # Track near-misses for diagnostics
                near_misses.append(f"{pair}: {spread_pct:.3f}% ({best_buy_exchange}->{best_sell_exchange})")

        if near_misses:
            logger.info(f"Spread near-misses (below {cfg.spread_min_pct}%): {', '.join(near_misses[:5])}")

    except Exception as e:
        logger.error(f"Spread scan failed: {e}")
    finally:
        for ex in exchanges.values():
            try:
                await ex.close()
            except Exception:
                pass

    opportunities.sort(key=lambda x: x["net_profit_pct"], reverse=True)
    logger.info(f"Spread scan: {len(opportunities)} opportunities found")
    return opportunities


def _find_best_symbol(exchange, pair: str) -> str | None:
    """Find the best matching symbol for a pair — prefers spot, falls back to perp."""
    base, quote = pair.split("/")

    # 1. Try exact spot match
    if pair in exchange.symbols:
        market = exchange.markets[pair]
        if market.get("spot", False) and not market.get("swap") and not market.get("future"):
            return pair

    # 2. Search for spot market
    for symbol, market in exchange.markets.items():
        if (market.get("base") == base and
            market.get("quote") == quote and
            market.get("spot", False) and
            not market.get("swap") and
            not market.get("future") and
            not market.get("option")):
            return symbol

    # 3. Fall back to linear perpetual (especially for Hyperliquid)
    #    Use USDT or USDC-settled linear perps only
    for symbol, market in exchange.markets.items():
        if (market.get("base") == base and
            (market.get("swap") or market.get("linear")) and
            not market.get("option") and
            market.get("settle") in ("USDT", "USDC", None) and
            market.get("quote") in ("USDT", "USDC", "USD")):
            return symbol

    return None
