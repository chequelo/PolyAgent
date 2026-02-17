"""Polymarket Trade Execution — Limit orders via CLOB API"""
import logging
from config import cfg

logger = logging.getLogger("polyagent.pm.trader")

_clob_client = None
_region_blocked = None  # None = not checked, True/False = cached result

PM_MIN_SIZE = 1.0     # Minimum $1 order on CLOB
PM_MIN_PRICE = 0.01
PM_MAX_PRICE = 0.99


def _get_client():
    global _clob_client
    if _clob_client is None and cfg.poly_private_key:
        from py_clob_client.client import ClobClient
        _clob_client = ClobClient(
            cfg.poly_clob_url,
            key=cfg.poly_private_key,
            chain_id=137,
            signature_type=1,
            funder=cfg.poly_funder_address,
        )
        logger.info(f"CLOB client initialized → {cfg.poly_clob_url}")
        _clob_client.set_api_creds(_clob_client.create_or_derive_api_creds())
    return _clob_client


def _check_region_access() -> bool:
    """Check if CLOB API is accessible from this IP. Caches result."""
    global _region_blocked
    if _region_blocked is not None:
        return not _region_blocked

    client = _get_client()
    if not client:
        _region_blocked = True
        return False

    try:
        # Lightweight call to test access
        client.get_ok()
        _region_blocked = False
        logger.info("Polymarket CLOB access: OK")
        return True
    except Exception as e:
        err = str(e).lower()
        if "403" in err or "forbidden" in err or "region" in err:
            _region_blocked = True
            logger.warning(f"Polymarket CLOB geo-blocked: {e}")
            return False
        # Transient error — don't cache, allow retry
        logger.warning(f"Polymarket CLOB access check failed (transient): {e}")
        return False


def _validate_order(price: float, size: float) -> str | None:
    """Validate order params. Returns error string or None if valid."""
    if price < PM_MIN_PRICE or price > PM_MAX_PRICE:
        return f"Price ${price:.4f} out of range ({PM_MIN_PRICE}-{PM_MAX_PRICE})"
    if size < PM_MIN_SIZE:
        return f"Size {size:.2f} below minimum ({PM_MIN_SIZE})"
    return None


async def execute_prediction_bet(market: dict, estimate: dict) -> dict:
    """Place a prediction market bet."""
    if not _check_region_access():
        return {"success": False, "error": "Region blocked (403)"}

    client = _get_client()
    if not client:
        return {"success": False, "error": "CLOB client not configured"}

    side = estimate["side"]
    tokens = market.get("tokens", [])
    if len(tokens) < 2:
        return {"success": False, "error": "No token IDs"}

    # YES = tokens[0], NO = tokens[1]
    token_id = tokens[0] if side == "YES" else tokens[1]
    price = market["best_ask"] if side == "YES" else (1 - market["best_bid"])
    price = round(price, 2)  # CLOB uses 0.01 increments
    size = round(estimate["kelly_bet"] / price, 2) if price > 0 else 0

    err = _validate_order(price, size)
    if err:
        return {"success": False, "error": err}

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        order = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY,
        )
        signed = client.create_order(order)
        resp = client.post_order(signed, OrderType.GTC)

        logger.info(f"Order placed: {side} {market['question'][:50]} @ ${price:.3f} x {size:.1f}")
        return {"success": True, "response": str(resp), "side": side, "price": price, "size": size}

    except Exception as e:
        logger.error(f"Order failed: {e}")
        return {"success": False, "error": str(e)}


async def execute_arb(opportunity: dict) -> dict:
    """Execute YES+NO arbitrage: buy both sides."""
    if not _check_region_access():
        return {"success": False, "error": "Region blocked (403)"}

    client = _get_client()
    if not client:
        return {"success": False, "error": "CLOB client not configured"}

    tokens = opportunity.get("tokens", [])
    if len(tokens) < 2:
        return {"success": False, "error": "No token IDs for arb"}

    bet_size = min(
        cfg.pm_arb_max_bet,
        cfg.poly_bankroll * 0.5,  # Max 50% of bankroll on single arb
    )

    yes_price = round(opportunity["yes_price"], 2)
    no_price = round(opportunity["no_price"], 2)
    yes_size = round(bet_size / yes_price, 2) if yes_price > 0 else 0
    no_size = round(bet_size / no_price, 2) if no_price > 0 else 0

    # Validate both legs before executing either
    for label, p, s in [("YES", yes_price, yes_size), ("NO", no_price, no_size)]:
        err = _validate_order(p, s)
        if err:
            return {"success": False, "error": f"{label} leg: {err}"}

    results = []
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        # Buy YES
        yes_order = OrderArgs(
            token_id=tokens[0],
            price=yes_price,
            size=yes_size,
            side=BUY,
        )
        yes_signed = client.create_order(yes_order)
        yes_resp = client.post_order(yes_signed, OrderType.GTC)
        results.append({"side": "YES", "resp": str(yes_resp)})

        # Buy NO
        no_order = OrderArgs(
            token_id=tokens[1],
            price=no_price,
            size=no_size,
            side=BUY,
        )
        no_signed = client.create_order(no_order)
        no_resp = client.post_order(no_signed, OrderType.GTC)
        results.append({"side": "NO", "resp": str(no_resp)})

        expected_profit = bet_size * opportunity["profit_per_dollar"]
        logger.info(f"Arb executed: {opportunity['question'][:50]}, expected profit: ${expected_profit:.3f}")
        return {"success": True, "results": results, "expected_profit": expected_profit}

    except Exception as e:
        logger.error(f"Arb execution failed: {e}")
        return {"success": False, "error": str(e)}
