"""Funding Rate Arbitrage — Scan for profitable funding rate opportunities on Hyperliquid.

Strategy: When funding rate is highly positive (longs pay shorts), we:
  1. Buy spot on Hyperliquid
  2. Short perpetual on Hyperliquid (same size)
  → Delta neutral position that collects funding every hour

When funding rate is highly negative (shorts pay longs):
  1. Sell/short spot (or skip)
  2. Long perpetual on Hyperliquid
  → Collect negative funding

Hyperliquid funding is paid every 1 hour (vs 8h on Binance).
"""
import asyncio
import logging
import ccxt.async_support as ccxt
from config import cfg

logger = logging.getLogger("polyagent.crypto.funding")


async def scan_funding_rates() -> list[dict]:
    """Scan funding rates across Hyperliquid and compare with other exchanges."""
    opportunities = []

    # Initialize exchanges (public API only for scanning)
    exchanges = {
        "hyperliquid": ccxt.hyperliquid({"enableRateLimit": True}),
    }

    # Add comparison exchanges (public data only, no keys needed)
    for name in cfg.spread_exchanges:
        try:
            exchange_class = getattr(ccxt, name)
            exchanges[name] = exchange_class({"enableRateLimit": True})
        except Exception:
            pass

    try:
        # Get Hyperliquid funding rates
        hl = exchanges["hyperliquid"]
        await hl.load_markets()

        # Get all perpetual markets on Hyperliquid
        perp_symbols = [
            s for s in hl.symbols
            if hl.markets[s].get("swap") or hl.markets[s].get("linear")
        ]

        # Fetch funding rates for top pairs
        target_pairs = cfg.spread_pairs
        for pair in target_pairs:
            # Find matching perp symbol on Hyperliquid
            perp_symbol = None
            for s in perp_symbols:
                base = pair.split("/")[0]
                if base in s and ("USDT" in s or "USDC" in s or "USD" in s):
                    perp_symbol = s
                    break

            if not perp_symbol:
                continue

            try:
                # Hyperliquid funding rate
                funding = await hl.fetch_funding_rate(perp_symbol)
                rate = funding.get("fundingRate", 0) or 0

                # Hyperliquid pays every 1 hour = 24 periods/day = 8760 periods/year
                annualized = rate * 24 * 365 * 100  # percentage

                # Get comparison rates from other exchanges
                comparison_rates = {}
                for name, ex in exchanges.items():
                    if name == "hyperliquid":
                        continue
                    try:
                        await ex.load_markets()
                        # Find matching perp
                        for s in ex.symbols:
                            base = pair.split("/")[0]
                            if (base in s and ("USDT" in s) and
                                (ex.markets[s].get("swap") or ex.markets[s].get("linear"))):
                                f = await ex.fetch_funding_rate(s)
                                comparison_rates[name] = {
                                    "rate": f.get("fundingRate", 0) or 0,
                                    "symbol": s,
                                }
                                break
                    except Exception:
                        pass

                abs_rate = abs(rate)
                if abs_rate >= cfg.fr_min_rate / 100 and abs(annualized) >= cfg.fr_min_annualized:
                    # Determine strategy direction
                    if rate > 0:
                        strategy = "LONG_SPOT + SHORT_PERP (longs pay shorts → collect)"
                        direction = "short_perp"
                    else:
                        strategy = "SHORT_SPOT + LONG_PERP (shorts pay longs → collect)"
                        direction = "long_perp"

                    opportunities.append({
                        "pair": pair,
                        "hl_symbol": perp_symbol,
                        "funding_rate": rate,
                        "funding_rate_pct": rate * 100,
                        "annualized_pct": annualized,
                        "strategy": strategy,
                        "direction": direction,
                        "comparison": comparison_rates,
                        "position_size": min(cfg.fr_max_position, cfg.hl_bankroll * 0.4),
                    })

            except Exception as e:
                logger.debug(f"Funding rate fetch failed for {pair}: {e}")

    except Exception as e:
        logger.error(f"Funding rate scan failed: {e}")
    finally:
        # Close all exchange connections
        for ex in exchanges.values():
            try:
                await ex.close()
            except Exception:
                pass

    opportunities.sort(key=lambda x: abs(x["annualized_pct"]), reverse=True)
    logger.info(f"Funding scan: {len(opportunities)} opportunities found")
    return opportunities
