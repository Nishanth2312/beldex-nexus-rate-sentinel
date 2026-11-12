import logging
import time
from typing import Any, Dict, Optional
import httpx
from pydantic import BaseModel
from app.config import settings

logger = logging.getLogger("beldex_sentinel.nexus")

class NexusState(BaseModel):
    rate_usd: Optional[float] = None
    rate_inr: Optional[float] = None
    status: str = "awaiting_sync"  # 'synced_via_companion', 'connected_direct', 'manual_override', 'challenge_required', 'calculated_from_market'
    status_detail: str = "Nexus Selling Price active (Actual + 0.45 paise)"
    last_sync_timestamp: float = 0.0
    sync_method: str = "formula"   # 'formula', 'companion', 'session_auth', 'manual'
    premium_pct_vs_market: Optional[float] = None
    user_account_info: Optional[Dict[str, Any]] = None
    
    # Nexus Specific Selling Price Metrics (Actual Beldex Price + 0.45 paise)
    actual_beldex_price_inr: Optional[float] = None
    actual_beldex_price_usd: Optional[float] = None
    nexus_selling_premium_inr: float = 0.45
    nexus_selling_premium_paise: float = 45.0
    nexus_selling_price_inr: Optional[float] = None
    nexus_selling_price_usd: Optional[float] = None
    holdings_regular_value_inr: Optional[float] = None
    holdings_nexus_value_inr: Optional[float] = None
    holdings_extra_profit_inr: Optional[float] = None
    formula_rule: str = "Selling Price = Actual Beldex Price + 0.45 Paise (+₹0.45)"

class NexusClientService:
    def __init__(self):
        self.state = NexusState()

    def calculate_selling_price(
        self,
        actual_inr: float,
        actual_usd: float,
        holdings_bdx: float = 10000.0,
        premium_inr: Optional[float] = None
    ) -> NexusState:
        """
        Calculates Nexus Selling Price strictly adhering to rule:
        Selling Price = Actual Beldex Price + 0.45 paise (₹0.45 INR).
        """
        if premium_inr is None:
            premium_inr = settings.nexus_selling_premium_inr

        self.state.actual_beldex_price_inr = round(actual_inr, 4)
        self.state.actual_beldex_price_usd = round(actual_usd, 6)
        self.state.nexus_selling_premium_inr = round(premium_inr, 4)
        self.state.nexus_selling_premium_paise = round(premium_inr * 100.0, 1)

        # Selling price = actual beldex price + 0.45 paise
        selling_inr = round(actual_inr + premium_inr, 4)
        selling_usd = round(selling_inr / 93.85, 6)

        self.state.nexus_selling_price_inr = selling_inr
        self.state.nexus_selling_price_usd = selling_usd

        if actual_inr > 0:
            premium_pct = ((selling_inr - actual_inr) / actual_inr) * 100.0
            self.state.premium_pct_vs_market = round(premium_pct, 2)

        self.state.holdings_regular_value_inr = round(holdings_bdx * actual_inr, 2)
        self.state.holdings_nexus_value_inr = round(holdings_bdx * selling_inr, 2)
        self.state.holdings_extra_profit_inr = round(holdings_bdx * premium_inr, 2)

        if self.state.status not in ["manual_override", "synced_via_companion"]:
            self.state.rate_inr = selling_inr
            self.state.rate_usd = selling_usd
            self.state.status = "calculated_from_market"
            self.state.status_detail = f"Live Nexus Selling Price: ₹{selling_inr} (Actual ₹{actual_inr} + ₹{premium_inr})"
            self.state.last_sync_timestamp = time.time()
            self.state.sync_method = "formula"

        return self.state

    def update_from_relay(
        self,
        rate_usd: Optional[float] = None,
        rate_inr: Optional[float] = None,
        account_info: Optional[Dict[str, Any]] = None,
        source_note: str = "Browser Companion Bookmarklet"
    ) -> NexusState:
        now = time.time()
        # If one currency is provided, calculate the other using standard or known conversion
        if rate_usd and not rate_inr:
            rate_inr = round(rate_usd * 93.85, 4)
        elif rate_inr and not rate_usd:
            rate_usd = round(rate_inr / 93.85, 6)

        self.state.rate_usd = rate_usd
        self.state.rate_inr = rate_inr
        self.state.status = "synced_via_companion"
        self.state.status_detail = f"Successfully synced from {source_note} at {time.strftime('%H:%M:%S')}"
        self.state.last_sync_timestamp = now
        self.state.sync_method = "companion"
        if account_info:
            self.state.user_account_info = account_info
        return self.state

    def set_manual_rate(self, rate_usd: Optional[float] = None, rate_inr: Optional[float] = None) -> NexusState:
        now = time.time()
        if rate_usd and not rate_inr:
            rate_inr = round(rate_usd * 93.85, 4)
        elif rate_inr and not rate_usd:
            rate_usd = round(rate_inr / 93.85, 6)

        self.state.rate_usd = rate_usd
        self.state.rate_inr = rate_inr
        self.state.status = "manual_override"
        self.state.status_detail = f"Manually configured at {time.strftime('%H:%M:%S')}"
        self.state.last_sync_timestamp = now
        self.state.sync_method = "manual"
        return self.state

    async def attempt_direct_query(self) -> NexusState:
        """
        Attempts direct HTTP query to nexus.vin using configured session cookies or bearer token.
        If Cloudflare Turnstile/Challenge blocks raw HTTP, reports status cleanly.
        """
        headers = {
            "User-Agent": settings.nexus_user_agent,
            "Accept": "application/json, text/html, */*",
        }
        if settings.nexus_auth_token:
            headers["Authorization"] = f"Bearer {settings.nexus_auth_token}"
        if settings.nexus_cookie:
            headers["Cookie"] = settings.nexus_cookie

        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                resp = await client.get(f"{settings.nexus_url.rstrip('/')}/", headers=headers)
                if resp.status_code == 200:
                    self.state.status = "connected_direct"
                    self.state.status_detail = "Direct HTTP session established"
                    self.state.sync_method = "session_auth"
                elif resp.status_code == 403 or "challenge" in resp.headers.get("cf-mitigated", ""):
                    # Cloudflare challenge present - user should use companion sync
                    if self.state.status != "synced_via_companion" and self.state.status != "manual_override":
                        self.state.status = "challenge_required"
                        self.state.status_detail = (
                            "nexus.vin is protected by Cloudflare. "
                            "Use the 1-Click Browser Bookmarklet or export your session cookie."
                        )
                else:
                    self.state.status = "error"
                    self.state.status_detail = f"HTTP {resp.status_code} received from nexus.vin"
        except Exception as e:
            logger.debug(f"Direct nexus query attempt: {e}")
            if self.state.status not in ["synced_via_companion", "manual_override"]:
                self.state.status = "challenge_required"
                self.state.status_detail = "Direct connection pending. Use Browser Companion Sync."
        return self.state

    def calculate_arbitrage(self, market_price_usd: float) -> Optional[float]:
        if self.state.rate_usd and market_price_usd > 0:
            premium = ((self.state.rate_usd - market_price_usd) / market_price_usd) * 100.0
            self.state.premium_pct_vs_market = round(premium, 2)
            return self.state.premium_pct_vs_market
        return None

nexus_client = NexusClientService()
