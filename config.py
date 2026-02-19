"""PolyAgent v2 — Unified Configuration"""
import os
from dataclasses import dataclass, field

@dataclass
class Config:
    # ── API Keys ──
    anthropic_key: str = ""
    tavily_key: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # ── Polymarket ──
    poly_private_key: str = ""
    poly_funder_address: str = ""
    poly_bankroll: float = 20.0
    poly_clob_url: str = "https://clob.polymarket.com"  # Override with CF Worker URL to bypass geo-block
    poly_proxy_url: str = ""  # Residential proxy (e.g. http://user:pass@gate.dataimpulse.com:823)

    # ── Hyperliquid ──
    hl_private_key: str = ""          # ETH wallet private key
    hl_wallet_address: str = ""       # ETH wallet address (public)
    hl_bankroll: float = 20.0

    # ── Binance ──
    binance_api_key: str = ""
    binance_secret: str = ""
    binance_bankroll: float = 20.0

    # ── Strategy: Polymarket Predictions (Simons: small edge × many bets × low correlation) ──
    pm_scan_interval_hours: int = 2
    pm_min_volume: float = 10_000
    pm_min_liquidity: float = 5_000
    pm_max_spread: float = 0.05
    pm_min_edge: float = 0.03         # 3% edge (Simons: small edge is enough with volume)
    pm_kelly_fraction: float = 0.15   # 15% Kelly (conservative)
    pm_max_bet: float = 2.0           # $2 max per bet (more granular)
    pm_max_markets_per_scan: int = 20 # Analyze up to 20 markets per scan
    pm_max_category_exposure: float = 0.30  # Max 30% of bankroll in one category

    # ── Strategy: PM Position Monitor (2-level re-evaluation) ──
    pm_reeval_price_trigger: float = 0.05   # 5% price move → trigger re-eval (Level 2)
    pm_reeval_min_edge: float = 0.01        # <1% edge after re-eval → auto-sell

    # ── Strategy: Polymarket YES+NO Arbitrage ──
    pm_arb_min_profit: float = 0.005  # Min $0.005 per $1 profit after fees
    pm_arb_fee: float = 0.02          # 2% Polymarket fee on winning side
    pm_arb_max_bet: float = 10.0      # Max $10 per arb

    # ── Strategy: Funding Rate Arbitrage ──
    fr_min_rate: float = 0.005        # 0.005% min funding rate per period
    fr_min_annualized: float = 5.0    # 5% min annualized return
    fr_max_position: float = 10.0     # Max $10 per side
    fr_scan_interval_min: int = 30    # Check every 30 min

    # ── Strategy: Cross-Exchange Spreads ──
    spread_min_pct: float = 0.10      # 0.10% min spread
    spread_max_position: float = 10.0 # Max $10 per side
    spread_exchanges: list = field(default_factory=lambda: ["binance", "bybit", "okx"])
    spread_pairs: list = field(default_factory=lambda: [
        # Large caps (tight spreads, high volume)
        "BTC/USDT", "ETH/USDT", "SOL/USDT",
        # Mid caps (wider spreads possible)
        "ARB/USDT", "DOGE/USDT", "AVAX/USDT", "LINK/USDT",
        "SUI/USDT", "INJ/USDT", "SEI/USDT", "TIA/USDT",
        # Smaller/newer (most likely to have spreads)
        "WIF/USDT", "ONDO/USDT", "PENDLE/USDT", "PYTH/USDT",
        "JUP/USDT", "W/USDT", "STRK/USDT", "MANTA/USDT",
        "DYM/USDT", "PIXEL/USDT", "PORTAL/USDT", "AEVO/USDT",
        "ENA/USDT", "ETHFI/USDT", "BOME/USDT", "MEW/USDT",
    ])
    spread_scan_interval_min: int = 5

    # ── Position Management ──
    pos_check_interval_min: int = 5        # Check positions every 5 min
    pos_funding_timeout_hours: float = 24  # Close funding arb after 24h
    pos_spread_timeout_hours: float = 1    # Close spread after 1h
    pos_stop_loss_pct: float = 0.05        # 5% stop loss (legacy, used by polling fallback)
    pos_funding_tp_pct: float = 0.03       # 3% take-profit for funding arb (exchange-native)
    pos_funding_sl_pct: float = 0.05       # 5% stop-loss for funding arb (exchange-native)

    # ── Research ──
    research_max_sources: int = 5
    research_model: str = "claude-sonnet-4-20250514"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            anthropic_key=os.getenv("ANTHROPIC_API_KEY", ""),
            tavily_key=os.getenv("TAVILY_API_KEY", ""),
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            poly_private_key=os.getenv("POLYMARKET_PRIVATE_KEY", ""),
            poly_funder_address=os.getenv("POLYMARKET_FUNDER_ADDRESS", ""),
            poly_bankroll=float(os.getenv("POLY_BANKROLL", "20")),
            poly_clob_url=os.getenv("POLY_CLOB_PROXY_URL", "https://clob.polymarket.com"),
            poly_proxy_url=os.getenv("POLY_PROXY_URL", ""),
            hl_private_key=os.getenv("HYPERLIQUID_PRIVATE_KEY", ""),
            hl_wallet_address=os.getenv("HYPERLIQUID_WALLET_ADDRESS", ""),
            hl_bankroll=float(os.getenv("HL_BANKROLL", "20")),
            binance_api_key=os.getenv("BINANCE_API_KEY", ""),
            binance_secret=os.getenv("BINANCE_SECRET", ""),
            binance_bankroll=float(os.getenv("BINANCE_BANKROLL", "20")),
        )

cfg = Config.from_env()
