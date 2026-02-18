"""Position Tracker — JSON-based storage for open/closed positions."""
import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("polyagent.positions")

DATA_DIR = Path(__file__).parent / "data"
POSITIONS_FILE = DATA_DIR / "positions.json"


@dataclass
class Position:
    id: str
    strategy: str                    # "funding_arb" | "spread"
    exchange: str                    # "hyperliquid" | "binance"
    symbol: str                      # "SEI/USDT:USDT"
    side: str                        # "long" | "short"
    quantity: float
    entry_price: float
    entry_time: str                  # ISO timestamp
    size_usd: float
    order_ids: list[str] = field(default_factory=list)
    status: str = "open"             # "open" | "closed"
    # Funding arb specific
    entry_rate: float | None = None  # Funding rate at entry
    direction: str | None = None     # "short_perp" | "long_perp"
    pair: str | None = None          # "SEI/USDT"
    # Spread specific
    other_exchange: str | None = None
    other_symbol: str | None = None
    other_side: str | None = None
    other_order_id: str | None = None
    # TP/SL order tracking (exchange-native orders)
    tp_order_id: str | None = None
    sl_order_id: str | None = None
    tp_price: float | None = None
    sl_price: float | None = None
    # For spread: SL on the other leg
    other_sl_order_id: str | None = None
    other_sl_price: float | None = None
    # Close info (filled on close)
    close_time: str | None = None
    close_price: float | None = None
    close_reason: str | None = None
    pnl: float | None = None


def _load() -> list[dict]:
    if not POSITIONS_FILE.exists():
        return []
    try:
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        logger.warning("Corrupted positions file, starting fresh")
        return []


def _save(positions: list[dict]):
    DATA_DIR.mkdir(exist_ok=True)
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2)


def save_position(pos: Position):
    """Add a new position to the store."""
    positions = _load()
    positions.append(asdict(pos))
    _save(positions)
    logger.info(f"Position saved: {pos.strategy} {pos.side} {pos.symbol} on {pos.exchange} (${pos.size_usd:.2f})")


def get_open_positions(strategy: str | None = None) -> list[Position]:
    """Get all open positions, optionally filtered by strategy."""
    positions = _load()
    result = []
    for p in positions:
        if p.get("status") != "open":
            continue
        if strategy and p.get("strategy") != strategy:
            continue
        result.append(Position(**p))
    return result


def close_position(pos_id: str, close_price: float, reason: str, pnl: float | None = None):
    """Mark a position as closed."""
    positions = _load()
    for p in positions:
        if p["id"] == pos_id:
            p["status"] = "closed"
            p["close_time"] = datetime.now(timezone.utc).isoformat()
            p["close_price"] = close_price
            p["close_reason"] = reason
            p["pnl"] = pnl
            break
    _save(positions)
    logger.info(f"Position closed: {pos_id} reason={reason} pnl={pnl}")


def create_funding_position(
    symbol: str,
    side: str,
    quantity: float,
    entry_price: float,
    size_usd: float,
    entry_rate: float,
    direction: str,
    pair: str,
    order_ids: list[str],
    tp_order_id: str | None = None,
    sl_order_id: str | None = None,
    tp_price: float | None = None,
    sl_price: float | None = None,
) -> Position:
    """Create and save a funding arb position."""
    pos = Position(
        id=str(uuid.uuid4())[:8],
        strategy="funding_arb",
        exchange="hyperliquid",
        symbol=symbol,
        side=side,
        quantity=quantity,
        entry_price=entry_price,
        entry_time=datetime.now(timezone.utc).isoformat(),
        size_usd=size_usd,
        order_ids=order_ids,
        entry_rate=entry_rate,
        direction=direction,
        pair=pair,
        tp_order_id=tp_order_id,
        sl_order_id=sl_order_id,
        tp_price=tp_price,
        sl_price=sl_price,
    )
    save_position(pos)
    return pos


def create_spread_position(
    buy_exchange: str,
    buy_symbol: str,
    sell_exchange: str,
    sell_symbol: str,
    quantity: float,
    buy_price: float,
    sell_price: float,
    size_usd: float,
    buy_order_id: str,
    sell_order_id: str,
    sl_order_id: str | None = None,
    sl_price: float | None = None,
    other_sl_order_id: str | None = None,
    other_sl_price: float | None = None,
) -> Position:
    """Create and save a spread arb position (tracks both legs)."""
    pos = Position(
        id=str(uuid.uuid4())[:8],
        strategy="spread",
        exchange=buy_exchange,
        symbol=buy_symbol,
        side="long",
        quantity=quantity,
        entry_price=buy_price,
        entry_time=datetime.now(timezone.utc).isoformat(),
        size_usd=size_usd,
        order_ids=[buy_order_id],
        other_exchange=sell_exchange,
        other_symbol=sell_symbol,
        other_side="short",
        other_order_id=sell_order_id,
        sl_order_id=sl_order_id,
        sl_price=sl_price,
        other_sl_order_id=other_sl_order_id,
        other_sl_price=other_sl_price,
    )
    save_position(pos)
    return pos


def position_age_hours(entry_time: str) -> float:
    """Calculate how many hours a position has been open."""
    try:
        entry = datetime.fromisoformat(entry_time)
        now = datetime.now(timezone.utc)
        return (now - entry).total_seconds() / 3600
    except Exception:
        return 0
