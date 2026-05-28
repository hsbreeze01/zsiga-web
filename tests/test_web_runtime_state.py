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
import subprocess
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


class TestDaemonRestart:
    @patch("zsiga_web.time.sleep", return_value=None)
    @patch("zsiga_web.os.kill")
    @patch("zsiga_web.subprocess.run")
    def test_restart_stops_manual_lock_pid_before_systemd_start(
        self, mock_run, mock_kill, mock_sleep, app
    ):
        from zsiga_web import _restart_daemon

        _runtime_state_path(app).parent.mkdir(parents=True, exist_ok=True)
        (_runtime_state_path(app).parent / "lock.pid").write_text("123\n")
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        mock_kill.side_effect = [
            None,               # os.kill(pid, 0): running
            None,               # os.kill(pid, SIGTERM): terminate
            ProcessLookupError, # os.kill(pid, 0): stopped
        ]

        with app.app_context():
            result = _restart_daemon()

        assert result["ok"] is True
        systemctl_commands = [
            call.args[0][3]
            for call in mock_run.call_args_list
            if call.args[0][:3] == ["sudo", "-n", "systemctl"]
        ]
        assert systemctl_commands == ["stop", "reset-failed", "start"]
        assert mock_kill.call_args_list[1].args == (123, 15)

    @patch("zsiga_web.time.sleep", return_value=None)
    @patch("zsiga_web.os.kill")
    @patch("zsiga_web.subprocess.run")
    def test_restart_stops_port_listener_when_lock_pid_missing(
        self, mock_run, mock_kill, mock_sleep, app
    ):
        from zsiga_web import _restart_daemon

        mock_run.side_effect = [
            MagicMock(returncode=0, stderr="", stdout=""),
            MagicMock(returncode=0, stderr="", stdout="456\n"),
            MagicMock(returncode=0, stderr="", stdout=""),
            MagicMock(returncode=0, stderr="", stdout=""),
        ]
        mock_kill.side_effect = [
            None,               # os.kill(pid, 0): running
            None,               # os.kill(pid, SIGTERM): terminate
            ProcessLookupError, # os.kill(pid, 0): stopped
        ]

        with app.app_context():
            result = _restart_daemon()

        assert result["ok"] is True
        assert mock_run.call_args_list[1].args[0] == [
            "lsof", "-nP", "-tiTCP:58175", "-sTCP:LISTEN",
        ]
        assert mock_kill.call_args_list[1].args == (456, 15)

    @patch("zsiga_web.time.sleep", return_value=None)
    @patch("zsiga_web.os.kill")
    @patch("zsiga_web.subprocess.run")
    def test_restart_continues_when_systemctl_stop_times_out(
        self, mock_run, mock_kill, mock_sleep, app
    ):
        from zsiga_web import _restart_daemon

        mock_run.side_effect = [
            subprocess.TimeoutExpired(["sudo", "-n", "systemctl", "stop", "zsiga-daemon"], 5),
            MagicMock(returncode=0, stderr="", stdout="456\n"),
            MagicMock(returncode=0, stderr="", stdout=""),
            MagicMock(returncode=0, stderr="", stdout=""),
        ]
        mock_kill.side_effect = [
            None,
            None,
            ProcessLookupError,
        ]

        with app.app_context():
            result = _restart_daemon()

        assert result["ok"] is True
        assert mock_kill.call_args_list[1].args == (456, 15)

    @patch("zsiga_web.os.kill")
    @patch("zsiga_web.subprocess.run")
    def test_restart_reports_timeout_when_lock_pid_stays_running(
        self, mock_run, mock_kill, app
    ):
        from zsiga_web import _restart_daemon

        _runtime_state_path(app).parent.mkdir(parents=True, exist_ok=True)
        (_runtime_state_path(app).parent / "lock.pid").write_text("123\n")
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        mock_kill.return_value = None

        with patch("zsiga_web.time.time", side_effect=[0, 21, 21, 27]):
            with app.app_context():
                result = _restart_daemon()

        assert result["ok"] is False
        assert "未在超时内退出" in result["message"]
        assert mock_kill.call_args_list[2].args == (123, 9)
