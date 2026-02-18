"""Cross-Exchange Spread Scanner — Detect price discrepancies across CEXs.

Monitors price differences between Hyperliquid and major CEXs.
Hyperliquid is mainly a perp exchange, so we compare its perp prices
against spot prices on Binance/Bybit/OKX. Only executes on Hyperliquid.
"""
import asyncio
import logging
import ccxt.async_support as ccxt
from config import cfg

logger = logging.getLogger("polyagent.crypto.spreads")

# Reject spreads above this as likely data errors
MAX_PLAUSIBLE_SPREAD_PCT = 2.0


async def scan_spreads() -> list[dict]:
    """Scan for cross-exchange price spreads.

    Compare Hyperliquid prices against each other exchange individually.
    Only report spreads where Hyperliquid is on one side.
    """
    opportunities = []

    # Initialize exchanges
    exchanges = {}
    all_names = ["hyperliquid"] + cfg.spread_exchanges
    for name in all_names:
        try:
            exchange_class = getattr(ccxt, name)
            exchanges[name] = exchange_class({"enableRateLimit": True})
        except Exception as e:
            logger.debug(f"Failed to init {name}: {e}")

    if "hyperliquid" not in exchanges:
        logger.error("Hyperliquid not available, skipping spread scan")
        return []

    try:
        # Load all markets in parallel
        load_tasks = [ex.load_markets() for ex in exchanges.values()]
        await asyncio.gather(*load_tasks, return_exceptions=True)

        hl = exchanges["hyperliquid"]
        near_misses = []

        for pair in cfg.spread_pairs:
            base = pair.split("/")[0]

            # Get Hyperliquid price (perp or spot)
            hl_symbol = _find_hl_symbol(hl, base)
            if not hl_symbol:
                continue

            try:
                hl_ticker = await hl.fetch_ticker(hl_symbol)
                if not hl_ticker or not hl_ticker.get("bid") or not hl_ticker.get("ask"):
                    continue
                hl_bid = hl_ticker["bid"]
                hl_ask = hl_ticker["ask"]
            except Exception:
                continue

            # Compare against each other exchange (spot only)
            for name, ex in exchanges.items():
                if name == "hyperliquid":
                    continue

                spot_symbol = _find_spot_symbol(ex, pair)
                if not spot_symbol:
                    continue

                try:
                    ticker = await ex.fetch_ticker(spot_symbol)
                    if not ticker or not ticker.get("bid") or not ticker.get("ask"):
                        continue
                    ex_bid = ticker["bid"]
                    ex_ask = ticker["ask"]
                except Exception:
                    continue

                # Check both directions:
                # 1. Buy on HL, sell on other exchange
                spread_buy_hl = (ex_bid - hl_ask) / hl_ask * 100
                # 2. Buy on other exchange, sell on HL
                spread_buy_other = (hl_bid - ex_ask) / ex_ask * 100

                # Pick the better direction
                if spread_buy_hl > spread_buy_other:
                    spread_pct = spread_buy_hl
                    buy_exchange = "hyperliquid"
                    buy_price = hl_ask
                    sell_exchange = name
                    sell_price = ex_bid
                else:
                    spread_pct = spread_buy_other
                    buy_exchange = name
                    buy_price = ex_ask
                    sell_exchange = "hyperliquid"
                    sell_price = hl_bid

                # Reject implausible spreads
                if spread_pct > MAX_PLAUSIBLE_SPREAD_PCT:
                    logger.warning(
                        f"Rejecting implausible {pair}: {spread_pct:.2f}% "
                        f"(HL {hl_symbol} bid/ask={hl_bid}/{hl_ask} vs "
                        f"{name} {spot_symbol} bid/ask={ex_bid}/{ex_ask})"
                    )
                    continue

                if spread_pct < 0:
                    continue

                fee_pct = 0.20  # ~0.1% maker each side
                net_profit_pct = spread_pct - fee_pct

                if spread_pct >= cfg.spread_min_pct and net_profit_pct > 0:
                    position = min(cfg.hl_bankroll * 0.3, 10.0)
                    est_profit = position * net_profit_pct / 100

                    opportunities.append({
                        "pair": pair,
                        "buy_exchange": buy_exchange,
                        "buy_price": buy_price,
                        "sell_exchange": sell_exchange,
                        "sell_price": sell_price,
                        "spread_pct": round(spread_pct, 4),
                        "net_profit_pct": round(net_profit_pct, 4),
                        "est_profit_usd": round(est_profit, 4),
                        "all_prices": {
                            "hyperliquid": {"bid": hl_bid, "ask": hl_ask, "symbol": hl_symbol},
                            name: {"bid": ex_bid, "ask": ex_ask, "symbol": spot_symbol},
                        },
                        "executable": True,
                    })
                elif spread_pct > 0.05:
                    near_misses.append(
                        f"{pair}: {spread_pct:.3f}% (HL vs {name})"
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

    opportunities.sort(key=lambda x: x["net_profit_pct"], reverse=True)
    logger.info(f"Spread scan: {len(opportunities)} opportunities found")
    return opportunities


def _find_hl_symbol(hl, base: str) -> str | None:
    """Find the best Hyperliquid symbol for a base asset (perp or spot)."""
    # Try spot first
    for symbol, market in hl.markets.items():
        if (market.get("base") == base and
            market.get("spot", False) and
            market.get("quote") in ("USDT", "USDC", "USD")):
            return symbol

    # Fall back to perpetual
    for symbol, market in hl.markets.items():
        if (market.get("base") == base and
            (market.get("swap") or market.get("linear")) and
            not market.get("option") and
            market.get("quote") in ("USDT", "USDC", "USD")):
            return symbol

    return None


def _find_spot_symbol(exchange, pair: str) -> str | None:
    """Find the exact spot symbol for a pair on an exchange. Spot only, no perps."""
    # Try direct match
    if pair in exchange.symbols:
        market = exchange.markets[pair]
        if market.get("spot", False) and not market.get("swap") and not market.get("future"):
            return pair

    # Search for matching spot market
    base, quote = pair.split("/")
    for symbol, market in exchange.markets.items():
        if (market.get("base") == base and
            market.get("quote") == quote and
            market.get("spot", False) and
            not market.get("swap") and
            not market.get("future") and
            not market.get("option")):
            return symbol

    return None
