# zsiga-web

zsiga 自主工程智能体的 Web 管理控制台。独立于 daemon 进程运行，提供系统配置、需求投递、运行监控能力。

## 架构

```
zsiga-web (Flask, 58176)           zsiga-daemon (端口 58175)
├── / — 仪表盘                        ├── pipeline 执行
├── /admin/ — 系统配置                  └── /api/* 只读
├── /proposals/ — 需求管理
│                                 共享数据层:
│                                 ├── zsiga.yaml
│                                 ├── openspec/changes/
│                                 └── data/zsiga.db
```

两个进程通过共享文件系统通信，不依赖网络或消息队列。daemon 挂掉时管理控制台仍可操作。

## 快速开始

### 前置条件

- Python 3.10+
- zsiga daemon 已部署（共享同一个 repo 目录）

### 安装

```bash
git clone git@github.com:hsbreeze01/zsiga-web.git
cd zsiga-web
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
```

### 启动

```bash
# 前台运行（开发调试）
python run.py --port=58176 --repo=/path/to/zsiga/repo

# 指定 zsiga repo 路径（默认 /home/zsiga/repo）
python run.py --port=58176 --repo=~/ProjectNIO/zsiga
```

浏览器打开 `http://localhost:58176`。

### systemd 部署

```bash
# 复制 service 文件
sudo cp zsiga-web.service /etc/systemd/system/

# 按需修改 service 文件中的路径
sudo systemctl daemon-reload
sudo systemctl enable zsiga-web
sudo systemctl start zsiga-web

# 管理命令
sudo systemctl status zsiga-web
sudo systemctl restart zsiga-web
sudo systemctl stop zsiga-web
```

service 文件内容：

```ini
[Unit]
Description=zsiga web console
After=network-online.target

[Service]
Type=simple
User=lancer
WorkingDirectory=/home/zsiga/zsiga-web
Environment=ZSIGA_REPO=/home/zsiga/repo
ExecStart=/home/zsiga/zsiga-web/venv/bin/python run.py --port=58176 --repo=/home/zsiga/repo
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## 页面说明

### 仪表盘（/）

首页，30 秒自动刷新。显示：

| 区域 | 内容 |
|------|------|
| 概览卡片 | daemon 状态、总变更数、成功率、目标项目数、LLM 模型 |
| 当前任务 | 正在执行的 proposal 名称、当前 phase、各 phase 进度点 |
| 提案队列 | 排队中的 proposal 列表 |
| 近期记录 | 最近 5 条执行记录（名称、结果、耗时） |

数据来源：读取 daemon 的 `/api/pipeline-status` 和 `/api/proposal-stats`。daemon 未运行时仪表盘仍可打开，数据显示为空。

### 系统配置（/admin/）

#### 项目配置

添加、删除、验证目标项目：

| 字段 | 说明 | 示例 |
|------|------|------|
| 项目名称 | 英文标识符 | `factory` |
| 项目路径 | 服务器上的绝对路径 | `/home/user/project` |
| 部署分支 | DELIVER 时 merge 到的分支 | `main` |
| 测试命令 | VERIFY 阶段执行的测试命令 | `venv/bin/python -m pytest -x` |
| 传输方式 | `local`（本机）或 `ssh`（远程） | `local` |

点击「验证」按钮会检查路径是否存在、是否为 Git 仓库、remote 是否配置。

SSH 模式额外需要填写：主机地址、用户名、密钥路径。

#### LLM 配置

| 字段 | 说明 | 默认值 |
|------|------|--------|
| 供应商 | LLM 服务商 | `zhipuai` |
| 模型 | 模型名称 | `glm-5.1` |
| API Key | API 密钥 | — |
| Base URL | API 端点 | `https://open.bigmodel.cn/api/coding/paas/v4` |

保存时自动验证连通性：发送一个最小请求到 API，成功显示「连接成功」，失败显示具体错误。

#### GitHub 配置

| 字段 | 说明 |
|------|------|
| Token | GitHub Personal Access Token（`ghp_` 开头） |
| 仓库地址 | Git remote URL（`git@github.com:...`） |

保存时验证 token 有效性（调用 GitHub `/user` API），显示认证用户名。

### 需求管理（/proposals/）

#### 需求队列

显示所有排队中的 proposal：

| 列 | 说明 |
|----|------|
| 提案名称 | proposal 目录名 |
| 当前阶段 | `.phase_state` 中记录的 phase（等待中 / clarify / enrich / ...） |
| Steward 审查 | 是否已有 `steward-review.md` |
| 状态 | 执行中 / 排队中 |
| 操作 | 移除（删除 proposal 目录） |

#### 新建需求（/proposals/submit）

**两步操作：预检查 → 提交**

1. 填写需求名称和内容（Markdown）
2. 点「预检查」— 系统检查以下要素：
   - 内容长度 >= 100 字符
   - 包含 `## Summary` 段落
   - 包含 `## Problem` 或 `## 问题` 段落
   - 包含 `## Acceptance` 或 `## 验收` 段落
   - 包含 `## Scope` 或 `## 范围` 段落
3. 检查通过后点「提交到队列」— 内容写入 `openspec/changes/<name>/proposal.md`

**Proposal 描述要点：**

```markdown
# proposal-name

## Summary
一句话描述要做什么。必须包含目标文件和功能。

## Problem
为什么需要这个变更。现有系统的什么缺陷。

## Technical Design
技术方案：
- 要修改的文件列表
- 接口设计和数据流
- 错误处理策略

## Acceptance Criteria
验收标准（每条必须可验证）：
1. `curl http://localhost:58175/api/health` 返回 HTTP 200
2. 响应包含 `status` 字段
3. 现有功能不受影响

## Scope
- **In scope**: 明确在范围内的变更
- **Out of scope**: 明确不在范围内

## Risk
- **Impact**: 高/中/低
- **Blast radius**: 影响范围
- **Reversibility**: `git revert <hash>` 回滚
```

也可指定目标项目（留空则使用默认）。提交后 daemon 的下一个 cycle 会自动扫描并开始处理。

## API 端点

所有 API 返回 JSON。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 当前 zsiga.yaml 配置 |
| GET | `/api/daemon-status` | daemon 状态（读 daemon_state.json） |
| GET | `/api/proposals` | 当前提案列表（扫描 openspec/changes/） |
| GET | `/api/proposal-stats` | 历史统计（代理 daemon 的 /api/proposal-stats） |
| GET | `/api/pipeline-status` | pipeline 实时状态（代理 daemon 的 /api/pipeline-status） |
| POST | `/admin/llm/validate` | 验证 LLM Key 连通性（JSON: api_key, base_url, model） |
| POST | `/proposals/precheck` | 预检查 proposal 内容（JSON: content） |

## 配置

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `ZSIGA_REPO` | zsiga repo 根目录路径 | `/home/zsiga/repo` |

### 命令行参数

```
python run.py --help

--port    监听端口（默认 58176）
--host    绑定地址（默认 0.0.0.0）
--repo    zsiga repo 路径（默认 /home/zsiga/repo）
```

## 本地开发

```bash
cd ~/ProjectNIO/zsiga-web
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt

# 指向本地 zsiga repo（需要有 zsiga.yaml 和 data/ 目录）
python run.py --port=58176 --repo=~/ProjectNIO/zsiga
```

访问 `http://localhost:58176`。

### 目录结构

```
zsiga-web/
├── run.py                    # 入口
├── requirements.txt          # Python 依赖
├── zsiga-web.service         # systemd 服务文件
├── zsiga_web/
│   ├── __init__.py           # Flask app + 共享工具函数
│   ├── admin.py              # 系统配置 Blueprint
│   ├── proposals.py          # 需求管理 Blueprint
│   ├── templates/
│   │   ├── base.html         # 基础布局（导航、flash、footer）
│   │   ├── index.html        # 仪表盘
│   │   ├── admin.html        # 系统配置页
│   │   ├── proposals.html    # 需求队列页
│   │   └── proposal_submit.html  # 新建需求页
│   └── static/
│       └── style.css         # 暗色主题样式
```

## 常见问题

### Q: 仪表盘数据为空？
daemon 未运行或 58175 端口不可达。zsiga-web 通过 `curl localhost:58175/api/*` 获取数据。确认 daemon 在运行：`sudo systemctl status zsiga-daemon`。

### Q: 保存配置后 daemon 没有生效？
daemon 在每个 cycle 开始时读取 `zsiga.yaml`。手动触发：`sudo systemctl restart zsiga-daemon`。

### Q: 端口被占用？
检查占用进程：`lsof -i :58176`。换端口：`python run.py --port=58177`。

### Q: 外网无法访问？
云服务器需在安全组开放对应端口（TCP 入站）。iptables/ufw 也要放行：`sudo ufw allow 58176/tcp`。
