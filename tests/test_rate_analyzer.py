import time
import pytest
from app.database import init_db, record_price_tick
from app.services.rate_analyzer import rate_analyzer

@pytest.mark.asyncio
async def test_30m_rate_difference_calculation():
    await init_db()
    now = time.time()

    # Record historical tick 30 minutes ago (1800s): $0.0700
    await record_price_tick(
        source="test_source",
        price_usd=0.0700,
        price_inr=6.57,
        timestamp=now - 1800.0
    )

    # Record current tick: $0.0742 (+6% increase)
    await record_price_tick(
        source="test_source",
        price_usd=0.0742,
        price_inr=6.96,
        timestamp=now
    )

    analysis = await rate_analyzer.analyze_current_rate(
        source="test_source",
        surge_threshold_pct=3.0,
        holdings_bdx=10000.0
    )

    assert analysis is not None
    assert analysis.current_price_usd == 0.0742
    assert analysis.price_30m_usd == 0.0700
    assert analysis.diff_30m_pct is not None
    assert round(analysis.diff_30m_pct, 1) == 6.0
    assert analysis.is_high_demand_surge is True
    assert "30-Minute Price Surge" in analysis.alert_reasons[0]
    assert analysis.demand_level in ["HIGH DEMAND", "EXTREME SURGE"]
    assert "SELL" in analysis.recommendation

    # Portfolio checks
    assert analysis.portfolio_value_usd == round(0.0742 * 10000.0, 2)
    assert analysis.profit_gain_30m_usd == round((0.0742 - 0.0700) * 10000.0, 2)

@pytest.mark.asyncio
async def test_target_sell_price_trigger():
    await init_db()
    now = time.time()

    await record_price_tick(
        source="target_test",
        price_usd=0.0820,
        price_inr=7.70,
        timestamp=now
    )

    analysis = await rate_analyzer.analyze_current_rate(
        source="target_test",
        target_sell_usd=0.0800
    )

    assert analysis is not None
    assert analysis.is_target_sell_reached is True
    assert "Target Sell Price Hit" in " ".join(analysis.alert_reasons)
    assert "TARGET SELL PRICE REACHED" in analysis.recommendation
