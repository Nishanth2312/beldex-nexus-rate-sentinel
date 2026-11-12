import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel
from app.config import settings

logger = logging.getLogger("beldex_sentinel.market_feed")

class ExchangeTicker(BaseModel):
    exchange: str
    price_usd: float
    price_inr: float
    volume_24h: float = 0.0
    high_24h: float = 0.0
    low_24h: float = 0.0
    change_24h_pct: float = 0.0
    timestamp: float
    raw_status: str = "ok"

class MarketSnapshot(BaseModel):
    composite_price_usd: float
    composite_price_inr: float
    usd_inr_rate: float
    volume_24h_total: float
    high_24h_max: float
    low_24h_min: float
    average_change_24h_pct: float
    timestamp: float
    sources: Dict[str, ExchangeTicker]

class MarketFeedService:
    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout = timeout_seconds
        self.default_usd_inr = 93.85
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) BeldexSentinel/1.0",
            "Accept": "application/json"
        }

    async def fetch_gateio(self, client: httpx.AsyncClient) -> Optional[ExchangeTicker]:
        url = "https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BDX_USDT"
        try:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    t = data[0]
                    p_usd = float(t.get("last", 0.0))
                    vol = float(t.get("quote_volume", 0.0))
                    high = float(t.get("high_24h", 0.0))
                    low = float(t.get("low_24h", 0.0))
                    change = float(t.get("change_percentage", 0.0))
                    return ExchangeTicker(
                        exchange="gateio",
                        price_usd=p_usd,
                        price_inr=p_usd * self.default_usd_inr,
                        volume_24h=vol,
                        high_24h=high,
                        low_24h=low,
                        change_24h_pct=change,
                        timestamp=time.time(),
                        raw_status="ok"
                    )
        except Exception as e:
            logger.warning(f"Gate.io fetch failed: {e}")
        return None

    async def fetch_kucoin(self, client: httpx.AsyncClient) -> Optional[ExchangeTicker]:
        url = "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=BDX-USDT"
        try:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 200:
                body = resp.json()
                data = body.get("data", {})
                p_usd = float(data.get("price", 0.0))
                if p_usd > 0:
                    return ExchangeTicker(
                        exchange="kucoin",
                        price_usd=p_usd,
                        price_inr=p_usd * self.default_usd_inr,
                        volume_24h=0.0,
                        high_24h=0.0,
                        low_24h=0.0,
                        change_24h_pct=0.0,
                        timestamp=time.time(),
                        raw_status="ok"
                    )
        except Exception as e:
            logger.warning(f"KuCoin fetch failed: {e}")
        return None

    async def fetch_mexc(self, client: httpx.AsyncClient) -> Optional[ExchangeTicker]:
        url = "https://api.mexc.com/api/v3/ticker/price?symbol=BDXUSDT"
        try:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 200:
                data = resp.json()
                p_usd = float(data.get("price", 0.0))
                if p_usd > 0:
                    return ExchangeTicker(
                        exchange="mexc",
                        price_usd=p_usd,
                        price_inr=p_usd * self.default_usd_inr,
                        volume_24h=0.0,
                        high_24h=0.0,
                        low_24h=0.0,
                        change_24h_pct=0.0,
                        timestamp=time.time(),
                        raw_status="ok"
                    )
        except Exception as e:
            logger.warning(f"MEXC fetch failed: {e}")
        return None

    async def get_market_snapshot(self) -> Optional[MarketSnapshot]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # Tier 1 Priority: KuCoin
            if settings.enable_kucoin:
                kucoin_res = await self.fetch_kucoin(client)
                if kucoin_res and kucoin_res.price_usd > 0:
                    sources = {"kucoin": kucoin_res}
                    return MarketSnapshot(
                        composite_price_usd=round(kucoin_res.price_usd, 6),
                        composite_price_inr=round(kucoin_res.price_inr, 4),
                        usd_inr_rate=round(self.default_usd_inr, 3),
                        volume_24h_total=round(kucoin_res.volume_24h, 2),
                        high_24h_max=round(kucoin_res.high_24h or kucoin_res.price_usd, 6),
                        low_24h_min=round(kucoin_res.low_24h or kucoin_res.price_usd, 6),
                        average_change_24h_pct=round(kucoin_res.change_24h_pct, 3),
                        timestamp=time.time(),
                        sources=sources
                    )

            # Secondary Fallback: Gate.io & MEXC (only if KuCoin is unavailable)
            fallback_tasks = []
            if settings.enable_gateio:
                fallback_tasks.append(self.fetch_gateio(client))
            if settings.enable_mexc:
                fallback_tasks.append(self.fetch_mexc(client))

            results = await asyncio.gather(*fallback_tasks, return_exceptions=True)

        sources: Dict[str, ExchangeTicker] = {}
        for res in results:
            if isinstance(res, ExchangeTicker) and res.price_usd > 0:
                res.price_inr = round(res.price_usd * self.default_usd_inr, 4)
                sources[res.exchange] = res

        if not sources:
            logger.error("All market feed endpoints failed to provide BDX price.")
            return None

        # Calculate composite values across active fallback sources
        prices_usd = [s.price_usd for s in sources.values()]
        prices_inr = [s.price_inr for s in sources.values()]
        comp_usd = sum(prices_usd) / len(prices_usd)
        comp_inr = sum(prices_inr) / len(prices_inr)

        vol_total = sum(s.volume_24h for s in sources.values())
        highs = [s.high_24h for s in sources.values() if s.high_24h > 0]
        lows = [s.low_24h for s in sources.values() if s.low_24h > 0]
        changes = [s.change_24h_pct for s in sources.values() if s.change_24h_pct != 0]

        high_max = max(highs) if highs else comp_usd
        low_min = min(lows) if lows else comp_usd
        avg_change = (sum(changes) / len(changes)) if changes else 0.0

        return MarketSnapshot(
            composite_price_usd=round(comp_usd, 6),
            composite_price_inr=round(comp_inr, 4),
            usd_inr_rate=round(self.default_usd_inr, 3),
            volume_24h_total=round(vol_total, 2),
            high_24h_max=round(high_max, 6),
            low_24h_min=round(low_min, 6),
            average_change_24h_pct=round(avg_change, 3),
            timestamp=time.time(),
            sources=sources
        )

market_feed = MarketFeedService()
