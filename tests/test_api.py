import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database import init_db
from app.config import settings

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
async def test_admin_auth_workflow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Invalid login
        bad_login = await ac.post("/api/auth/login", json={"username": "wrong", "password": "bad"})
        assert bad_login.status_code == 401

        # 2. Valid login
        login_res = await ac.post("/api/auth/login", json={
            "username": settings.admin_username,
            "password": settings.admin_password
        })
        assert login_res.status_code == 200
        login_data = login_res.json()
        assert login_data["status"] == "success"
        token = login_data["token"]
        assert token is not None

        # 3. Access /api/auth/me without token -> 401
        me_unauth = await ac.get("/api/auth/me")
        assert me_unauth.status_code == 401

        # 4. Access /api/auth/me with valid token -> 200
        me_auth = await ac.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_auth.status_code == 200
        assert me_auth.json()["username"] == settings.admin_username

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
        # 1. Public read config
        res = await ac.get("/api/alerts/config")
        assert res.status_code == 200
        data = res.json()
        assert "monitoring" in data
        assert "channels" in data

        # 2. Updating config without admin auth must fail (401)
        update_payload = {
            "surge_threshold_percent_30m": 4.5,
            "user_holdings_bdx": 15000.0
        }
        unauth_res = await ac.post("/api/alerts/config", json=update_payload)
        assert unauth_res.status_code == 401

        # 3. Log in as admin
        login = await ac.post("/api/auth/login", json={
            "username": settings.admin_username,
            "password": settings.admin_password
        })
        token = login.json()["token"]

        # 4. Updating config with admin token must succeed (200)
        auth_res = await ac.post(
            "/api/alerts/config",
            json=update_payload,
            headers={"Authorization": f"Bearer {token}"}
        )
        assert auth_res.status_code == 200

        # 5. Check history
        hist_res = await ac.get("/api/alerts/history")
        assert hist_res.status_code == 200
        assert "alerts" in hist_res.json()

@pytest.mark.asyncio
async def test_admin_protected_surge_simulation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Unauthenticated surge simulation fails (401)
        sim_unauth = await ac.post("/api/rates/simulate-surge", json={"surge_percent": 12.5})
        assert sim_unauth.status_code == 401

        # 2. Authenticate
        login = await ac.post("/api/auth/login", json={
            "username": settings.admin_username,
            "password": settings.admin_password
        })
        token = login.json()["token"]

        # 3. Authenticated surge simulation succeeds (200)
        sim_auth = await ac.post(
            "/api/rates/simulate-surge",
            json={"surge_percent": 12.5, "trigger_alerts": False},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert sim_auth.status_code == 200
        assert sim_auth.json()["status"] == "simulation_complete"

@pytest.mark.asyncio
async def test_nexus_selling_price_formula():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Fetch Nexus pricing endpoint
        res = await ac.get("/api/nexus/pricing?holdings=10000")
        assert res.status_code == 200
        data = res.json()

        actual = data["actual_beldex_price_inr"]
        premium = data["nexus_selling_premium_inr"]
        selling = data["nexus_selling_price_inr"]
        extra = data["holdings_extra_profit_inr"]

        # Exact formula test: Selling Price = Actual Beldex Price + 0.45 paise (+₹0.45)
        assert premium == 0.45
        assert data["nexus_selling_premium_paise"] == 45.0
        assert selling == round(actual + 0.45, 4)
        assert extra == 4500.0  # 10,000 * 0.45 = ₹4,500.00 bonus

        # 2. Test admin-protected nexus config
        unauth_cfg = await ac.post("/api/nexus/config", json={"nexus_selling_premium_inr": 0.50})
        assert unauth_cfg.status_code == 401

        # 3. Authenticated config update
        login = await ac.post("/api/auth/login", json={
            "username": settings.admin_username,
            "password": settings.admin_password
        })
        token = login.json()["token"]

        auth_cfg = await ac.post(
            "/api/nexus/config",
            json={"nexus_selling_premium_inr": 0.45},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert auth_cfg.status_code == 200
        assert auth_cfg.json()["nexus_selling_premium_inr"] == 0.45
