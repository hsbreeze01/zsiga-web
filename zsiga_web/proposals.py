import re
from flask import Blueprint, render_template, request, redirect, url_for, flash

from . import (
    _load_config, _get_proposals, _get_pipeline_status,
    _precheck_proposal, _write_proposal,
)

proposals_bp = Blueprint("proposals", __name__, template_folder="../templates")


@proposals_bp.route("/")
def index():
    proposals = _get_proposals(proposals_bp)
    pipeline = _get_pipeline_status(proposals_bp)
    active = pipeline.get("active_proposal")
    current_phase = pipeline.get("current_phase")
    return render_template("proposals.html",
                           proposals=proposals,
                           active_proposal=active,
                           current_phase=current_phase)


@proposals_bp.route("/submit", methods=["GET", "POST"])
def submit():
    if request.method == "GET":
        return render_template("proposal_submit.html", precheck=None, content="", name="")

    name = request.form.get("name", "").strip()
    content = request.form.get("content", "").strip()

    if not name or not content:
        flash("名称和内容不能为空", "error")
        return render_template("proposal_submit.html", precheck=None, content=content, name=name)

    name = re.sub(r"[^a-z0-9_-]", "-", name.lower())

    precheck = _precheck_proposal(content)

    action = request.form.get("action", "check")
    if action == "check":
        return render_template("proposal_submit.html", precheck=precheck, content=content, name=name)

    has_error = any(p["level"] == "error" for p in precheck)
    if has_error:
        flash("预检查未通过，请修改后重新提交", "error")
        return render_template("proposal_submit.html", precheck=precheck, content=content, name=name)

    target = request.form.get("target", "").strip()
    config = _load_config(proposals_bp)
    targets = config.get("targets", {})
    if target and target not in targets:
        flash(f"目标项目 {target} 不存在于配置中", "error")
        return render_template("proposal_submit.html", precheck=precheck, content=content, name=name)

    if target:
        content = f"# {name}\n\n## Target\n{target}\n\n{content}"

    _write_proposal(proposals_bp, name, content)
    flash(f"Proposal '{name}' 已投递到队列", "success")
    return redirect(url_for("proposals.index"))


@proposals_bp.route("/precheck", methods=["POST"])
def precheck():
    data = request.get_json() or request.form
    content = data.get("content", "")
    return {"issues": _precheck_proposal(content)}


@proposals_bp.route("/remove/<name>", methods=["POST"])
def remove(name):
    from pathlib import Path
    change_dir = _repo(proposals_bp) / "openspec" / "changes" / name
    if change_dir.exists():
        import shutil
        shutil.rmtree(change_dir)
        flash(f"Proposal '{name}' 已移除", "success")
    else:
        flash(f"Proposal '{name}' 不存在", "error")
    return redirect(url_for("proposals.index"))


@proposals_bp.route("/pause/<name>", methods=["POST"])
def pause(name):
    from pathlib import Path
    from . import _repo
    change_dir = _repo(proposals_bp) / "openspec" / "changes" / name
    pause_file = change_dir / ".paused"
    if change_dir.exists():
        pause_file.touch()
        flash(f"Proposal '{name}' 已暂停", "success")
    return redirect(url_for("proposals.index"))


@proposals_bp.route("/resume/<name>", methods=["POST"])
def resume(name):
    from pathlib import Path
    from . import _repo
    pause_file = _repo(proposals_bp) / "openspec" / "changes" / name / ".paused"
    if pause_file.exists():
        pause_file.unlink()
        flash(f"Proposal '{name}' 已恢复", "success")
    return redirect(url_for("proposals.index"))
