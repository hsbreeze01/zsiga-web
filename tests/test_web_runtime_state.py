"""Integration tests for zsiga-web runtime_state operations.

Tests run against the Flask app with a test client.
Covers:
  - admin page renders with correct active target
  - target_activate writes runtime_state.yaml
  - target_activate blocks when daemon running
  - target_delete updates runtime_state if active
  - evolution_schedule writes runtime_state.yaml
  - runtime_state.yaml persists across config reloads
"""

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml


@pytest.fixture
def app(tmp_path):
    """Create Flask app with temp repo."""
    cfg = {
        "agent": {
            "name": "zsiga",
            "llm": {
                "provider": "zhipuai",
                "model": "glm-5.1",
                "api_key": "test",
                "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
            },
        },
        "targets": {
            "zsiga": {
                "domain": "self",
                "path": str(tmp_path),
                "transport": "local",
                "deploy_branch": "main",
                "description": "zsiga self",
            },
            "factory": {
                "domain": "external",
                "path": "/tmp/factory",
                "transport": "ssh",
                "deploy_branch": "main",
                "description": "data factory",
                "ssh": {"host": "1.2.3.4", "user": "root", "key_path": "/tmp/key"},
            },
        },
    }
    (tmp_path / "zsiga.yaml").write_text(yaml.dump(cfg, allow_unicode=True))
    (tmp_path / "data").mkdir()

    os.environ["ZSIGA_REPO"] = str(tmp_path)
    os.environ["ZSIGA_DAEMON_URL"] = "http://localhost:58175"

    from zsiga_web import create_app
    app = create_app(repo_path=str(tmp_path), daemon_url="http://localhost:58175")
    app.config["TESTING"] = True
    yield app

    os.environ.pop("ZSIGA_REPO", None)
    os.environ.pop("ZSIGA_DAEMON_URL", None)


@pytest.fixture
def client(app):
    return app.test_client()


def _runtime_state_path(app):
    from zsiga_web import _runtime_state_path
    with app.app_context():
        return _runtime_state_path()


def _read_runtime(app):
    p = _runtime_state_path(app)
    if p.exists():
        return yaml.safe_load(p.read_text()) or {}
    return {}


class TestAdminPage:
    def test_renders_zsiga_mode_by_default(self, client, app):
        resp = client.get("/admin/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "自演进模式" in html

    def test_renders_factory_mode(self, client, app):
        p = _runtime_state_path(app)
        p.write_text(yaml.dump({"active_target": "factory"}))
        resp = client.get("/admin/")
        html = resp.data.decode()
        assert "外部项目: factory" in html

    def test_shows_both_targets(self, client, app):
        resp = client.get("/admin/")
        html = resp.data.decode()
        assert "factory" in html
        assert "zsiga" in html


class TestTargetActivate:
    @patch("zsiga_web.admin._check_daemon_state", return_value={})
    @patch("zsiga_web.admin._restart_daemon", return_value={"ok": True, "message": "OK"})
    def test_activate_factory_writes_runtime_state(self, mock_restart, mock_daemon, client, app):
        resp = client.post("/admin/target/activate/factory", follow_redirects=True)
        assert resp.status_code == 200
        rs = _read_runtime(app)
        assert rs.get("active_target") == "factory"

    @patch("zsiga_web.admin._check_daemon_state", return_value={})
    @patch("zsiga_web.admin._restart_daemon", return_value={"ok": True, "message": "OK"})
    def test_activate_zsiga_writes_runtime_state(self, mock_restart, mock_daemon, client, app):
        p = _runtime_state_path(app)
        p.write_text(yaml.dump({"active_target": "factory"}))
        resp = client.post("/admin/target/activate/zsiga", follow_redirects=True)
        assert resp.status_code == 200
        rs = _read_runtime(app)
        assert rs.get("active_target") == "zsiga"

    @patch("zsiga_web.admin._check_daemon_state", return_value={"state": "running"})
    def test_writes_pending_switch_when_daemon_running(self, mock_daemon, client, app):
        resp = client.post("/admin/target/activate/factory", follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "排队" in html or "已排队" in html
        rs = _read_runtime(app)
        assert rs.get("active_target") == "zsiga"
        assert rs.get("pending_switch") == "factory"

    @patch("zsiga_web.admin._check_daemon_state", return_value={"state": "idle"})
    @patch("zsiga_web.admin._restart_daemon", return_value={"ok": True, "message": "OK"})
    def test_pending_switch_cleared_on_direct_activate(self, mock_restart, mock_daemon, client, app):
        p = _runtime_state_path(app)
        p.write_text(yaml.dump({"active_target": "factory", "pending_switch": "compass"}))
        resp = client.post("/admin/target/activate/zsiga", follow_redirects=True)
        assert resp.status_code == 200
        rs = _read_runtime(app)
        assert rs.get("active_target") == "zsiga"
        assert "pending_switch" not in rs

    @patch("zsiga_web.admin._check_daemon_state", return_value={"state": "idle"})
    @patch("zsiga_web.admin._restart_daemon", return_value={"ok": True, "message": "OK"})
    def test_allows_switch_when_daemon_idle(self, mock_restart, mock_daemon, client, app):
        resp = client.post("/admin/target/activate/factory", follow_redirects=True)
        assert resp.status_code == 200
        rs = _read_runtime(app)
        assert rs.get("active_target") == "factory"


class TestTargetDelete:
    @patch("zsiga_web.admin._check_daemon_state", return_value={})
    @patch("zsiga_web.admin._restart_daemon", return_value={"ok": True, "message": "OK"})
    def test_delete_active_target_resets_to_zsiga(self, mock_restart, mock_daemon, client, app):
        p = _runtime_state_path(app)
        p.write_text(yaml.dump({"active_target": "factory"}))
        resp = client.post("/admin/target/delete/factory", follow_redirects=True)
        assert resp.status_code == 200
        rs = _read_runtime(app)
        assert rs.get("active_target") == "zsiga"

    def test_delete_non_active_target_keeps_state(self, client, app):
        p = _runtime_state_path(app)
        p.write_text(yaml.dump({"active_target": "zsiga"}))
        resp = client.post("/admin/target/delete/factory", follow_redirects=True)
        assert resp.status_code == 200
        rs = _read_runtime(app)
        assert rs.get("active_target") == "zsiga"

    def test_cannot_delete_zsiga(self, client, app):
        resp = client.post("/admin/target/delete/zsiga", follow_redirects=True)
        html = resp.data.decode()
        assert "不可删除" in html


class TestEvolutionSchedule:
    def test_saves_schedule_to_runtime_state(self, client, app):
        resp = client.post("/admin/evolution-schedule", data={
            "start_hour": "23",
            "end_hour": "7",
        }, follow_redirects=True)
        assert resp.status_code == 200
        rs = _read_runtime(app)
        assert rs.get("evolution_window_start_hour") == 23
        assert rs.get("evolution_window_end_hour") == 7

    def test_rejects_invalid_hour(self, client, app):
        resp = client.post("/admin/evolution-schedule", data={
            "start_hour": "25",
            "end_hour": "10",
        }, follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "格式错误" in html


class TestRuntimeStatePersistence:
    @patch("zsiga_web.admin._check_daemon_state", return_value={})
    @patch("zsiga_web.admin._restart_daemon", return_value={"ok": True, "message": "OK"})
    def test_state_survives_config_reload(self, mock_restart, mock_daemon, client, app):
        p = _runtime_state_path(app)
        p.write_text(yaml.dump({
            "active_target": "factory",
            "evolution_window_start_hour": 23,
            "evolution_window_end_hour": 7,
        }))

        resp = client.get("/admin/")
        html = resp.data.decode()
        assert "外部项目: factory" in html

        # reload page - state should persist
        resp2 = client.get("/admin/")
        html2 = resp2.data.decode()
        assert "外部项目: factory" in html2

    def test_empty_runtime_state_defaults_to_zsiga(self, client, app):
        p = _runtime_state_path(app)
        if p.exists():
            p.unlink()
        resp = client.get("/admin/")
        html = resp.data.decode()
        assert "自演进模式" in html
