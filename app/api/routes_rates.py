import time
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from app.config import settings
from app.database import get_price_history, record_price_tick
from app.services.auth import require_admin
from app.services.market_feed import market_feed
from app.services.rate_analyzer import rate_analyzer
from app.services.scheduler import scheduler
from app.services.notification import notification_service

router = APIRouter(prefix="/api/rates", tags=["Rates"])

class SimulateSurgeRequest(BaseModel):
    surge_percent: float = 7.5
    custom_price_usd: Optional[float] = None
    trigger_alerts: bool = True

@router.get("/current")
async def get_current_rates():
    """
    Returns latest price snapshot, 30-minute rate difference, and market breakdown.
    """
    analysis = await rate_analyzer.analyze_current_rate()
    if not analysis:
        # Fallback to immediate fetch if database has no ticks yet
        cycle = await scheduler.run_single_cycle()
        if cycle and cycle.get("analysis"):
            return cycle["analysis"]
        raise HTTPException(status_code=503, detail="Market feed currently initializing, please retry in a few seconds.")
    return analysis.model_dump()

@router.get("/diff-30m")
async def get_30m_difference():
    """
    Dedicated endpoint for 30-minute rate difference, demand level, and sell signal.
    """
    analysis = await rate_analyzer.analyze_current_rate()
    if not analysis:
        cycle = await scheduler.run_single_cycle()
        if cycle and cycle.get("analysis"):
            analysis_dict = cycle["analysis"]
        else:
            raise HTTPException(status_code=503, detail="Rate analysis unavailable")
    else:
        analysis_dict = analysis.model_dump()

    return {
        "timestamp": analysis_dict["timestamp"],
        "current_price_usd": analysis_dict["current_price_usd"],
        "current_price_inr": analysis_dict["current_price_inr"],
        "price_30m_ago_usd": analysis_dict["price_30m_usd"],
        "price_30m_ago_inr": analysis_dict["price_30m_inr"],
        "diff_30m_usd": analysis_dict["diff_30m_usd"],
        "diff_30m_inr": analysis_dict["diff_30m_inr"],
        "diff_30m_pct": analysis_dict["diff_30m_pct"],
        "demand_level": analysis_dict["demand_level"],
        "recommendation": analysis_dict["recommendation"],
        "is_high_demand_surge": analysis_dict["is_high_demand_surge"],
        "portfolio_value_usd": analysis_dict["portfolio_value_usd"],
        "portfolio_value_inr": analysis_dict["portfolio_value_inr"],
        "profit_gain_30m_usd": analysis_dict["profit_gain_30m_usd"],
        "profit_gain_30m_inr": analysis_dict["profit_gain_30m_inr"]
    }

@router.get("/history")
async def get_rates_history(
    hours: float = Query(6.0, ge=0.5, le=168.0),
    source: Optional[str] = Query("composite")
):
    """
    Retrieves time-series price data for charts.
    """
    since_ts = time.time() - (hours * 3600.0)
    ticks = await get_price_history(source=source, since_timestamp=since_ts, limit=500)
    return {
        "hours": hours,
        "source": source,
        "count": len(ticks),
        "ticks": ticks
    }

@router.post("/scan-now")
async def trigger_immediate_scan():
    """
    Forces an immediate live scan across all exchanges and nexus.vin.
    """
    res = await scheduler.run_single_cycle()
    if not res:
        raise HTTPException(status_code=502, detail="Scan failed to retrieve live data.")
    return {"status": "success", "result": res}

@router.post("/simulate-surge")
async def simulate_price_surge(
    req: SimulateSurgeRequest,
    admin: str = Depends(require_admin)
):
    """
    Simulates a high price surge (e.g. +7.5% over 30 mins) to test notification dispatch. (ADMIN ONLY)
    """
    # Get current price
    latest_analysis = await rate_analyzer.analyze_current_rate()
    base_usd = latest_analysis.current_price_usd if latest_analysis else 0.0710
    base_inr = latest_analysis.current_price_inr if latest_analysis else 6.66

    if req.custom_price_usd and req.custom_price_usd > 0:
        new_usd = req.custom_price_usd
        diff_pct = ((new_usd - base_usd) / base_usd) * 100.0
    else:
        diff_pct = req.surge_percent
        new_usd = base_usd * (1.0 + (diff_pct / 100.0))

    new_inr = round(new_usd * 93.85, 4)
    now = time.time()

    # Record historical tick 30 mins ago if not present
    await record_price_tick(
        source="composite",
        price_usd=base_usd,
        price_inr=base_inr,
        timestamp=now - 1800.0
    )

    # Record simulated new high price
    await record_price_tick(
        source="composite",
        price_usd=round(new_usd, 6),
        price_inr=new_inr,
        timestamp=now
    )

    # Perform analysis
    analysis = await rate_analyzer.analyze_current_rate(
        override_usd=new_usd,
        override_inr=new_inr,
        source=None
    )

    alert_dispatch = None
    if req.trigger_alerts and analysis:
        alert_dispatch = await notification_service.dispatch_rate_alert(analysis.model_dump())

    return {
        "status": "simulation_complete",
        "simulated_price_usd": round(new_usd, 6),
        "simulated_price_inr": new_inr,
        "simulated_diff_30m_pct": round(diff_pct, 2),
        "analysis": analysis.model_dump() if analysis else None,
        "alert_dispatch": alert_dispatch
    }
