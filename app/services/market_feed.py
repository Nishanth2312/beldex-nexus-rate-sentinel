import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx
from pydantic import BaseModel
from app.config import settings

logger = logging.getLogger("beldex_sentinel.market_feed")

MIN_LIQUID_VOLUME_USD = 1000.0  # Ignore illiquid outlier orderbooks (e.g. $54 24h volume)


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
    indian_exchange_inr: Optional[float] = None
    volume_24h_total: float
    high_24h_max: float
    low_24h_min: float
    average_change_24h_pct: float
    timestamp: float
    sources: Dict[str, ExchangeTicker]


class MarketFeedService:
    def __init__(self, timeout_seconds: float = 8.0):
        self.timeout = timeout_seconds
        self.default_usd_inr = 92.90
        self.latest_indian_exchange_inr: Optional[float] = None
        self._last_fx_fetch = 0.0
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Sentrix/1.0",
            "Accept": "application/json"
        }

    async def refresh_usd_inr_rate(self, client: httpx.AsyncClient, force: bool = False) -> float:
        """Fetch live USD -> INR forex rate (cached 5 minutes)."""
        now = time.time()
        if not force and (now - self._last_fx_fetch) < 300.0 and self.default_usd_inr > 50.0:
            return self.default_usd_inr

        try:
            resp = await client.get("https://open.er-api.com/v6/latest/USD", headers=self.headers)
            if resp.status_code == 200:
                inr = float(resp.json().get("rates", {}).get("INR", 0.0))
                if 60.0 < inr < 150.0:
                    self.default_usd_inr = round(inr, 4)
                    self._last_fx_fetch = now
                    return self.default_usd_inr
        except Exception as e:
            logger.debug(f"OpenER FX fetch skipped: {e}")

        try:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=usd,inr",
                headers=self.headers
            )
            if resp.status_code == 200:
                t_inr = float(resp.json().get("tether", {}).get("inr", 0.0))
                if 60.0 < t_inr < 150.0:
                    self.default_usd_inr = round(t_inr, 4)
                    self._last_fx_fetch = now
                    return self.default_usd_inr
        except Exception as e:
            logger.debug(f"CoinGecko FX fetch skipped: {e}")

        return self.default_usd_inr

    async def fetch_kucoin(self, client: httpx.AsyncClient, usd_inr: float) -> Optional[ExchangeTicker]:
        url = "https://api.kucoin.com/api/v1/market/stats?symbol=BDX-USDT"
        try:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                p_usd = float(data.get("last") or data.get("buy") or 0.0)
                if p_usd > 0:
                    return ExchangeTicker(
                        exchange="kucoin",
                        price_usd=p_usd,
                        price_inr=round(p_usd * usd_inr, 4),
                        volume_24h=float(data.get("volValue") or 0.0),
                        high_24h=float(data.get("high") or 0.0),
                        low_24h=float(data.get("low") or 0.0),
                        change_24h_pct=round(float(data.get("changeRate") or 0.0) * 100.0, 2),
                        timestamp=time.time(),
                        raw_status="ok"
                    )
        except Exception as e:
            logger.warning(f"KuCoin fetch failed: {e}")
        return None

    async def fetch_coingecko(self, client: httpx.AsyncClient, usd_inr: float) -> Optional[ExchangeTicker]:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=beldex&vs_currencies=usd,inr&include_24hr_change=true&include_24hr_vol=true"
        try:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 200:
                b = resp.json().get("beldex", {})
                p_usd = float(b.get("usd") or 0.0)
                p_inr = float(b.get("inr") or 0.0)
                if p_usd > 0 and p_inr <= 0:
                    p_inr = round(p_usd * usd_inr, 4)
                if p_usd > 0:
                    return ExchangeTicker(
                        exchange="coingecko",
                        price_usd=p_usd,
                        price_inr=round(p_inr, 4),
                        volume_24h=float(b.get("usd_24h_vol") or 0.0),
                        change_24h_pct=round(float(b.get("inr_24h_change") or b.get("usd_24h_change") or 0.0), 2),
                        timestamp=time.time(),
                        raw_status="ok"
                    )
        except Exception as e:
            logger.warning(f"CoinGecko fetch failed: {e}")
        return None

    async def fetch_coindcx(self, client: httpx.AsyncClient, usd_inr: float) -> Tuple[Optional[ExchangeTicker], Optional[ExchangeTicker]]:
        """Fetch Indian Exchange direct BDXINR and BDXUSDT tickers from CoinDCX."""
        url = "https://api.coindcx.com/exchange/ticker"
        inr_ticker = None
        usdt_ticker = None
        try:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 200:
                now = time.time()
                for item in resp.json():
                    mkt = item.get("market")
                    if mkt == "BDXINR":
                        last_inr = float(item.get("last_price") or 0.0)
                        if last_inr > 0:
                            inr_ticker = ExchangeTicker(
                                exchange="coindcx_inr",
                                price_usd=round(last_inr / usd_inr, 6),
                                price_inr=round(last_inr, 4),
                                volume_24h=float(item.get("volume") or 0.0) * last_inr / usd_inr,
                                high_24h=float(item.get("high") or 0.0),
                                low_24h=float(item.get("low") or 0.0),
                                change_24h_pct=round(float(item.get("change_24_hour") or 0.0), 2),
                                timestamp=now,
                                raw_status="ok"
                            )
                    elif mkt == "BDXUSDT":
                        last_usd = float(item.get("last_price") or 0.0)
                        if last_usd > 0:
                            usdt_ticker = ExchangeTicker(
                                exchange="coindcx_usdt",
                                price_usd=last_usd,
                                price_inr=round(last_usd * usd_inr, 4),
                                volume_24h=float(item.get("volume") or 0.0),
                                high_24h=float(item.get("high") or 0.0),
                                low_24h=float(item.get("low") or 0.0),
                                change_24h_pct=round(float(item.get("change_24_hour") or 0.0), 2),
                                timestamp=now,
                                raw_status="ok"
                            )
        except Exception as e:
            logger.debug(f"CoinDCX fetch failed: {e}")
        return inr_ticker, usdt_ticker

    async def fetch_gateio(self, client: httpx.AsyncClient, usd_inr: float) -> Optional[ExchangeTicker]:
        url = "https://api.gateio.ws/api/v4/spot/tickers?currency_pair=BDX_USDT"
        try:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    t = data[0]
                    p_usd = float(t.get("last", 0.0))
                    vol = float(t.get("quote_volume", 0.0))
                    if p_usd > 0 and vol >= MIN_LIQUID_VOLUME_USD:
                        return ExchangeTicker(
                            exchange="gateio",
                            price_usd=p_usd,
                            price_inr=round(p_usd * usd_inr, 4),
                            volume_24h=vol,
                            high_24h=float(t.get("high_24h", 0.0)),
                            low_24h=float(t.get("low_24h", 0.0)),
                            change_24h_pct=float(t.get("change_percentage", 0.0)),
                            timestamp=time.time(),
                            raw_status="ok"
                        )
        except Exception as e:
            logger.warning(f"Gate.io fetch failed: {e}")
        return None

    async def fetch_mexc(self, client: httpx.AsyncClient, usd_inr: float) -> Optional[ExchangeTicker]:
        url = "https://api.mexc.com/api/v3/ticker/24hr?symbol=BDXUSDT"
        try:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 200:
                data = resp.json()
                p_usd = float(data.get("lastPrice", 0.0))
                vol = float(data.get("quoteVolume", 0.0))
                if p_usd > 0 and vol >= MIN_LIQUID_VOLUME_USD:
                    return ExchangeTicker(
                        exchange="mexc",
                        price_usd=p_usd,
                        price_inr=round(p_usd * usd_inr, 4),
                        volume_24h=vol,
                        high_24h=float(data.get("highPrice", 0.0)),
                        low_24h=float(data.get("lowPrice", 0.0)),
                        change_24h_pct=float(data.get("priceChangePercent", 0.0)),
                        timestamp=time.time(),
                        raw_status="ok"
                    )
        except Exception as e:
            logger.warning(f"MEXC fetch failed: {e}")
        return None

    async def fetch_historical_24h(self) -> List[Dict[str, float]]:
        """Fetch real 5-minute 24h market chart history in INR & USD from CoinGecko."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                usd_inr = await self.refresh_usd_inr_rate(client)
                resp = await client.get(
                    "https://api.coingecko.com/api/v3/coins/beldex/market_chart?vs_currency=inr&days=1",
                    headers=self.headers
                )
                if resp.status_code == 200:
                    prices = resp.json().get("prices", [])
                    ticks = []
                    for ts_ms, price_inr in prices:
                        if price_inr and price_inr > 0:
                            ticks.append({
                                "timestamp": float(ts_ms) / 1000.0,
                                "price_inr": round(float(price_inr), 4),
                                "price_usd": round(float(price_inr) / usd_inr, 6)
                            })
                    return ticks
        except Exception as e:
            logger.warning(f"Historical market chart fetch failed: {e}")
        return []

    async def get_market_snapshot(self) -> Optional[MarketSnapshot]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            usd_inr = await self.refresh_usd_inr_rate(client)

            tasks = []
            if settings.enable_kucoin:
                tasks.append(self.fetch_kucoin(client, usd_inr))
            tasks.append(self.fetch_coingecko(client, usd_inr))
            tasks.append(self.fetch_coindcx(client, usd_inr))
            if settings.enable_gateio:
                tasks.append(self.fetch_gateio(client, usd_inr))
            if settings.enable_mexc:
                tasks.append(self.fetch_mexc(client, usd_inr))

            results = await asyncio.gather(*tasks, return_exceptions=True)

        sources: Dict[str, ExchangeTicker] = {}
        indian_inr: Optional[float] = None

        for res in results:
            if isinstance(res, tuple):
                inr_t, usdt_t = res
                if inr_t:
                    sources[inr_t.exchange] = inr_t
                    indian_inr = inr_t.price_inr
                if usdt_t:
                    sources[usdt_t.exchange] = usdt_t
            elif isinstance(res, ExchangeTicker) and res.price_usd > 0:
                sources[res.exchange] = res

        # Tier-1 Global Market Selection: Prefer KuCoin ($3M+ vol), CoinDCX USDT, CoinGecko
        primary_sources = [
            sources[k] for k in ("kucoin", "coingecko", "coindcx_usdt", "mexc", "gateio")
            if k in sources and sources[k].price_usd > 0
        ]

        if not primary_sources:
            logger.error("All market feed endpoints failed to provide BDX price.")
            return None

        # Use KuCoin as primary live price when healthy; fallback to CoinGecko / CoinDCX
        if "kucoin" in sources:
            comp_usd = sources["kucoin"].price_usd
            comp_inr = round(comp_usd * usd_inr, 4)
        elif "coingecko" in sources:
            comp_usd = sources["coingecko"].price_usd
            comp_inr = sources["coingecko"].price_inr
        else:
            comp_usd = sum(s.price_usd for s in primary_sources) / len(primary_sources)
            comp_inr = round(comp_usd * usd_inr, 4)

        vol_total = sum(s.volume_24h for s in primary_sources if s.exchange != "coingecko")
        highs = [s.high_24h for s in primary_sources if s.high_24h > 0]
        lows = [s.low_24h for s in primary_sources if s.low_24h > 0]
        changes = [s.change_24h_pct for s in primary_sources if s.change_24h_pct != 0]

        high_max = max(highs) if highs else comp_usd
        low_min = min(lows) if lows else comp_usd
        avg_change = (sum(changes) / len(changes)) if changes else 0.0
        if indian_inr is not None:
            self.latest_indian_exchange_inr = indian_inr

        return MarketSnapshot(
            composite_price_usd=round(comp_usd, 6),
            composite_price_inr=round(comp_inr, 4),
            usd_inr_rate=round(usd_inr, 4),
            indian_exchange_inr=indian_inr,
            volume_24h_total=round(vol_total, 2),
            high_24h_max=round(high_max, 6),
            low_24h_min=round(low_min, 6),
            average_change_24h_pct=round(avg_change, 2),
            timestamp=time.time(),
            sources=sources
        )


market_feed = MarketFeedService()
