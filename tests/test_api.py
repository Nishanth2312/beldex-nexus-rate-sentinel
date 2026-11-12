import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import init_db

@pytest.fixture(autouse=True)
async def setup_database():
    await init_db()

@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"

@pytest.mark.asyncio
async def test_nexus_status_and_sync():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Check initial status
        res = await ac.get("/api/nexus/status")
        assert res.status_code == 200
        assert "state" in res.json()

        # 2. Sync from companion
        sync_payload = {
            "rate_usd": 0.0765,
            "rate_inr": 7.18,
            "note": "Pytest Test Suite"
        }
        sync_res = await ac.post("/api/nexus/sync", json=sync_payload)
        assert sync_res.status_code == 200
        sync_data = sync_res.json()
        assert sync_data["status"] == "success"
        assert sync_data["state"]["rate_usd"] == 0.0765
        assert sync_data["state"]["status"] == "synced_via_companion"

@pytest.mark.asyncio
async def test_rate_diff_30m_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/rates/diff-30m")
        assert res.status_code == 200
        data = res.json()
        assert "current_price_usd" in data
        assert "recommendation" in data

@pytest.mark.asyncio
async def test_alerts_config_and_history():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Get config
        res = await ac.get("/api/alerts/config")
        assert res.status_code == 200
        data = res.json()
        assert "monitoring" in data
        assert "channels" in data

        # Update config
        update_payload = {
            "surge_threshold_percent_30m": 4.5,
            "user_holdings_bdx": 15000.0
        }
        upd_res = await ac.post("/api/alerts/config", json=update_payload)
        assert upd_res.status_code == 200

        # Check history
        hist_res = await ac.get("/api/alerts/history")
        assert hist_res.status_code == 200
        assert "alerts" in hist_res.json()
