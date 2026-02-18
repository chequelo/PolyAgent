"""Cross-Exchange Spread Scanner — Detect price discrepancies across CEXs.

Scans for price spreads between all exchange pairs where we have execution
capability (Hyperliquid and/or Binance). Prioritizes two-sided trades
(both legs executable) over one-sided.
"""
import asyncio
import logging
import ccxt.async_support as ccxt
from config import cfg

logger = logging.getLogger("polyagent.crypto.spreads")

MAX_PLAUSIBLE_SPREAD_PCT = 2.0


def _executable_exchanges() -> set[str]:
    """Exchanges where we have API keys for execution."""
    execs = set()
    if cfg.hl_private_key:
        execs.add("hyperliquid")
    if cfg.binance_api_key:
        execs.add("binance")
    return execs


async def scan_spreads() -> list[dict]:
    """Scan for cross-exchange price spreads.

    Compares prices across all exchanges, only reports opportunities
    where at least one side is an executable exchange.
    """
    opportunities = []
    exec_exchanges = _executable_exchanges()

    if not exec_exchanges:
        logger.warning("No executable exchanges configured, skipping spread scan")
        return []

    # Initialize all exchanges (public API for price scanning)
    exchanges = {}
    all_names = list(set(["hyperliquid"] + cfg.spread_exchanges))
    for name in all_names:
        try:
            exchange_class = getattr(ccxt, name)
            exchanges[name] = exchange_class({"enableRateLimit": True})
        except Exception as e:
            logger.debug(f"Failed to init {name}: {e}")

    try:
        load_tasks = [ex.load_markets() for ex in exchanges.values()]
        await asyncio.gather(*load_tasks, return_exceptions=True)

        near_misses = []

        for pair in cfg.spread_pairs:
            base, quote = pair.split("/")

            # Fetch prices from all exchanges
            prices = {}
            for name, ex in exchanges.items():
                symbol = _find_symbol(ex, base, quote, name)
                if not symbol:
                    continue
                try:
                    ticker = await ex.fetch_ticker(symbol)
                    if ticker and ticker.get("bid") and ticker.get("ask"):
                        prices[name] = {
                            "bid": ticker["bid"],
                            "ask": ticker["ask"],
                            "symbol": symbol,
                        }
                except Exception:
                    pass

            if len(prices) < 2:
                continue

            # Compare all pairs of exchanges
            exchange_names = list(prices.keys())
            for i in range(len(exchange_names)):
                for j in range(i + 1, len(exchange_names)):
                    ex_a = exchange_names[i]
                    ex_b = exchange_names[j]

                    # At least one side must be executable
                    a_exec = ex_a in exec_exchanges
                    b_exec = ex_b in exec_exchanges
                    if not a_exec and not b_exec:
                        continue

                    a_bid = prices[ex_a]["bid"]
                    a_ask = prices[ex_a]["ask"]
                    b_bid = prices[ex_b]["bid"]
                    b_ask = prices[ex_b]["ask"]

                    # Direction 1: buy on A, sell on B
                    spread_ab = (b_bid - a_ask) / a_ask * 100
                    # Direction 2: buy on B, sell on A
                    spread_ba = (a_bid - b_ask) / b_ask * 100

                    if spread_ab > spread_ba:
                        spread_pct = spread_ab
                        buy_ex, sell_ex = ex_a, ex_b
                        buy_price, sell_price = a_ask, b_bid
                    else:
                        spread_pct = spread_ba
                        buy_ex, sell_ex = ex_b, ex_a
                        buy_price, sell_price = b_ask, a_bid

                    if spread_pct <= 0:
                        continue

                    if spread_pct > MAX_PLAUSIBLE_SPREAD_PCT:
                        logger.warning(
                            f"Rejecting implausible {pair}: {spread_pct:.2f}% "
                            f"({buy_ex} ask={buy_price} vs {sell_ex} bid={sell_price})"
                        )
                        continue

                    both_executable = buy_ex in exec_exchanges and sell_ex in exec_exchanges

                    fee_pct = 0.20  # ~0.1% maker each side
                    net_profit_pct = spread_pct - fee_pct

                    if spread_pct >= cfg.spread_min_pct and net_profit_pct > 0:
                        position = min(cfg.spread_max_position, 10.0)
                        est_profit = position * net_profit_pct / 100

                        opportunities.append({
                            "pair": pair,
                            "buy_exchange": buy_ex,
                            "buy_price": buy_price,
                            "sell_exchange": sell_ex,
                            "sell_price": sell_price,
                            "spread_pct": round(spread_pct, 4),
                            "net_profit_pct": round(net_profit_pct, 4),
                            "est_profit_usd": round(est_profit, 4),
                            "both_executable": both_executable,
                            "all_prices": {
                                buy_ex: {"bid": prices[buy_ex]["bid"], "ask": prices[buy_ex]["ask"]},
                                sell_ex: {"bid": prices[sell_ex]["bid"], "ask": prices[sell_ex]["ask"]},
                            },
                            "executable": True,
                        })
                    elif spread_pct > 0.05:
                        near_misses.append(
                            f"{pair}: {spread_pct:.3f}% ({buy_ex}->{sell_ex})"
                        )

        if near_misses:
            logger.info(f"Spread near-misses: {', '.join(near_misses[:8])}")

    except Exception as e:
        logger.error(f"Spread scan failed: {e}")
    finally:
        for ex in exchanges.values():
            try:
                await ex.close()
            except Exception:
                pass

    # Sort: two-sided first, then by profit
    opportunities.sort(key=lambda x: (x["both_executable"], x["net_profit_pct"]), reverse=True)
    logger.info(f"Spread scan: {len(opportunities)} opportunities found")
    return opportunities


def _find_symbol(exchange, base: str, quote: str, exchange_name: str) -> str | None:
    """Find the best symbol for a base/quote pair on an exchange."""
    pair = f"{base}/{quote}"

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

    # 3. For Hyperliquid only: fall back to linear perpetual
    if exchange_name == "hyperliquid":
        for symbol, market in exchange.markets.items():
            if (market.get("base") == base and
                (market.get("swap") or market.get("linear")) and
                not market.get("option") and
                market.get("quote") in ("USDT", "USDC", "USD")):
                return symbol

    return None
