"""Keep API persistence tests away from the user's local query history."""
import pytest


@pytest.fixture(autouse=True)
def isolated_history_path(tmp_path, monkeypatch):
    monkeypatch.setenv("QUERY_HISTORY_PATH", str(tmp_path / "history.sqlite3"))

    monkeypatch.setenv("CONTROL_STORE_PATH", str(tmp_path / "control.sqlite3"))
    monkeypatch.delenv("CONTROL_DATABASE_URL", raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "false")
