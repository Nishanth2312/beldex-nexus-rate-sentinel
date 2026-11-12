import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional
from app.config import settings
from app.database import record_price_tick
from app.services.market_feed import market_feed
from app.services.nexus_client import nexus_client
from app.services.rate_analyzer import rate_analyzer
from app.services.notification import notification_service

logger = logging.getLogger("beldex_sentinel.scheduler")

class SentinelScheduler:
    def __init__(self):
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        self.last_check_time: float = 0.0
        self.total_checks: int = 0
        self.consecutive_errors: int = 0
        self.broadcast_callbacks: List[Callable[[Dict[str, Any]], Any]] = []

    def register_broadcast_callback(self, callback: Callable[[Dict[str, Any]], Any]):
        if callback not in self.broadcast_callbacks:
            self.broadcast_callbacks.append(callback)

    def unregister_broadcast_callback(self, callback: Callable[[Dict[str, Any]], Any]):
        if callback in self.broadcast_callbacks:
            self.broadcast_callbacks.remove(callback)

    async def _broadcast(self, payload: Dict[str, Any]):
        for cb in list(self.broadcast_callbacks):
            try:
                res = cb(payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.debug(f"Broadcast callback error: {e}")

    async def run_single_cycle(self) -> Optional[Dict[str, Any]]:
        self.last_check_time = time.time()
        self.total_checks += 1
        
        # 1. Fetch live market snapshot
        snapshot = await market_feed.get_market_snapshot()
        if not snapshot:
            self.consecutive_errors += 1
            logger.warning(f"Could not retrieve market price. (Error streak: {self.consecutive_errors})")
            return None

        self.consecutive_errors = 0
        
        # 2. Persist composite tick to database
        await record_price_tick(
            source="composite",
            price_usd=snapshot.composite_price_usd,
            price_inr=snapshot.composite_price_inr,
            volume_24h=snapshot.volume_24h_total,
            high_24h=snapshot.high_24h_max,
            low_24h=snapshot.low_24h_min,
            change_24h_pct=snapshot.average_change_24h_pct,
            timestamp=snapshot.timestamp
        )

        # 3. Persist individual exchange ticks
        for src_name, ticker in snapshot.sources.items():
            await record_price_tick(
                source=src_name,
                price_usd=ticker.price_usd,
                price_inr=ticker.price_inr,
                volume_24h=ticker.volume_24h,
                high_24h=ticker.high_24h,
                low_24h=ticker.low_24h,
                change_24h_pct=ticker.change_24h_pct,
                timestamp=ticker.timestamp
            )

        # 4. Process Nexus.vin
        nexus_client.calculate_arbitrage(snapshot.composite_price_usd)
        if nexus_client.state.rate_usd and nexus_client.state.rate_usd > 0:
            await record_price_tick(
                source="nexus_vin",
                price_usd=nexus_client.state.rate_usd,
                price_inr=nexus_client.state.rate_inr or (nexus_client.state.rate_usd * snapshot.usd_inr_rate),
                timestamp=time.time()
            )

        # 5. Analyze 30-Minute Rate Difference and Demand
        analysis = await rate_analyzer.analyze_current_rate()
        
        # 6. Check if Alert should trigger
        alert_result = None
        if analysis and analysis.should_trigger_alert:
            logger.info(f"Triggering Rate Alert! Reasons: {analysis.alert_reasons}")
            alert_result = await notification_service.dispatch_rate_alert(analysis.model_dump())

        # 7. Construct cycle payload
        cycle_payload = {
            "type": "tick_update",
            "timestamp": self.last_check_time,
            "market_snapshot": snapshot.model_dump(),
            "analysis": analysis.model_dump() if analysis else None,
            "nexus_state": nexus_client.state.model_dump(),
            "alert_result": alert_result
        }

        # 8. Broadcast to WebSocket listeners (dashboard UI)
        await self._broadcast(cycle_payload)
        return cycle_payload

    async def _loop(self):
        logger.info(f"Sentinel background monitoring scheduler started. Interval: {settings.poll_interval_seconds}s")
        while self.is_running:
            try:
                await self.run_single_cycle()
            except Exception as e:
                logger.error(f"Error in scheduler cycle: {e}", exc_info=True)
            
            await asyncio.sleep(settings.poll_interval_seconds)

    def start(self):
        if not self.is_running:
            self.is_running = True
            self._task = asyncio.create_task(self._loop())

    def stop(self):
        if self.is_running:
            self.is_running = False
            if self._task:
                self._task.cancel()
                self._task = None
            logger.info("Sentinel background monitoring scheduler stopped.")

scheduler = SentinelScheduler()
