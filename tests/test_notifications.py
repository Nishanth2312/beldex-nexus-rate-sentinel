import pytest
from app.config import settings
from app.database import init_db
from app.services.notification import notification_service

@pytest.mark.asyncio
async def test_alert_dispatcher_format():
    await init_db()
    test_analysis = {
        "current_price_usd": 0.0750,
        "current_price_inr": 7.04,
        "price_30m_usd": 0.0700,
        "price_30m_inr": 6.57,
        "diff_30m_pct": 7.14,
        "alert_reasons": ["30-Minute Price Surge: +7.14%"],
        "holdings_bdx": 20000.0,
        "portfolio_value_usd": 1500.0,
        "portfolio_value_inr": 140800.0,
        "profit_gain_30m_usd": 100.0
    }

    # Dispatch with default mock / disabled channels
    res = await notification_service.dispatch_rate_alert(test_analysis)
    assert res is not None
    assert "status" in res
    assert "timestamp" in res

@pytest.mark.asyncio
async def test_test_alert_unknown_channel():
    res = await notification_service.send_test_alert("invalid_channel")
    assert res["status"] == "error"
