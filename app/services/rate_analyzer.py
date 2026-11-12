import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel
from app.config import settings
from app.database import get_latest_tick, get_price_at_or_before, get_price_history, get_last_alert_time

logger = logging.getLogger("beldex_sentinel.analyzer")

class WindowDiff(BaseModel):
    window_label: str
    minutes: int
    past_price_usd: Optional[float] = None
    past_price_inr: Optional[float] = None
    diff_usd: Optional[float] = None
    diff_inr: Optional[float] = None
    diff_pct: Optional[float] = None

class RateAnalysis(BaseModel):
    current_price_usd: float
    current_price_inr: float
    timestamp: float
    source: str
    
    # 30-Minute Primary Metrics
    price_30m_usd: Optional[float] = None
    price_30m_inr: Optional[float] = None
    diff_30m_usd: Optional[float] = None
    diff_30m_inr: Optional[float] = None
    diff_30m_pct: Optional[float] = None
    
    # Supplementary Windows
    diff_5m: Optional[WindowDiff] = None
    diff_15m: Optional[WindowDiff] = None
    diff_1h: Optional[WindowDiff] = None
    diff_24h: Optional[WindowDiff] = None
    
    # High Demand & Selling Intelligence
    is_high_demand_surge: bool = False
    is_target_sell_reached: bool = False
    should_trigger_alert: bool = False
    alert_reasons: List[str] = []
    demand_level: str = "NORMAL"  # "EXTREME SURGE", "HIGH DEMAND", "NORMAL", "DECLINING"
    recommendation: str = "MONITOR"  # "STRONG SELL OPPORTUNITY", "TARGET REACHED", "HOLD"
    
    # Portfolio Valuation
    holdings_bdx: float = 10000.0
    portfolio_value_usd: float = 0.0
    portfolio_value_inr: float = 0.0
    profit_gain_30m_usd: Optional[float] = None
    profit_gain_30m_inr: Optional[float] = None

class RateAnalyzerService:
    def __init__(self):
        pass

    async def calculate_window_diff(
        self,
        current_usd: float,
        current_inr: float,
        now: float,
        minutes: int,
        label: str,
        source: Optional[str] = None
    ) -> WindowDiff:
        target_ts = now - (minutes * 60)
        past = await get_price_at_or_before(target_ts, source=source, max_lookback_seconds=minutes * 120)
        if not past or past["price_usd"] <= 0:
            return WindowDiff(window_label=label, minutes=minutes)

        past_usd = past["price_usd"]
        past_inr = past["price_inr"]
        diff_usd = current_usd - past_usd
        diff_inr = current_inr - past_inr
        diff_pct = ((current_usd - past_usd) / past_usd) * 100.0

        return WindowDiff(
            window_label=label,
            minutes=minutes,
            past_price_usd=round(past_usd, 6),
            past_price_inr=round(past_inr, 4),
            diff_usd=round(diff_usd, 6),
            diff_inr=round(diff_inr, 4),
            diff_pct=round(diff_pct, 2)
        )

    async def analyze_current_rate(
        self,
        override_usd: Optional[float] = None,
        override_inr: Optional[float] = None,
        source: Optional[str] = None,
        surge_threshold_pct: Optional[float] = None,
        target_sell_usd: Optional[float] = None,
        target_sell_inr: Optional[float] = None,
        holdings_bdx: Optional[float] = None
    ) -> Optional[RateAnalysis]:
        now = time.time()
        
        # Determine latest price
        if override_usd is not None and override_usd > 0:
            cur_usd = override_usd
            cur_inr = override_inr if (override_inr and override_inr > 0) else round(override_usd * 93.85, 4)
            active_src = source or "override"
        else:
            latest = await get_latest_tick(source=source)
            if not latest:
                return None
            cur_usd = latest["price_usd"]
            cur_inr = latest["price_inr"]
            active_src = latest["source"]

        # Thresholds
        threshold_pct = surge_threshold_pct if surge_threshold_pct is not None else settings.surge_threshold_percent_30m
        t_sell_usd = target_sell_usd if target_sell_usd is not None else settings.target_sell_price_usd
        t_sell_inr = target_sell_inr if target_sell_inr is not None else settings.target_sell_price_inr
        bdx_held = holdings_bdx if holdings_bdx is not None else settings.user_holdings_bdx

        # Calculate Windows
        diff_5m = await self.calculate_window_diff(cur_usd, cur_inr, now, 5, "5m", source=source)
        diff_15m = await self.calculate_window_diff(cur_usd, cur_inr, now, 15, "15m", source=source)
        diff_30m = await self.calculate_window_diff(cur_usd, cur_inr, now, 30, "30m", source=source)
        diff_1h = await self.calculate_window_diff(cur_usd, cur_inr, now, 60, "1h", source=source)
        diff_24h = await self.calculate_window_diff(cur_usd, cur_inr, now, 1440, "24h", source=source)

        # High Demand & Surge Detection
        is_surge = False
        is_target_reached = False
        reasons: List[str] = []
        demand_level = "NORMAL"
        recommendation = "HOLD / MONITOR"

        # Check 30m Surge
        if diff_30m.diff_pct is not None:
            if diff_30m.diff_pct >= threshold_pct:
                is_surge = True
                reasons.append(f"30-Minute Price Surge: +{diff_30m.diff_pct:.2f}% (Threshold: {threshold_pct:.1f}%)")
                if diff_30m.diff_pct >= threshold_pct * 2.0:
                    demand_level = "EXTREME SURGE"
                    recommendation = "STRONG SELL OPPORTUNITY (Huge Spike)"
                else:
                    demand_level = "HIGH DEMAND"
                    recommendation = "CONSIDER SELLING (High 30m Demand)"
            elif diff_30m.diff_pct <= -threshold_pct:
                demand_level = "DECLINING"
                recommendation = "PRICE DUMP DETECTED"

        # Check Target Sell Prices
        if t_sell_usd > 0 and cur_usd >= t_sell_usd:
            is_target_reached = True
            reasons.append(f"Target Sell Price Hit: ${cur_usd:.4f} >= ${t_sell_usd:.4f}")
            recommendation = "TARGET SELL PRICE REACHED - READY TO SELL"

        if t_sell_inr > 0 and cur_inr >= t_sell_inr:
            is_target_reached = True
            reasons.append(f"Target INR Price Hit: ₹{cur_inr:.2f} >= ₹{t_sell_inr:.2f}")
            recommendation = "TARGET SELL PRICE REACHED - READY TO SELL"

        # Determine Alert Cooldown
        should_alert = False
        if is_surge or is_target_reached:
            last_alert = await get_last_alert_time()
            elapsed_minutes = (now - last_alert) / 60.0
            if elapsed_minutes >= settings.cooldown_minutes:
                should_alert = True
            else:
                logger.info(f"Alert condition met but cooldown active ({elapsed_minutes:.1f}/{settings.cooldown_minutes} mins)")

        # Portfolio metrics
        port_usd = cur_usd * bdx_held
        port_inr = cur_inr * bdx_held
        profit_usd = (diff_30m.diff_usd * bdx_held) if diff_30m.diff_usd is not None else None
        profit_inr = (diff_30m.diff_inr * bdx_held) if diff_30m.diff_inr is not None else None

        return RateAnalysis(
            current_price_usd=cur_usd,
            current_price_inr=cur_inr,
            timestamp=now,
            source=active_src,
            price_30m_usd=diff_30m.past_price_usd,
            price_30m_inr=diff_30m.past_price_inr,
            diff_30m_usd=diff_30m.diff_usd,
            diff_30m_inr=diff_30m.diff_inr,
            diff_30m_pct=diff_30m.diff_pct,
            diff_5m=diff_5m,
            diff_15m=diff_15m,
            diff_1h=diff_1h,
            diff_24h=diff_24h,
            is_high_demand_surge=is_surge,
            is_target_sell_reached=is_target_reached,
            should_trigger_alert=should_alert,
            alert_reasons=reasons,
            demand_level=demand_level,
            recommendation=recommendation,
            holdings_bdx=bdx_held,
            portfolio_value_usd=round(port_usd, 2),
            portfolio_value_inr=round(port_inr, 2),
            profit_gain_30m_usd=round(profit_usd, 2) if profit_usd is not None else None,
            profit_gain_30m_inr=round(profit_inr, 2) if profit_inr is not None else None
        )

rate_analyzer = RateAnalyzerService()
