from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.config import settings
from app.database import record_price_tick
from app.services.auth import require_admin
from app.services.nexus_client import nexus_client
from app.services.rate_analyzer import rate_analyzer

router = APIRouter(prefix="/api/nexus", tags=["Nexus.vin"])

class NexusSyncPayload(BaseModel):
    rate_usd: Optional[float] = None
    rate_inr: Optional[float] = None
    account_info: Optional[Dict[str, Any]] = None
    note: Optional[str] = "Browser Companion Sync"

class NexusManualRatePayload(BaseModel):
    rate_usd: Optional[float] = None
    rate_inr: Optional[float] = None

class NexusConfigPayload(BaseModel):
    nexus_selling_premium_inr: Optional[float] = None
    nexus_url: Optional[str] = None
    nexus_cookie: Optional[str] = None
    nexus_auth_token: Optional[str] = None

class NexusCredentialsPayload(BaseModel):
    nexus_url: Optional[str] = None
    nexus_cookie: Optional[str] = None
    nexus_auth_token: Optional[str] = None
    nexus_user_agent: Optional[str] = None

@router.get("/status")
async def get_nexus_status():
    """
    Returns current sync status, latest rate on nexus.vin, and arbitrage comparison vs market.
    """
    latest_market = await rate_analyzer.analyze_current_rate()
    if latest_market:
        nexus_client.calculate_selling_price(
            actual_inr=latest_market.current_price_inr,
            actual_usd=latest_market.current_price_usd,
            holdings_bdx=settings.user_holdings_bdx,
            premium_inr=settings.nexus_selling_premium_inr
        )
        nexus_client.calculate_arbitrage(latest_market.current_price_usd)

    return {
        "state": nexus_client.state.model_dump(),
        "nexus_url": settings.nexus_url,
        "nexus_selling_premium_inr": settings.nexus_selling_premium_inr,
        "formula": f"Selling Price = Actual Beldex Price + {settings.nexus_selling_premium_inr} INR ({round(settings.nexus_selling_premium_inr * 100, 1)} paise)",
        "is_cookie_configured": bool(settings.nexus_cookie),
        "is_token_configured": bool(settings.nexus_auth_token)
    }

@router.get("/pricing")
async def get_nexus_pricing(holdings: Optional[float] = None, premium: Optional[float] = None):
    """
    Dedicated Nexus Menu pricing calculator:
    Selling Price = Actual Beldex Price + 0.45 paise (+₹0.45 INR).
    """
    latest_market = await rate_analyzer.analyze_current_rate()
    actual_inr = latest_market.current_price_inr if latest_market else 6.66
    actual_usd = latest_market.current_price_usd if latest_market else 0.0710
    holdings_qty = holdings if (holdings and holdings > 0) else settings.user_holdings_bdx
    prem_inr = premium if premium is not None else settings.nexus_selling_premium_inr

    state = nexus_client.calculate_selling_price(
        actual_inr=actual_inr,
        actual_usd=actual_usd,
        holdings_bdx=holdings_qty,
        premium_inr=prem_inr
    )
    return {
        "formula": f"Selling Price = Actual Beldex Price + {prem_inr} INR ({round(prem_inr * 100, 1)} paise)",
        "actual_beldex_price_inr": state.actual_beldex_price_inr,
        "actual_beldex_price_usd": state.actual_beldex_price_usd,
        "nexus_selling_premium_inr": state.nexus_selling_premium_inr,
        "nexus_selling_premium_paise": state.nexus_selling_premium_paise,
        "nexus_selling_price_inr": state.nexus_selling_price_inr,
        "nexus_selling_price_usd": state.nexus_selling_price_usd,
        "premium_percent": state.premium_pct_vs_market,
        "holdings_bdx": holdings_qty,
        "holdings_regular_value_inr": state.holdings_regular_value_inr,
        "holdings_nexus_value_inr": state.holdings_nexus_value_inr,
        "holdings_extra_profit_inr": state.holdings_extra_profit_inr
    }

@router.post("/config")
async def update_nexus_config(payload: NexusConfigPayload, admin: str = Depends(require_admin)):
    """
    Update Nexus settings and premium offset (Admin Only).
    """
    if payload.nexus_selling_premium_inr is not None:
        settings.nexus_selling_premium_inr = payload.nexus_selling_premium_inr
    if payload.nexus_url is not None:
        settings.nexus_url = payload.nexus_url
    if payload.nexus_cookie is not None:
        settings.nexus_cookie = payload.nexus_cookie
    if payload.nexus_auth_token is not None:
        settings.nexus_auth_token = payload.nexus_auth_token

    return {
        "status": "success",
        "message": "Nexus configuration updated successfully",
        "nexus_selling_premium_inr": settings.nexus_selling_premium_inr,
        "formula": f"Selling Price = Actual Beldex Price + {settings.nexus_selling_premium_inr} INR"
    }

@router.post("/sync")
async def sync_from_companion(payload: NexusSyncPayload):
    """
    Relay endpoint for the Nexus Browser Companion Bookmarklet / Extension.
    Directly updates the Sentinel with live authenticated rate from nexus.vin.
    """
    if not payload.rate_usd and not payload.rate_inr:
        raise HTTPException(status_code=400, detail="Must provide at least rate_usd or rate_inr")

    state = nexus_client.update_from_relay(
        rate_usd=payload.rate_usd,
        rate_inr=payload.rate_inr,
        account_info=payload.account_info,
        source_note=payload.note or "Browser Companion Sync"
    )

    # Persist as a nexus_vin price tick
    if state.rate_usd:
        await record_price_tick(
            source="nexus_vin",
            price_usd=state.rate_usd,
            price_inr=state.rate_inr or (state.rate_usd * 93.85)
        )

    return {
        "status": "success",
        "message": "Nexus rate successfully synchronized",
        "state": state.model_dump()
    }

@router.post("/manual-rate")
async def set_manual_rate(
    payload: NexusManualRatePayload,
    admin: str = Depends(require_admin)
):
    """
    Set or override nexus.vin rate manually. (ADMIN ONLY)
    """
    if not payload.rate_usd and not payload.rate_inr:
        raise HTTPException(status_code=400, detail="Must provide rate_usd or rate_inr")

    state = nexus_client.set_manual_rate(
        rate_usd=payload.rate_usd,
        rate_inr=payload.rate_inr
    )
    if state.rate_usd:
        await record_price_tick(
            source="nexus_vin",
            price_usd=state.rate_usd,
            price_inr=state.rate_inr or (state.rate_usd * 93.85)
        )
    return {"status": "success", "state": state.model_dump()}

@router.post("/test-direct")
async def test_direct_nexus_connection():
    """
    Tests direct HTTP connectivity to nexus.vin using saved session credentials.
    """
    state = await nexus_client.attempt_direct_query()
    return {"status": "completed", "state": state.model_dump()}

@router.get("/bookmarklet-code")
async def get_bookmarklet_code():
    """
    Returns the one-click JavaScript bookmarklet that users drag to their bookmarks.
    When clicked on nexus.vin, it grabs live rate elements and transmits them to this app!
    """
    port = settings.port
    script = rf"""javascript:(function(){{
        try {{
            var text = document.body.innerText;
            var bdxMatch = text.match(/(?:BDX|Beldex)[^0-9]*([0-9]+(?:\.[0-9]+)?)/i);
            var inrMatch = text.match(/(?:₹|INR|Rs)[^0-9]*([0-9]+(?:\.[0-9]+)?)/i);
            var usdMatch = text.match(/(?:\$|USDT|USD)[^0-9]*([0-9]+(?:\.[0-9]+)?)/i);
            var rate_usd = usdMatch ? parseFloat(usdMatch[1]) : null;
            var rate_inr = inrMatch ? parseFloat(inrMatch[1]) : null;
            if(!rate_usd && !rate_inr && bdxMatch) {{
                rate_usd = parseFloat(bdxMatch[1]);
            }}
            if(!rate_usd && !rate_inr) {{
                var promptRate = prompt("Enter current Beldex rate seen on Nexus:", "0.0710");
                if(promptRate) rate_usd = parseFloat(promptRate);
            }}
            fetch('http://localhost:{port}/api/nexus/sync', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{
                    rate_usd: rate_usd,
                    rate_inr: rate_inr,
                    note: 'Nexus Browser Tab (' + window.location.host + ')'
                }})
            }}).then(r=>r.json()).then(data=>{{
                alert('✅ Beldex Rate successfully synced to Sentinel Dashboard!\\nRate: ' + (rate_usd ? '$'+rate_usd : '₹'+rate_inr));
            }}).catch(err=>{{
                alert('❌ Could not connect to Beldex Sentinel (http://localhost:{port}).\\nMake sure the app is running!');
            }});
        }} catch(e) {{
            alert('Error running bookmarklet: ' + e.message);
        }}
    }})();"""
    return {"bookmarklet": script.replace("\n", "").replace("    ", " ")}
