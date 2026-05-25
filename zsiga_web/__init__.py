"""zsiga-web: Independent management console for zsiga autonomous engineer."""
import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path

import yaml
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

ZSIGA_REPO = os.environ.get("ZSIGA_REPO", "/home/zsiga/repo")


def create_app(repo_path: str = None):
    app = Flask(__name__)
    app.secret_key = "zsiga-web-console"
    app.config["ZSIGA_REPO"] = repo_path or ZSIGA_REPO

    @app.template_filter("status_class")
    def status_class(status: str) -> str:
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
        return {"repo_path": app.config["ZSIGA_REPO"]}

    from .admin import admin_bp
    from .proposals import proposals_bp
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(proposals_bp, url_prefix="/proposals")

    @app.route("/")
    def index():
        cfg = _load_config(app)
        daemon_status = _get_daemon_status(app)
        pipeline = _get_pipeline_status(app)
        proposal_stats = _get_proposal_stats(app)
        return render_template("index.html",
                               config=cfg, daemon=daemon_status,
                               pipeline=pipeline, stats=proposal_stats)

    @app.route("/api/config")
    def api_config():
        return jsonify(_load_config(app))

    @app.route("/api/daemon-status")
    def api_daemon_status():
        return jsonify(_get_daemon_status(app))

    @app.route("/api/proposals")
    def api_proposals():
        return jsonify(_get_proposals(app))

    @app.route("/api/proposal-stats")
    def api_proposal_stats():
        return jsonify(_get_proposal_stats(app))

    @app.route("/api/pipeline-status")
    def api_pipeline_status():
        return jsonify(_get_pipeline_status(app))

    return app


def _repo(bp_or_app) -> Path:
    if hasattr(bp_or_app, "app"):
        app = bp_or_app.app
    elif hasattr(bp_or_app, "config"):
        app = bp_or_app
    else:
        app = bp_or_app
    return Path(app.config["ZSIGA_REPO"])


def _load_config(app) -> dict:
    cfg_path = _repo(app) / "zsiga.yaml"
    if not cfg_path.exists():
        return {}
    with open(cfg_path) as f:
        return yaml.safe_load(f) or {}


def _save_config(app, config: dict):
    cfg_path = _repo(app) / "zsiga.yaml"
    with open(cfg_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _get_db(app) -> sqlite3.Connection:
    db_path = _repo(app) / "data" / "zsiga.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_daemon_status(app) -> dict:
    state_path = _repo(app) / "data" / "daemon_state.json"
    if state_path.exists():
        try:
            return json.loads(state_path.read_text())
        except Exception:
            pass
    return {"state": "unknown"}


def _get_pipeline_status(app) -> dict:
    daemon_port = 58175
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "3", f"http://localhost:{daemon_port}/api/pipeline-status"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception:
        pass
    return {}


def _get_proposal_stats(app) -> dict:
    daemon_port = 58175
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "3", f"http://localhost:{daemon_port}/api/proposal-stats"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except Exception:
        pass
    return {}


def _get_proposals(app) -> list[dict]:
    changes_dir = _repo(app) / "openspec" / "changes"
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


def _read_phase_state(change_dir: Path) -> dict:
    ps = change_dir / ".phase_state"
    if ps.exists():
        try:
            return json.loads(ps.read_text())
        except Exception:
            pass
    return {}


def _validate_llm_key(api_key: str, base_url: str, model: str) -> dict:
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


def _validate_project_path(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"ok": False, "message": f"目录不存在: {path}"}
    if not (p / ".git").exists():
        return {"ok": False, "message": f"不是 Git 仓库: {path}"}
    return {"ok": True, "message": "路径有效"}


def _validate_git_remote(repo_path: str) -> dict:
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


def _validate_github_token(token: str) -> dict:
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


def _precheck_proposal(content: str) -> list[dict]:
    issues = []
    if len(content) < 100:
        issues.append({"level": "error", "message": "Proposal 内容太短（少于 100 字符），请补充更多细节"})
    for name, pattern, msg in PROPOSAL_CHECKS:
        if not re.search(pattern, content, re.IGNORECASE):
            issues.append({"level": "warning", "message": msg})
    if not issues:
        issues.append({"level": "ok", "message": "预检查通过"})
    return issues


def _write_proposal(app, name: str, content: str) -> Path:
    changes_dir = _repo(app) / "openspec" / "changes" / name
    changes_dir.mkdir(parents=True, exist_ok=True)
    proposal_path = changes_dir / "proposal.md"
    proposal_path.write_text(content)
    return proposal_path
