"""Cross-Exchange Spread Scanner — Detect price discrepancies across CEXs.

Monitors price differences between Hyperliquid and major CEXs.
Only executes on Hyperliquid (the only exchange with API keys).
Alerts when spreads exceed threshold for manual/future execution on other side.
"""
import asyncio
import logging
import ccxt.async_support as ccxt
from config import cfg

logger = logging.getLogger("polyagent.crypto.spreads")


async def scan_spreads() -> list[dict]:
    """Scan for cross-exchange price spreads."""
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
        # Load all markets
        load_tasks = [ex.load_markets() for ex in exchanges.values()]
        await asyncio.gather(*load_tasks, return_exceptions=True)

        for pair in cfg.spread_pairs:
            prices = {}

            # Fetch ticker from each exchange
            fetch_tasks = []
            for name, ex in exchanges.items():
                # Find matching spot symbol
                spot_symbol = None
                for s in ex.symbols:
                    if (pair.replace("/", "") in s.replace("/", "") and
                        not ex.markets[s].get("swap") and
                        not ex.markets[s].get("future")):
                        spot_symbol = s
                        break
                if not spot_symbol:
                    # Try direct
                    if pair in ex.symbols:
                        spot_symbol = pair
                if spot_symbol:
                    fetch_tasks.append((name, spot_symbol, ex))

            for name, symbol, ex in fetch_tasks:
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

            if spread_pct >= cfg.spread_min_pct:
                # Estimate profit after typical fees (0.1% maker on each side = 0.2% total)
                fee_pct = 0.20
                net_profit_pct = spread_pct - fee_pct

                if net_profit_pct > 0:
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
                        "executable": "hyperliquid" in [best_buy_exchange, best_sell_exchange],
                    })

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
