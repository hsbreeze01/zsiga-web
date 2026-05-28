from flask import Blueprint, render_template, request, redirect, url_for, flash

from . import (
    _load_config, _save_config, _validate_llm_key,
    _validate_project_path, _validate_git_remote, _validate_github_token,
    _ensure_self_target, _restart_daemon, _get_active_target,
    _check_daemon_state, _load_runtime_state, _save_runtime_state,
)

admin_bp = Blueprint("admin", __name__, template_folder="../templates")


@admin_bp.route("/")
def index():
    config = _load_config()
    config = _ensure_self_target(config)
    targets = config.get("targets", {})
    active_target, active_target_info = _get_active_target(config)
    llm = config.get("agent", {}).get("llm", {})
    github = config.get("github", {})
    evolution = _load_runtime_state()
    daemon_state = _check_daemon_state()
    return render_template("admin.html",
                           targets=targets,
                           active_target=active_target,
                           active_target_info=active_target_info,
                           llm=llm, github=github,
                           evolution=evolution,
                           daemon_state=daemon_state)


@admin_bp.route("/target/add", methods=["POST"])
def target_add():
    return target_update()


@admin_bp.route("/target/update", methods=["POST"])
def target_update():
    name = request.form.get("name", "").strip()
    path = request.form.get("path", "").strip()
    deploy_branch = request.form.get("deploy_branch", "main").strip()
    test_cmd = request.form.get("test_cmd", "").strip()
    lint_cmd = request.form.get("lint_cmd", "").strip()
    transport = request.form.get("transport", "local").strip()
    description = request.form.get("description", "").strip()
    tech_stack_raw = request.form.get("tech_stack", "").strip()
    key_dirs_raw = request.form.get("key_dirs", "").strip()
    conventions = request.form.get("conventions", "").strip()

    if not name or not path:
        flash("项目名称和路径不能为空", "error")
        return redirect(url_for("admin.index"))

    if name == "zsiga":
        flash("不能修改 zsiga 自身 target（自演进默认存在）", "error")
        return redirect(url_for("admin.index"))

    config = _load_config()
    config = _ensure_self_target(config)
    if "targets" not in config:
        config["targets"] = {}

    tech_stack = [t.strip() for t in tech_stack_raw.split(",") if t.strip()] if tech_stack_raw else []
    key_dirs = [d.strip() for d in key_dirs_raw.split(",") if d.strip()] if key_dirs_raw else []

    target_cfg = {
        "domain": "external",
        "path": path,
        "deploy_branch": deploy_branch,
        "transport": transport,
        "description": description,
        "tech_stack": tech_stack,
        "key_dirs": key_dirs,
        "conventions": conventions,
    }
    if test_cmd:
        target_cfg["test_cmd"] = test_cmd
    if lint_cmd:
        target_cfg["lint_cmd"] = lint_cmd

    if transport == "ssh":
        ssh_host = request.form.get("ssh_host", "").strip()
        ssh_user = request.form.get("ssh_user", "root").strip()
        ssh_key = request.form.get("ssh_key_path", "").strip()
        if ssh_host:
            target_cfg["ssh"] = {
                "host": ssh_host,
                "user": ssh_user,
                "key_path": ssh_key,
            }

    config["targets"][name] = target_cfg
    _save_config(config)
    flash(f"外部 target {name} 已保存（需切换激活或重启 daemon 生效）", "success")
    return redirect(url_for("admin.index"))


@admin_bp.route("/target/activate/<name>", methods=["POST"])
def target_activate(name):
    config = _load_config()
    config = _ensure_self_target(config)

    if name != "zsiga" and name not in config.get("targets", {}):
        flash(f"Target {name} 不存在", "error")
        return redirect(url_for("admin.index"))

    daemon_state = _check_daemon_state()
    daemon_running = daemon_state.get("state") in ("running",)
    current_active = _load_runtime_state().get("active_target", "zsiga")

    if current_active == "zsiga" and daemon_running:
        rs = _load_runtime_state()
        rs["pending_switch"] = name
        _save_runtime_state(rs)
        mode = "自演进模式" if name == "zsiga" else f"外部项目: {name}"
        flash(f"已排队切换到 {mode}。当前 cycle 完成后将自动切换。", "success")
        return redirect(url_for("admin.index"))

    rs = _load_runtime_state()
    rs["active_target"] = name
    rs.pop("pending_switch", None)
    _save_runtime_state(rs)

    result = _restart_daemon()
    if result["ok"]:
        mode = "自演进模式" if name == "zsiga" else f"外部项目: {name}"
        flash(f"已切换到 {mode}，daemon 重启中...", "success")
    else:
        flash(f"配置已保存，但 daemon 重启失败: {result['message']}", "warning")
    return redirect(url_for("admin.index"))


@admin_bp.route("/target/delete/<name>", methods=["POST"])
def target_delete(name):
    if name == "zsiga":
        flash("zsiga 自身 target 不可删除（自演进默认存在）", "error")
        return redirect(url_for("admin.index"))
    config = _load_config()
    if "targets" in config and name in config["targets"]:
        del config["targets"][name]
        _save_config(config)
        rs = _load_runtime_state()
        if rs.get("active_target") == name:
            rs["active_target"] = "zsiga"
            _save_runtime_state(rs)
        flash(f"项目 {name} 已删除", "success")
    return redirect(url_for("admin.index"))


@admin_bp.route("/target/validate/<name>")
def target_validate(name):
    config = _load_config()
    target = config.get("targets", {}).get(name)
    if not target:
        return {"ok": False, "message": f"项目 {name} 不存在"}
    path_result = _validate_project_path(target.get("path", ""))
    git_result = _validate_git_remote(target.get("path", ""))
    messages = []
    messages.append(f"路径: {path_result['message']}")
    messages.append(f"Git: {git_result['message']}")
    all_ok = path_result["ok"] and git_result["ok"]
    return {"ok": all_ok, "message": " | ".join(messages)}


@admin_bp.route("/daemon/restart", methods=["POST"])
def daemon_restart():
    result = _restart_daemon()
    if result["ok"]:
        flash("Daemon 重启中...", "success")
    else:
        flash(f"重启失败: {result['message']}", "error")
    return redirect(url_for("admin.index"))


@admin_bp.route("/llm", methods=["POST"])
def llm_update():
    provider = request.form.get("provider", "zhipuai").strip()
    api_key = request.form.get("api_key", "").strip()
    model = request.form.get("model", "glm-5.1").strip()
    base_url = request.form.get("base_url", "").strip()

    if not api_key:
        flash("API Key 不能为空", "error")
        return redirect(url_for("admin.index"))

    config = _load_config()
    if "agent" not in config:
        config["agent"] = {}
    config["agent"]["llm"] = {
        "provider": provider,
        "api_key": api_key,
        "model": model,
        "base_url": base_url,
        "max_tokens": config.get("agent", {}).get("llm", {}).get("max_tokens", 4096),
        "temperature": config.get("agent", {}).get("llm", {}).get("temperature", 0.3),
    }
    _save_config(config)

    validation = _validate_llm_key(api_key, base_url, model)
    if validation["ok"]:
        flash(f"LLM 配置已保存并验证成功: {validation['message']}", "success")
    else:
        flash(f"LLM 配置已保存，但验证失败: {validation['message']}", "warning")

    return redirect(url_for("admin.index"))


@admin_bp.route("/llm/validate", methods=["POST"])
def llm_validate():
    data = request.get_json() or request.form
    api_key = data.get("api_key", "")
    base_url = data.get("base_url", "")
    model = data.get("model", "")
    if not api_key or not base_url:
        return {"ok": False, "message": "参数不完整"}
    return _validate_llm_key(api_key, base_url, model)


@admin_bp.route("/github", methods=["POST"])
def github_update():
    token = request.form.get("token", "").strip()
    repo_url = request.form.get("repo_url", "").strip()

    config = _load_config()
    config["github"] = {
        "token": token or "${GITHUB_TOKEN}",
        "repo_url": repo_url,
    }
    _save_config(config)

    if token and not token.startswith("${"):
        validation = _validate_github_token(token)
        if validation["ok"]:
            flash(f"GitHub 配置已保存并验证成功: {validation['message']}", "success")
        else:
            flash(f"GitHub 配置已保存，但验证失败: {validation['message']}", "warning")
    else:
        flash("GitHub 配置已保存（未提供 token，跳过验证）", "success")

    return redirect(url_for("admin.index"))


@admin_bp.route("/evolution-schedule", methods=["POST"])
def evolution_schedule():
    start_hour = request.form.get("start_hour", "22").strip()
    end_hour = request.form.get("end_hour", "10").strip()

    try:
        start_h = int(start_hour)
        end_h = int(end_hour)
        if not (0 <= start_h <= 23 and 0 <= end_h <= 23):
            raise ValueError
    except (ValueError, TypeError):
        flash("时间格式错误，请输入 0-23 的整数", "error")
        return redirect(url_for("admin.index"))

    config = _load_config()
    if "pipeline" not in config:
        config["pipeline"] = {}
    config["pipeline"]["evolution_window_start_hour"] = start_h
    config["pipeline"]["evolution_window_end_hour"] = end_h
    _save_config(config)

    rs = _load_runtime_state()
    rs["evolution_window_start_hour"] = start_h
    rs["evolution_window_end_hour"] = end_h
    _save_runtime_state(rs)

    flash(f"自演进时间窗口已更新: {start_h}:00 - {end_h}:00（到时间将自动切回自演进模式）", "success")
    return redirect(url_for("admin.index"))
