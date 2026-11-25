import pytest
import app.config
import app.database


@pytest.fixture(autouse=True)
def isolated_test_db(tmp_path, monkeypatch):
    """Ensure automated tests never pollute production data/sentinel.db."""
    test_db = tmp_path / "test_sentinel.db"
    monkeypatch.setattr(app.config, "DB_PATH", test_db)
    monkeypatch.setattr(app.database, "DB_PATH", test_db)
