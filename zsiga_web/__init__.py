import subprocess
import os
import json
import re
import sqlite3
from pathlib import Path
import yaml
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

ZSIGA_REPO = os.environ.get("ZSIGA_REPO", "/home/zsiga/repo")
DAEMON_URL = os.environ.get("ZSIGA_DAEMON_URL", "http://localhost:58175")


def create_app(repo_path=None, daemon_url=None):
    app = Flask(__name__)
    app.secret_key = "zsiga-web-console"
    app.config["ZSIGA_REPO"] = repo_path or ZSIGA_REPO
    app.config["DAEMON_URL"] = daemon_url or DAEMON_URL

    @app.template_filter("status_class")
    def status_class(status):
        mapping = {
            "healthy": "good", "ready": "good", "running": "good", "active": "good",
            "success": "good", "PASS": "good", "working": "good",
            "warning": "warn", "tight": "warn", "RUNNING": "warn",
            "error": "bad", "fail": "bad", "FAIL": "bad", "unhealthy": "bad",
            "over budget": "bad",
            "idle": "muted", "pending": "muted", "PENDING": "muted",
            "skipped": "muted", "reverted": "bad",
        }
        return mapping.get(status, "muted")

    @app.context_processor
    def inject_globals():
        return {"repo_path": app.config["ZSIGA_REPO"], "daemon": _get_daemon_status()}

    from .admin import admin_bp
    from .proposals import proposals_bp
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(proposals_bp, url_prefix="/proposals")

    @app.route("/")
    def index():
        cfg = _load_config()
        daemon_status = _get_daemon_status()
        pipeline = _get_pipeline_status()
        proposal_stats = _get_proposal_stats()
        return render_template("index.html",
                               config=cfg, daemon=daemon_status,
                               pipeline=pipeline, stats=proposal_stats)

    @app.route("/api/config")
    def api_config():
        return jsonify(_load_config())

    @app.route("/api/daemon-status")
    def api_daemon_status():
        return jsonify(_get_daemon_status())

    @app.route("/api/proposals")
    def api_proposals():
        return jsonify(_get_proposals())

    @app.route("/api/proposal-stats")
    def api_proposal_stats():
        return jsonify(_get_proposal_stats())

    @app.route("/api/pipeline-status")
    def api_pipeline_status():
        return jsonify(_get_pipeline_status())

    return app


def _get_repo():
    from flask import current_app
    return Path(current_app.config["ZSIGA_REPO"])


def _load_config():
    cfg_path = _get_repo() / "zsiga.yaml"
    if not cfg_path.exists():
        return {}
    with open(cfg_path) as f:
        return yaml.safe_load(f) or {}


def _save_config(config):
    cfg_path = _get_repo() / "zsiga.yaml"
    with open(cfg_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _ensure_self_target(config):
    if "targets" not in config:
        config["targets"] = {}
    if "zsiga" not in config["targets"]:
        config["targets"]["zsiga"] = {
            "domain": "self",
            "path": str(_get_repo()),
            "transport": "local",
            "deploy_branch": "zsiga-l5-autonomous-engineer",
            "description": "zsiga 自主工程智能体自身演进",
        }
    if "active_target" not in config:
        config["active_target"] = "zsiga"
    return config


def _runtime_state_path():
    return _get_repo() / "data" / "runtime_state.yaml"


def _load_runtime_state():
    p = _runtime_state_path()
    if p.exists():
        try:
            with open(p) as f:
                return yaml.safe_load(f) or {}
        except Exception:
            pass
    return {"active_target": "zsiga"}


def _save_runtime_state(state):
    p = _runtime_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        yaml.dump(state, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _get_active_target(config):
    """Read active_target from runtime_state.yaml. All targets preserved in config."""
    rs = _load_runtime_state()
    name = rs.get("active_target", "zsiga")
    target_cfg = config.get("targets", {}).get(name, {})
    return name, target_cfg


def _check_daemon_state():
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "3", f"{DAEMON_URL}/api/daemon-state"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception:
        pass
    return {}


def _restart_daemon():
    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", "zsiga-daemon"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return {"ok": True, "message": "Daemon 重启成功"}
        return {"ok": False, "message": f"重启失败: {result.stderr[:200]}"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:200]}


def _validate_ssh_connection(host, user, key_path):
    try:
        cmd = ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
               "-i", key_path, f"{user}@{host}", "echo OK"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if "OK" in result.stdout:
            return {"ok": True, "message": f"SSH 连接成功: {user}@{host}"}
        return {"ok": False, "message": f"连接失败: {result.stderr[:200]}"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:200]}


def _get_db():
    db_path = _get_repo() / "data" / "zsiga.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_daemon_status():
    state_path = _get_repo() / "data" / "daemon_state.json"
    if state_path.exists():
        try:
            return json.loads(state_path.read_text())
        except Exception:
            pass
    return {"state": "unknown"}


def _get_pipeline_status():
    from flask import current_app
    daemon_url = current_app.config.get("DAEMON_URL", DAEMON_URL)
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "3", f"{daemon_url}/api/pipeline-status"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception:
        pass
    return {}


def _get_proposal_stats():
    from flask import current_app
    daemon_url = current_app.config.get("DAEMON_URL", DAEMON_URL)
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "3", f"{daemon_url}/api/proposal-stats"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception:
        pass
    return {}


def _get_proposals():
    changes_dir = _get_repo() / "openspec" / "changes"
    proposals = []
    if not changes_dir.exists():
        return proposals
    for d in sorted(changes_dir.iterdir()):
        if d.is_dir() and d.name != "archive" and not d.name.startswith("."):
            proposal_path = d / "proposal.md"
            proposals.append({
                "name": d.name,
                "has_proposal": proposal_path.exists(),
                "proposal_content": proposal_path.read_text()[:2000] if proposal_path.exists() else "",
                "phase_state": _read_phase_state(d),
                "has_steward_review": (d / "steward-review.md").exists(),
            })
    return proposals


def _read_phase_state(change_dir):
    ps = change_dir / ".phase_state"
    if ps.exists():
        try:
            return json.loads(ps.read_text())
        except Exception:
            pass
    return {}


def _validate_llm_key(api_key, base_url, model):
    try:
        import urllib.request
        url = f"{base_url}/chat/completions"
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        })
        resp = urllib.request.urlopen(req, timeout=10)
        if resp.status == 200:
            return {"ok": True, "message": "连接成功"}
        return {"ok": False, "message": f"HTTP {resp.status}"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:200]}


def _validate_project_path(path):
    p = Path(path)
    if not p.exists():
        return {"ok": False, "message": f"目录不存在: {path}"}
    if not (p / ".git").exists():
        return {"ok": False, "message": f"不是 Git 仓库: {path}"}
    return {"ok": True, "message": "路径有效"}


def _validate_git_remote(repo_path):
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            capture_output=True, text=True, timeout=5,
            cwd=repo_path,
        )
        if result.returncode == 0 and "origin" in result.stdout:
            return {"ok": True, "message": result.stdout.split("\n")[0]}
        return {"ok": False, "message": "未配置 origin remote"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:200]}


def _validate_github_token(token):
    if not token:
        return {"ok": False, "message": "Token 为空"}
    try:
        import urllib.request
        req = urllib.request.Request("https://api.github.com/user", headers={
            "Authorization": f"token {token}",
        })
        resp = urllib.request.urlopen(req, timeout=10)
        if resp.status == 200:
            data = json.loads(resp.read())
            return {"ok": True, "message": f"已认证: {data.get('login', '?')}"}
        return {"ok": False, "message": f"HTTP {resp.status}"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:200]}


PROPOSAL_CHECKS = [
    ("summary", r"##\s*Summary", "缺少 Summary 段落"),
    ("problem", r"##\s*(Problem|问题|背景)", "缺少 Problem/问题/背景 段落"),
    ("acceptance", r"##\s*(Acceptance|验收|验收标准)", "缺少验收标准段落"),
    ("scope", r"##\s*(Scope|范围)", "缺少 Scope/范围 段落（应明确 in/out scope）"),
]


def _precheck_proposal(content):
    issues = []
    if len(content) < 100:
        issues.append({"level": "error", "message": "Proposal 内容太短（少于 100 字符），请补充更多细节"})
    for name, pattern, msg in PROPOSAL_CHECKS:
        if not re.search(pattern, content, re.IGNORECASE):
            issues.append({"level": "warning", "message": msg})
    if not issues:
        issues.append({"level": "ok", "message": "预检查通过"})
    return issues


def _write_proposal(name, content):
    changes_dir = _get_repo() / "openspec" / "changes" / name
    changes_dir.mkdir(parents=True, exist_ok=True)
    proposal_path = changes_dir / "proposal.md"
    proposal_path.write_text(content)
    return proposal_path
