"""Funding Rate Arbitrage — Scan for profitable funding rate opportunities on Hyperliquid.

Strategy: When funding rate is highly positive (longs pay shorts), we:
  1. Buy spot on Hyperliquid
  2. Short perpetual on Hyperliquid (same size)
  -> Delta neutral position that collects funding every hour

When funding rate is highly negative (shorts pay longs):
  1. Long perpetual on Hyperliquid
  -> Collect negative funding

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
        perp_symbols = {}
        for s, m in hl.markets.items():
            if m.get("swap") or m.get("linear"):
                base = m.get("base", "")
                if base:
                    perp_symbols[base] = s

        near_misses = []

        for pair in cfg.spread_pairs:
            base = pair.split("/")[0]
            perp_symbol = perp_symbols.get(base)

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
                        for s, m in ex.markets.items():
                            if (m.get("base") == base and
                                (m.get("swap") or m.get("linear")) and
                                m.get("quote") in ("USDT", "USDC")):
                                f = await ex.fetch_funding_rate(s)
                                comparison_rates[name] = {
                                    "rate": f.get("fundingRate", 0) or 0,
                                    "symbol": s,
                                }
                                break
                    except Exception:
                        pass

                abs_rate = abs(rate)
                abs_annualized = abs(annualized)

                if abs_rate >= cfg.fr_min_rate / 100 and abs_annualized >= cfg.fr_min_annualized:
                    # Determine strategy direction
                    if rate > 0:
                        strategy = "LONG_SPOT + SHORT_PERP (longs pay shorts)"
                        direction = "short_perp"
                    else:
                        strategy = "LONG_PERP (shorts pay longs)"
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
                elif abs_annualized >= 3.0:
                    # Track near-misses for diagnostics
                    near_misses.append(f"{base}: {annualized:+.1f}% ann")

            except Exception as e:
                logger.debug(f"Funding rate fetch failed for {pair}: {e}")

        if near_misses:
            logger.info(f"Funding near-misses (below {cfg.fr_min_annualized}% ann): {', '.join(near_misses)}")

    except Exception as e:
        logger.error(f"Funding rate scan failed: {e}")
    finally:
        for ex in exchanges.values():
            try:
                await ex.close()
            except Exception:
                pass

    opportunities.sort(key=lambda x: abs(x["annualized_pct"]), reverse=True)
    logger.info(f"Funding scan: {len(opportunities)} opportunities found")
    return opportunities
