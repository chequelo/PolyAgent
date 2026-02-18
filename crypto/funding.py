"""Funding Rate Arbitrage — Scan for profitable funding rate opportunities.

Two strategies:
1. Absolute: High HL funding rate -> delta neutral (spot + perp hedge)
2. Differential: HL rate vs Binance rate differs -> long cheap / short expensive

Hyperliquid funding is paid every 1 hour (vs 8h on Binance).
"""
import asyncio
import logging
import ccxt.async_support as ccxt
from config import cfg

logger = logging.getLogger("polyagent.crypto.funding")

# Module-level diagnostics
last_scan_diagnostics = {"rates": [], "near_misses": []}


async def scan_funding_rates() -> list[dict]:
    """Scan funding rates across Hyperliquid and compare with other exchanges."""
    global last_scan_diagnostics
    opportunities = []
    all_rates = []
    near_misses = []

    exchanges = {
        "hyperliquid": ccxt.hyperliquid({"enableRateLimit": True, "options": {"builderFee": False}}),
    }

    for name in cfg.spread_exchanges:
        try:
            exchange_class = getattr(ccxt, name)
            exchanges[name] = exchange_class({"enableRateLimit": True})
        except Exception:
            pass

    try:
        # Load all markets in parallel
        load_tasks = [ex.load_markets() for ex in exchanges.values()]
        await asyncio.gather(*load_tasks, return_exceptions=True)

        hl = exchanges["hyperliquid"]

        # Build perp symbol maps for each exchange
        perp_maps = {}
        for ex_name, ex in exchanges.items():
            perp_maps[ex_name] = {}
            for s, m in ex.markets.items():
                if m.get("swap") or m.get("linear"):
                    base = m.get("base", "")
                    if base and m.get("quote") in ("USDT", "USDC", "USD"):
                        perp_maps[ex_name][base] = s

        for pair in cfg.spread_pairs:
            base = pair.split("/")[0]
            hl_symbol = perp_maps.get("hyperliquid", {}).get(base)
            if not hl_symbol:
                continue

            try:
                # Hyperliquid funding rate
                funding = await hl.fetch_funding_rate(hl_symbol)
                hl_rate = funding.get("fundingRate", 0) or 0
                hl_annualized = hl_rate * 24 * 365 * 100  # HL is hourly

                # Get comparison rates
                comparison_rates = {}
                for ex_name, ex in exchanges.items():
                    if ex_name == "hyperliquid":
                        continue
                    comp_symbol = perp_maps.get(ex_name, {}).get(base)
                    if not comp_symbol:
                        continue
                    try:
                        f = await ex.fetch_funding_rate(comp_symbol)
                        comp_rate = f.get("fundingRate", 0) or 0
                        # Binance/Bybit/OKX pay every 8h
                        comp_annualized = comp_rate * 3 * 365 * 100
                        comparison_rates[ex_name] = {
                            "rate": comp_rate,
                            "rate_pct": comp_rate * 100,
                            "annualized": comp_annualized,
                            "symbol": comp_symbol,
                        }
                    except Exception:
                        pass

                # Track all rates for diagnostics
                rate_info = {
                    "pair": base,
                    "hl_ann": round(hl_annualized, 1),
                }
                for ex_name, comp in comparison_rates.items():
                    rate_info[f"{ex_name}_ann"] = round(comp["annualized"], 1)
                all_rates.append(rate_info)

                # ── Strategy 1: Absolute rate on HL ──
                abs_annualized = abs(hl_annualized)
                if abs(hl_rate) >= cfg.fr_min_rate / 100 and abs_annualized >= cfg.fr_min_annualized:
                    if hl_rate > 0:
                        strategy = "LONG_SPOT + SHORT_PERP (longs pay shorts)"
                        direction = "short_perp"
                    else:
                        strategy = "LONG_PERP (shorts pay longs)"
                        direction = "long_perp"

                    opportunities.append({
                        "pair": pair,
                        "type": "absolute",
                        "hl_symbol": hl_symbol,
                        "funding_rate": hl_rate,
                        "funding_rate_pct": hl_rate * 100,
                        "annualized_pct": hl_annualized,
                        "strategy": strategy,
                        "direction": direction,
                        "comparison": comparison_rates,
                        "position_size": min(cfg.fr_max_position, cfg.hl_bankroll * 0.4),
                    })

                # ── Strategy 2: Differential (HL vs Binance) ──
                # If HL rate is much higher than Binance, short on HL + long on Binance (or vice versa)
                for ex_name, comp in comparison_rates.items():
                    diff_annualized = hl_annualized - comp["annualized"]
                    abs_diff = abs(diff_annualized)

                    if abs_diff >= cfg.fr_min_annualized:
                        if diff_annualized > 0:
                            # HL rate higher -> short perp on HL (collect higher funding)
                            strategy = f"SHORT_PERP on HL, LONG_PERP on {ex_name} (rate diff)"
                            direction = "short_perp"
                        else:
                            # HL rate lower -> long perp on HL (pay less)
                            strategy = f"LONG_PERP on HL, SHORT_PERP on {ex_name} (rate diff)"
                            direction = "long_perp"

                        opportunities.append({
                            "pair": pair,
                            "type": "differential",
                            "hl_symbol": hl_symbol,
                            "funding_rate": hl_rate,
                            "funding_rate_pct": hl_rate * 100,
                            "annualized_pct": diff_annualized,
                            "strategy": strategy,
                            "direction": direction,
                            "comparison": {ex_name: comp},
                            "position_size": min(cfg.fr_max_position, cfg.hl_bankroll * 0.4),
                        })

                # Near misses
                if abs_annualized >= 2.0 and abs_annualized < cfg.fr_min_annualized:
                    near_misses.append(f"{base}: {hl_annualized:+.1f}% ann")

            except Exception as e:
                logger.debug(f"Funding rate fetch failed for {pair}: {e}")

        # Sort rates by absolute HL rate for diagnostics
        all_rates.sort(key=lambda x: abs(x.get("hl_ann", 0)), reverse=True)

        last_scan_diagnostics = {
            "rates": all_rates[:8],
            "near_misses": near_misses,
        }

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
