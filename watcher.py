"""Real-time WebSocket monitors — event-driven replacement for 5-min polling.

4 monitors running as asyncio tasks:
1. PM WebSocket: real-time price feeds for prediction positions (no auth)
2. CCXT watch_ticker: spread convergence/reversal detection
3. CCXT watch_ticker: micro-arb spot move detection on Binance
4. CCXT watch_positions: funding TP/SL execution detection on Hyperliquid
"""
import asyncio
import json
import logging
import time
from collections import deque

from config import cfg

logger = logging.getLogger("polyagent.watcher")

PM_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SPOT_WINDOW_SECONDS = 300  # 5 minutes of price history for move detection
MICRO_ARB_COOLDOWN = 300   # 5 min cooldown per asset after execution


class Watcher:
    """Event-driven position monitor for all strategies."""

    def __init__(self, bot):
        self.bot = bot
        self._running = False
        self._tasks: list[asyncio.Task] = []
        # PM state
        self._pm_prices: dict[str, float] = {}
        # Spread state: pos_id → watch task
        self._spread_watches: dict[str, asyncio.Task] = {}
        # Micro-arb state
        self._spot_prices: dict[str, deque] = {
            "BTC": deque(maxlen=600),
            "ETH": deque(maxlen=600),
            "SOL": deque(maxlen=600),
        }
        self._micro_arb_last_exec: dict[str, float] = {}  # asset → timestamp
        # Pro clients (CCXT WebSocket)
        self._pro_clients: dict[str, object] = {}

    async def start(self):
        """Launch all 4 monitors as background tasks."""
        self._running = True
        self._tasks = [
            asyncio.create_task(self._pm_watcher(), name="pm_ws"),
            asyncio.create_task(self._spread_watcher(), name="spread_ws"),
            asyncio.create_task(self._micro_arb_watcher(), name="micro_arb_ws"),
            asyncio.create_task(self._funding_watcher(), name="funding_ws"),
        ]
        logger.info("Watcher started: PM + Spread + MicroArb + Funding")

    async def stop(self):
        """Cancel all monitors."""
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._spread_watches.values():
            t.cancel()
        self._spread_watches.clear()
        # Close pro clients
        for client in self._pro_clients.values():
            try:
                await client.close()
            except Exception:
                pass
        self._pro_clients.clear()
        self._tasks.clear()
        logger.info("Watcher stopped")

    # ═══════════════════════════════════════════════════
    # 1. PM WebSocket — prediction position monitoring
    # ═══════════════════════════════════════════════════

    async def _pm_watcher(self):
        """Monitor PM prediction positions via CLOB WebSocket."""
        try:
            import websockets
        except ImportError:
            logger.warning("websockets not installed — PM real-time watcher disabled")
            return

        while self._running:
            try:
                from positions import get_open_positions
                positions = get_open_positions(strategy="prediction")
                token_ids = [p.token_id for p in positions if p.token_id]

                if not token_ids:
                    await asyncio.sleep(30)
                    continue

                async with websockets.connect(PM_WS_URL, ping_interval=None) as ws:
                    await ws.send(json.dumps({
                        "assets_ids": token_ids,
                        "type": "market",
                    }))
                    logger.info(f"PM WebSocket: connected, watching {len(token_ids)} tokens")

                    hb = asyncio.create_task(self._ws_heartbeat(ws))
                    refresh = asyncio.create_task(self._pm_refresh_subs(ws))

                    try:
                        async for msg in ws:
                            if not self._running:
                                break
                            if msg == "PONG":
                                continue
                            await self._pm_on_message(msg)
                    finally:
                        hb.cancel()
                        refresh.cancel()

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning(f"PM WebSocket error: {e}, reconnecting in 5s")
                await asyncio.sleep(5)

    async def _ws_heartbeat(self, ws):
        """Send PING every 10s to keep PM WebSocket alive."""
        while self._running:
            await asyncio.sleep(10)
            try:
                await ws.send("PING")
            except Exception:
                return

    async def _pm_refresh_subs(self, ws):
        """Every 60s, subscribe new positions and unsubscribe closed ones."""
        while self._running:
            await asyncio.sleep(60)
            try:
                from positions import get_open_positions
                positions = get_open_positions(strategy="prediction")
                current_ids = {p.token_id for p in positions if p.token_id}
                subscribed = set(self._pm_prices.keys())

                new_ids = current_ids - subscribed
                if new_ids:
                    await ws.send(json.dumps({
                        "assets_ids": list(new_ids),
                        "operation": "subscribe",
                    }))
                    logger.info(f"PM WebSocket: +{len(new_ids)} tokens")

                old_ids = subscribed - current_ids
                if old_ids:
                    await ws.send(json.dumps({
                        "assets_ids": list(old_ids),
                        "operation": "unsubscribe",
                    }))
                    for oid in old_ids:
                        self._pm_prices.pop(oid, None)
            except Exception as e:
                logger.debug(f"PM refresh error: {e}")

    async def _pm_on_message(self, raw: str):
        """Process PM WebSocket events."""
        try:
            events = json.loads(raw)
            if not isinstance(events, list):
                events = [events]

            for ev in events:
                etype = ev.get("event_type")
                asset_id = ev.get("asset_id", "")

                if etype == "book":
                    bids = ev.get("bids", [])
                    asks = ev.get("asks", [])
                    if bids and asks:
                        best_bid = float(bids[0]["price"])
                        best_ask = float(asks[0]["price"])
                        mid = (best_bid + best_ask) / 2
                        self._pm_prices[asset_id] = mid
                        await self._pm_check_trigger(asset_id, mid, best_bid, best_ask)

                elif etype == "last_trade_price":
                    price = float(ev.get("price", 0))
                    if price > 0:
                        self._pm_prices[asset_id] = price
                        await self._pm_check_trigger(asset_id, price, price, price + 0.01)

        except json.JSONDecodeError:
            pass
        except Exception as e:
            logger.debug(f"PM WS parse error: {e}")

    async def _pm_check_trigger(self, token_id: str, current_price: float, best_bid: float, best_ask: float):
        """Check if a PM price move warrants Level 2 re-evaluation."""
        from positions import get_open_positions, update_position

        positions = get_open_positions(strategy="prediction")
        pos = next((p for p in positions if p.token_id == token_id), None)
        if not pos:
            return

        ref_price = pos.last_check_price or pos.entry_price
        move_pct = abs(current_price - ref_price) / ref_price if ref_price > 0 else 0

        edge_inverted = False
        if pos.estimated_prob is not None:
            if pos.side == "YES" and current_price > pos.estimated_prob:
                edge_inverted = True
            elif pos.side == "NO" and current_price < (1 - pos.estimated_prob):
                edge_inverted = True

        if move_pct < cfg.pm_reeval_price_trigger and not edge_inverted:
            update_position(pos.id, last_check_price=current_price)
            return

        logger.info(f"PM WS: {pos.id} moved {move_pct:.1%} — triggering re-eval")

        from position_manager import _reeval_prediction
        price_data = {"mid": current_price, "best_bid": best_bid, "best_ask": best_ask}
        result = await _reeval_prediction(pos, price_data)
        if result:
            from notifier import notify_prediction_reeval
            await notify_prediction_reeval(self.bot, pos, result)

    # ═══════════════════════════════════════════════════
    # 2. Spread Watcher — CCXT watch_ticker on both legs
    # ═══════════════════════════════════════════════════

    async def _spread_watcher(self):
        """Lifecycle manager: start/stop per-position watch tasks."""
        while self._running:
            try:
                from positions import get_open_positions
                positions = get_open_positions(strategy="spread")
                active_ids = {p.id for p in positions}

                # Cancel watches for closed positions
                for pid in list(self._spread_watches):
                    if pid not in active_ids:
                        self._spread_watches[pid].cancel()
                        del self._spread_watches[pid]

                # Start watches for new positions
                for pos in positions:
                    if pos.id not in self._spread_watches:
                        task = asyncio.create_task(self._watch_one_spread(pos))
                        self._spread_watches[pos.id] = task

                await asyncio.sleep(30)

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Spread watcher manager error: {e}")
                await asyncio.sleep(10)

    async def _watch_one_spread(self, pos):
        """Watch tickers on both exchanges for a single spread position."""
        buy_client = await self._get_pro_client(pos.exchange)
        sell_client = await self._get_pro_client(pos.other_exchange)

        if not buy_client or not sell_client or not pos.other_symbol:
            return

        buy_bid = 0.0
        sell_ask = 0.0

        async def watch_buy():
            nonlocal buy_bid
            while self._running:
                ticker = await buy_client.watch_ticker(pos.symbol)
                buy_bid = ticker.get("bid", 0) or 0

        async def watch_sell():
            nonlocal sell_ask
            while self._running:
                ticker = await sell_client.watch_ticker(pos.other_symbol)
                sell_ask = ticker.get("ask", 0) or 0

        async def check_spread():
            while self._running:
                await asyncio.sleep(1)
                if buy_bid > 0 and sell_ask > 0:
                    spread = (buy_bid - sell_ask) / sell_ask * 100
                    if spread <= 0.03:
                        await self._close_spread_ws(pos, "spread_closed_ws")
                        return
                    if spread >= 0.5:
                        await self._close_spread_ws(pos, "profit_take_ws")
                        return

        try:
            await asyncio.gather(
                asyncio.create_task(watch_buy()),
                asyncio.create_task(watch_sell()),
                asyncio.create_task(check_spread()),
            )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Spread watch {pos.id} error: {e}")

    async def _close_spread_ws(self, pos, reason: str):
        """Close a spread position detected by the watcher."""
        from crypto.executor import _get_client
        from position_manager import _close_spread_position

        buy_client = await _get_client(pos.exchange)
        sell_client = await _get_client(pos.other_exchange)
        if buy_client and sell_client:
            result = await _close_spread_position(buy_client, sell_client, pos, reason)
            if result:
                from notifier import notify_position_closed
                await notify_position_closed(self.bot, pos, result)

    # ═══════════════════════════════════════════════════
    # 3. Micro-Arb Watcher — Binance spot price streaming
    # ═══════════════════════════════════════════════════

    async def _micro_arb_watcher(self):
        """Watch BTC/ETH/SOL on Binance for spot moves → trigger PM micro-arb."""
        try:
            import ccxt.pro as ccxtpro
        except ImportError:
            logger.warning("ccxt.pro not available — micro-arb watcher disabled")
            return

        client = ccxtpro.binance({"enableRateLimit": True})
        spot_pairs = {"BTC": "BTC/USDT", "ETH": "ETH/USDT", "SOL": "SOL/USDT"}

        tasks = [
            asyncio.create_task(self._watch_spot(client, asset, pair))
            for asset, pair in spot_pairs.items()
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            await client.close()

    async def _watch_spot(self, client, asset: str, pair: str):
        """Stream spot prices and detect moves."""
        while self._running:
            try:
                ticker = await client.watch_ticker(pair)
                price = ticker.get("last", 0)
                if not price:
                    continue

                now = time.time()
                self._spot_prices[asset].append((now, price))

                # Trim old entries
                cutoff = now - SPOT_WINDOW_SECONDS
                while self._spot_prices[asset] and self._spot_prices[asset][0][0] < cutoff:
                    self._spot_prices[asset].popleft()

                if len(self._spot_prices[asset]) < 2:
                    continue

                oldest_price = self._spot_prices[asset][0][1]
                move_pct = (price - oldest_price) / oldest_price * 100

                if abs(move_pct) >= 0.20:  # threshold from micro_arb config
                    # Cooldown check
                    last_exec = self._micro_arb_last_exec.get(asset, 0)
                    if now - last_exec < MICRO_ARB_COOLDOWN:
                        continue
                    await self._on_spot_move(asset, move_pct, oldest_price, price)

            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug(f"Spot watch {asset} error: {e}")
                await asyncio.sleep(5)

    async def _on_spot_move(self, asset: str, move_pct: float, old_price: float, new_price: float):
        """Spot move detected — run micro-arb scan with pre-detected move."""
        logger.info(f"Spot WS: {asset} {move_pct:+.3f}% ({old_price:.2f}→{new_price:.2f})")

        move = {
            asset: {
                "asset": asset,
                "direction": "UP" if move_pct > 0 else "DOWN",
                "move_pct": move_pct,
                "old_price": old_price,
                "new_price": new_price,
                "candles": 0,
            }
        }

        from polymarket.micro_arb import scan_micro_arb, execute_micro_arb
        from notifier import notify_micro_arb

        opportunities = await scan_micro_arb(moves=move)
        for opp in opportunities[:2]:
            result = await execute_micro_arb(opp)
            await notify_micro_arb(self.bot, opp, result)

        if opportunities:
            self._micro_arb_last_exec[asset] = time.time()

    # ═══════════════════════════════════════════════════
    # 4. Funding Watcher — HL position/rate monitoring
    # ═══════════════════════════════════════════════════

    async def _funding_watcher(self):
        """Watch Hyperliquid positions for TP/SL execution + funding rate changes."""
        if not cfg.hl_private_key:
            logger.info("Funding watcher: no HL key configured")
            return

        try:
            import ccxt.pro as ccxtpro
        except ImportError:
            logger.warning("ccxt.pro not available — funding watcher disabled")
            return

        client = ccxtpro.hyperliquid({
            "privateKey": cfg.hl_private_key,
            "walletAddress": cfg.hl_wallet_address,
            "enableRateLimit": True,
        })

        try:
            while self._running:
                try:
                    from positions import get_open_positions, close_position
                    funding_positions = get_open_positions(strategy="funding_arb")

                    if not funding_positions:
                        await asyncio.sleep(30)
                        continue

                    # Watch positions — returns when state changes
                    ws_positions = await client.watch_positions()

                    # Build set of symbols that still have open positions on exchange
                    exchange_symbols = set()
                    for p in ws_positions:
                        if abs(float(p.get("contracts", 0) or 0)) > 0:
                            exchange_symbols.add(p.get("symbol", ""))

                    # Check each tracked funding position
                    for pos in funding_positions:
                        if pos.symbol in exchange_symbols:
                            continue
                        if not (pos.tp_order_id or pos.sl_order_id):
                            continue

                        # Position gone from exchange — TP/SL was triggered
                        from crypto.executor import _get_client as get_rest_client
                        rest_client = await get_rest_client("hyperliquid")
                        if not rest_client:
                            continue

                        try:
                            ticker = await rest_client.fetch_ticker(pos.symbol)
                            price = ticker["last"]
                            if pos.side == "short":
                                pnl = (pos.entry_price - price) * pos.quantity
                            else:
                                pnl = (price - pos.entry_price) * pos.quantity

                            close_position(pos.id, price, "tp_sl_triggered_ws", pnl)
                            logger.info(f"Funding WS: {pos.id} TP/SL triggered, PnL~${pnl:.2f}")

                            from notifier import notify_position_closed
                            await notify_position_closed(self.bot, pos, {
                                "success": True,
                                "position_id": pos.id,
                                "reason": "tp_sl_triggered_ws",
                                "close_price": price,
                                "pnl": pnl,
                            })
                        except Exception as e:
                            logger.error(f"Funding WS close error {pos.id}: {e}")

                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.debug(f"Funding watch tick error: {e}")
                    await asyncio.sleep(5)
        finally:
            await client.close()

    # ═══════════════════════════════════════════════════
    # Pro Client Management (CCXT WebSocket)
    # ═══════════════════════════════════════════════════

    async def _get_pro_client(self, exchange: str):
        """Get or create a ccxt.pro client for WebSocket methods."""
        if exchange in self._pro_clients:
            return self._pro_clients[exchange]

        try:
            import ccxt.pro as ccxtpro
        except ImportError:
            return None

        client = None
        if exchange == "hyperliquid" and cfg.hl_private_key:
            client = ccxtpro.hyperliquid({
                "privateKey": cfg.hl_private_key,
                "walletAddress": cfg.hl_wallet_address,
                "enableRateLimit": True,
            })
        elif exchange == "binance" and cfg.binance_api_key:
            client = ccxtpro.binance({
                "apiKey": cfg.binance_api_key,
                "secret": cfg.binance_secret,
                "enableRateLimit": True,
            })

        if client:
            self._pro_clients[exchange] = client
        return client
