"""Polymarket Trade Execution — Limit orders via CLOB API"""
import logging
from config import cfg

logger = logging.getLogger("polyagent.pm.trader")

_clob_client = None


def _get_client():
    global _clob_client
    if _clob_client is None and cfg.poly_private_key:
        from py_clob_client.client import ClobClient
        _clob_client = ClobClient(
            "https://clob.polymarket.com",
            key=cfg.poly_private_key,
            chain_id=137,
            signature_type=1,
            funder=cfg.poly_funder_address,
        )
        _clob_client.set_api_creds(_clob_client.create_or_derive_api_creds())
    return _clob_client


async def execute_prediction_bet(market: dict, estimate: dict) -> dict:
    """Place a prediction market bet."""
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
    size = estimate["kelly_bet"] / price if price > 0 else 0

    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        order = OrderArgs(
            token_id=token_id,
            price=round(price, 3),
            size=round(size, 2),
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

    results = []
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        # Buy YES
        yes_size = bet_size / opportunity["yes_price"] if opportunity["yes_price"] > 0 else 0
        yes_order = OrderArgs(
            token_id=tokens[0],
            price=round(opportunity["yes_price"], 3),
            size=round(yes_size, 2),
            side=BUY,
        )
        yes_signed = client.create_order(yes_order)
        yes_resp = client.post_order(yes_signed, OrderType.GTC)
        results.append({"side": "YES", "resp": str(yes_resp)})

        # Buy NO
        no_size = bet_size / opportunity["no_price"] if opportunity["no_price"] > 0 else 0
        no_order = OrderArgs(
            token_id=tokens[1],
            price=round(opportunity["no_price"], 3),
            size=round(no_size, 2),
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
