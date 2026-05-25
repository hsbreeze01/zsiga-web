from flask import Blueprint, render_template, request, redirect, url_for, flash

from . import (
    _load_config, _save_config, _validate_llm_key,
    _validate_project_path, _validate_git_remote, _validate_github_token,
)

admin_bp = Blueprint("admin", __name__, template_folder="../templates")


@admin_bp.route("/")
def index():
    config = _load_config(admin_bp)
    targets = config.get("targets", {})
    llm = config.get("agent", {}).get("llm", {})
    github = config.get("github", {})
    return render_template("admin.html", targets=targets, llm=llm, github=github)


@admin_bp.route("/target/add", methods=["POST"])
def target_add():
    name = request.form.get("name", "").strip()
    path = request.form.get("path", "").strip()
    deploy_branch = request.form.get("deploy_branch", "main").strip()
    test_cmd = request.form.get("test_cmd", "").strip()
    transport = request.form.get("transport", "local").strip()

    if not name or not path:
        flash("项目名称和路径不能为空", "error")
        return redirect(url_for("admin.index"))

    validation = _validate_project_path(path)
    if not validation["ok"]:
        flash(f"路径验证失败: {validation['message']}", "error")
        return redirect(url_for("admin.index"))

    config = _load_config(admin_bp)
    if "targets" not in config:
        config["targets"] = {}

    target_cfg = {
        "path": path,
        "deploy_branch": deploy_branch,
        "transport": transport,
    }
    if test_cmd:
        target_cfg["test_cmd"] = test_cmd

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
    _save_config(admin_bp, config)
    flash(f"项目 {name} 添加成功", "success")
    return redirect(url_for("admin.index"))


@admin_bp.route("/target/delete/<name>", methods=["POST"])
def target_delete(name):
    config = _load_config(admin_bp)
    if "targets" in config and name in config["targets"]:
        del config["targets"][name]
        _save_config(admin_bp, config)
        flash(f"项目 {name} 已删除", "success")
    return redirect(url_for("admin.index"))


@admin_bp.route("/target/validate/<name>")
def target_validate(name):
    config = _load_config(admin_bp)
    target = config.get("targets", {}).get(name)
    if not target:
        return {"ok": False, "message": f"项目 {name} 不存在"}
    path_result = _validate_project_path(target.get("path", ""))
    git_result = _validate_git_remote(target.get("path", ""))
    messages = []
    if path_result["ok"]:
        messages.append(f"路径: {path_result['message']}")
    else:
        messages.append(f"路径: {path_result['message']}")
    if git_result["ok"]:
        messages.append(f"Git: {git_result['message']}")
    else:
        messages.append(f"Git: {git_result['message']}")
    all_ok = path_result["ok"] and git_result["ok"]
    return {"ok": all_ok, "message": " | ".join(messages)}


@admin_bp.route("/llm", methods=["POST"])
def llm_update():
    provider = request.form.get("provider", "zhipuai").strip()
    api_key = request.form.get("api_key", "").strip()
    model = request.form.get("model", "glm-5.1").strip()
    base_url = request.form.get("base_url", "").strip()

    if not api_key:
        flash("API Key 不能为空", "error")
        return redirect(url_for("admin.index"))

    config = _load_config(admin_bp)
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
    _save_config(admin_bp, config)

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

    config = _load_config(admin_bp)
    config["github"] = {
        "token": token or "${GITHUB_TOKEN}",
        "repo_url": repo_url,
    }
    _save_config(admin_bp, config)

    if token and not token.startswith("${"):
        validation = _validate_github_token(token)
        if validation["ok"]:
            flash(f"GitHub 配置已保存并验证成功: {validation['message']}", "success")
        else:
            flash(f"GitHub 配置已保存，但验证失败: {validation['message']}", "warning")
    else:
        flash("GitHub 配置已保存（未提供 token，跳过验证）", "success")

    return redirect(url_for("admin.index"))
