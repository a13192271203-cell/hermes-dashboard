"""
Hermes Agent Dashboard API - FastAPI backend for the monitoring dashboard.
Queries real data from Hermes SQLite DB, gateway logs, and cron jobs.
"""

import sqlite3
import json
import subprocess
import re
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse


def _detect_hermes_home():
    """Auto-detect Hermes home directory from multiple sources."""
    # 1. Environment variable
    env_home = os.environ.get("HERMES_HOME")
    if env_home and os.path.isdir(env_home):
        return env_home

    # 2. Default ~/.hermes/
    default_home = os.path.expanduser("~/.hermes")
    if os.path.isdir(default_home):
        return default_home

    # 3. Check /home/*/hermes/ (multi-user systems)
    import glob
    candidates = glob.glob("/home/*/hermes") + glob.glob(os.path.expanduser("~/hermes"))
    for c in candidates:
        if os.path.isdir(c):
            return c

    # 4. Fallback to default even if not found (will show errors in API)
    return default_home


HERMES_HOME = _detect_hermes_home()


def _load_dotenv():
    """Load .env file into os.environ."""
    env_path = os.path.join(HERMES_HOME, ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass

_load_dotenv()


def _load_config_yaml():
    """Load config.yaml providers and extract API keys."""
    import yaml
    config_path = os.path.join(HERMES_HOME, "config.yaml")
    providers = {}
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        for name, prov in (cfg.get("providers") or {}).items():
            key = prov.get("api_key", "")
            if key.startswith("env:"):
                # env:XXX means read from environment variable
                env_var = key[4:]
                key = os.environ.get(env_var, "")
            providers[name] = {
                "api_key": key,
                "base_url": prov.get("base_url", ""),
                "model": prov.get("default_model", ""),
            }
    except Exception as e:
        print(f"[Config YAML] Failed to load: {e}")
    return providers


_config_providers = _load_config_yaml()


# === Constants ===
DB_PATH = os.path.join(HERMES_HOME, "state.db")
LOG_PATH = os.path.join(HERMES_HOME, "logs", "gateway.log")
HERMES_VENV_PYTHON = os.path.join(HERMES_HOME, "hermes-agent", ".venv", "bin", "python3")

# Dashboard HTML: look in multiple locations
_DASHBOARD_CANDIDATES = [
    "/mnt/c/Users/Administrator/Desktop/设备监控v2/multi-agent-dashboard.html",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "multi-agent-dashboard.html"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"),
]
DASHBOARD_HTML = ""
for _p in _DASHBOARD_CANDIDATES:
    if os.path.isfile(_p):
        DASHBOARD_HTML = _p
        break

TZ_SHANGHAI = timezone(timedelta(hours=8))

MODEL_META = {
    "mimo-v2.5": {"provider": "Xiaomi", "color": "#ec4899", "display": "MiMo V2.5"},
    "GLM-5": {"provider": "Zhipu AI · 京东云", "color": "#3b82f6", "display": "GLM-5"},
    "deepseek-v4-flash": {"provider": "DeepSeek", "color": "#10b981", "display": "DeepSeek V4 Flash"},
    "qwen3.5-27b": {"provider": "Alibaba DashScope", "color": "#8b5cf6", "display": "Qwen 3.5-27B"},
}

AGENT_DEFS = [
    {
        "name": "小秘", "role": "首席秘书 · 全平台", "avatar": "👩‍💼",
        "avatarBg": "linear-gradient(135deg, #ec4899, #8b5cf6)",
        "model": "mimo-v2.5", "provider": "xiaomi",
        "source_filter": ["feishu"],
        "progressColor": "var(--gradient-3)",
        "default_task": "等待飞书消息",
    },
    {
        "name": "GLM-5 Agent", "role": "通用助手 · 默认网关", "avatar": "🧠",
        "avatarBg": "var(--gradient-1)",
        "model": "GLM-5", "provider": "zhipu",
        "source_filter": ["cron"],
        "progressColor": "var(--gradient-2)",
        "default_task": "等待 Cron 任务触发",
    },
    {
        "name": "CTO Agent", "role": "技术总监 · 项目管理", "avatar": "👔",
        "avatarBg": "linear-gradient(135deg, #f59e0b, #ef4444)",
        "model": "qwen3.5-27b", "provider": "dashscope",
        "source_filter": ["cli"],
        "progressColor": "linear-gradient(135deg, #f59e0b, #ef4444)",
        "default_task": "等待编排器分配任务",
    },
    {
        "name": "DeepSeek Agent", "role": "深度推理 · Flash", "avatar": "🔮",
        "avatarBg": "var(--gradient-4)",
        "model": "deepseek-v4-flash", "provider": "deepseek",
        "source_filter": ["api_server"],
        "progressColor": "var(--gradient-4)",
        "default_task": "等待推理任务",
    },
    {
        "name": "Feishu Bot", "role": "飞书机器人 · 京东云", "avatar": "💬",
        "avatarBg": "var(--gradient-2)",
        "model": "GLM-5", "provider": "zhipu-jd",
        "source_filter": ["feishu"],
        "progressColor": "var(--gradient-2)",
        "default_task": "飞书消息处理 · 监听中",
    },
    {
        "name": "Kanban Worker", "role": "任务执行 · Worker", "avatar": "⚙️",
        "avatarBg": "linear-gradient(135deg, #6b7280, #374151)",
        "model": "qwen3.5-27b", "provider": "dashscope",
        "source_filter": [],
        "progressColor": "linear-gradient(135deg, #6b7280, #374151)",
        "default_task": "等待编排器分配任务",
    },
]

KNOWN_PLATFORMS = ["api_server", "feishu", "weixin", "qqbot"]

# === App ===
app = FastAPI(title="Hermes Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the dashboard HTML directly."""
    try:
        with open(DASHBOARD_HTML, "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content=content)
    except Exception as e:
        return HTMLResponse(content=f"<h1>Error loading dashboard: {e}</h1>", status_code=500)


def _now():
    return datetime.now(TZ_SHANGHAI)


def _ts_now():
    return datetime.now(TZ_SHANGHAI).timestamp()


def _get_db():
    """Get a new SQLite connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_query(query, params=(), default=None):
    """Execute a query safely, returning default on error."""
    try:
        conn = _get_db()
        cur = conn.execute(query, params)
        result = cur.fetchall()
        conn.close()
        return result
    except Exception as e:
        print(f"[DB Error] {e}")
        return default or []


def _safe_query_one(query, params=(), default=None):
    """Execute a query safely, returning single row on error."""
    try:
        conn = _get_db()
        cur = conn.execute(query, params)
        result = cur.fetchone()
        conn.close()
        return result
    except Exception as e:
        print(f"[DB Error] {e}")
        return default


def _read_log_tail(n=500):
    """Read last n lines of gateway log."""
    try:
        with open(LOG_PATH, "r", errors="replace") as f:
            lines = f.readlines()
        return lines[-n:]
    except Exception as e:
        print(f"[Log Error] {e}")
        return []


def _run_cron_list():
    """Run hermes cron list and parse output."""
    try:
        # Find hermes binary: check common paths
        local_bin = os.path.expanduser("~/.local/bin")
        path_dirs = [local_bin, os.path.join(HERMES_HOME, "hermes-agent", "bin"), os.environ.get("PATH", "")]
        path_str = ":".join(p for p in path_dirs if p)

        result = subprocess.run(
            ["hermes", "cron", "list"],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "PATH": path_str}
        )
        return result.stdout
    except Exception as e:
        print(f"[Cron Error] {e}")
        return ""


def _format_tokens(n):
    """Format token count as human-readable string."""
    if n is None:
        return "0"
    if n >= 1000000:
        return f"{n/1000000:.1f}M"
    if n >= 1000:
        return f"{n/1000:.1f}K"
    return str(int(n))


def _format_uptime(started_at):
    """Format uptime from a timestamp to human-readable."""
    now = _ts_now()
    diff = now - (started_at or now)
    if diff < 0:
        diff = 0
    hours = int(diff // 3600)
    minutes = int((diff % 3600) // 60)
    if hours > 24:
        days = hours // 24
        hours = hours % 24
        return f"{days}d {hours}h"
    return f"{hours}h {minutes:02d}m"


# === Endpoint: /api/dashboard ===
@app.get("/api/dashboard")
def get_dashboard():
    now = _now()
    now_ts = now.timestamp()
    h24_ago = now_ts - 86400
    h30m_ago = now_ts - 1800

    error_fields = []

    # --- Platform status from gateway log ---
    platforms = _parse_platforms()
    if not platforms:
        error_fields.append("platforms")

    # --- Get all 24h sessions for stats ---
    sessions_24h = _safe_query(
        "SELECT * FROM sessions WHERE started_at > ?", (h24_ago,)
    )

    # --- Stats ---
    stats = _compute_stats(sessions_24h)
    if stats is None:
        error_fields.append("stats")

    # --- Agent data ---
    agents = _compute_agents(h24_ago, h30m_ago, platforms)
    if not agents:
        error_fields.append("agents")

    # --- Models ---
    models = _compute_models(sessions_24h)

    # --- Timeline ---
    timeline = _compute_timeline()

    # --- Hourly tokens ---
    hourly_tokens = _compute_hourly_tokens(h24_ago)

    # --- Cron jobs ---
    cron_jobs = _parse_cron_jobs()

    response = {
        "timestamp": now.isoformat(),
        "agents": agents,
        "models": models,
        "timeline": timeline,
        "hourly_tokens": hourly_tokens,
        "platforms": platforms,
        "stats": stats,
        "cron_jobs": cron_jobs,
    }

    if error_fields:
        response["error"] = f"部分数据加载失败: {', '.join(error_fields)}"

    return response


@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": _now().isoformat()}


@app.get("/api/config")
def get_config():
    """Return non-sensitive config info for the frontend settings panel."""
    # Check which models have valid API keys
    available_agents = []
    for name, cfg in AGENT_MODEL_MAP.items():
        has_key = bool(_resolve_api_key(cfg.get("api_key_env", ""), cfg.get("config_provider")))
        available_agents.append({
            "name": name,
            "model": cfg["model"],
            "has_api_key": has_key,
        })

    # Detect platform status
    platforms = _parse_platforms()

    # Check key file existence
    hermes_home_exists = os.path.isdir(HERMES_HOME)
    env_file_exists = os.path.isfile(os.path.join(HERMES_HOME, ".env"))
    config_yaml_exists = os.path.isfile(os.path.join(HERMES_HOME, "config.yaml"))
    db_exists = os.path.isfile(DB_PATH)

    return {
        "hermes_home": HERMES_HOME,
        "hermes_home_exists": hermes_home_exists,
        "env_file_exists": env_file_exists,
        "config_yaml_exists": config_yaml_exists,
        "db_exists": db_exists,
        "dashboard_html": DASHBOARD_HTML,
        "dashboard_html_exists": bool(DASHBOARD_HTML and os.path.isfile(DASHBOARD_HTML)),
        "available_agents": available_agents,
        "platforms": platforms,
        "models": list(MODEL_META.keys()),
    }


# === Chat: Real AI Agent Responses ===
def _resolve_api_key(env_var, config_provider=None):
    """Resolve API key: env var first, then config.yaml fallback."""
    key = os.environ.get(env_var, "")
    if key:
        return key
    if config_provider and config_provider in _config_providers:
        return _config_providers[config_provider].get("api_key", "")
    return ""


AGENT_MODEL_MAP = {
    "小秘": {"model": "mimo-v2.5", "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
             "api_key_env": "XIAOMI_API_KEY", "config_provider": None,
             "system": "你是小秘，老板的AI秘书。语气温柔、略带撒娇，称呼用户为老板。用中文回复，简洁专业。"},
    "GLM-5 Agent": {"model": "GLM-5", "base_url": "https://modelservice.jdcloud.com/coding/openai/v1",
                    "api_key_env": "JDGLM5_API_KEY", "config_provider": "jdcloud-glm5",
                    "system": "你是GLM-5 Agent，负责数据分析和任务协调。用中文回复，专业简洁。"},
    "CTO Agent": {"model": "qwen3.5-27b", "base_url": "https://api.silra.cn/v1",
                  "api_key_env": "SILRA_QWEN_API_KEY", "config_provider": "silra-qwen",
                  "system": "你是CTO Agent，负责技术架构和代码审查。用中文回复，关注技术细节。"},
    "DeepSeek Agent": {"model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com",
                       "api_key_env": "DEEPSEEK_API_KEY", "config_provider": "deepseek",
                       "system": "你是DeepSeek Agent，负责深度推理和代码分析。用中文回复，逻辑严谨。"},
    "Feishu Bot": {"model": "GLM-5", "base_url": "https://modelservice.jdcloud.com/coding/openai/v1",
                   "api_key_env": "JDGLM5_API_KEY", "config_provider": "jdcloud-glm5",
                   "system": "你是飞书Bot助手，负责飞书平台的消息处理和通知。用中文回复。"},
    "Kanban Worker": {"model": "qwen3.5-27b", "base_url": "https://api.silra.cn/v1",
                      "api_key_env": "SILRA_QWEN_API_KEY", "config_provider": "silra-qwen",
                      "system": "你是Kanban Worker，负责执行看板任务。用中文回复，简洁高效。"},
}


def _call_llm(model_cfg: dict, user_message: str) -> str:
    """Call an LLM provider directly via HTTP."""
    api_key = _resolve_api_key(
        model_cfg.get("api_key_env", ""),
        model_cfg.get("config_provider"),
    )
    if not api_key:
        return f"⚠️ 无法响应：未配置 API Key（{model_cfg.get('api_key_env', 'N/A')}）"

    url = model_cfg["base_url"].rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": model_cfg["model"],
        "messages": [
            {"role": "system", "content": model_cfg["system"]},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": 500,
        "temperature": 0.7,
    }).encode("utf-8")

    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"⚠️ 调用失败：{str(e)[:100]}"


@app.post("/api/chat")
async def chat(request: Request):
    """Send a message to a selected agent and get real AI response.
    Optional: is_manager + team_members for delegation system prompt."""
    body = await request.json()
    agent_name = body.get("agent", "小秘")
    message = body.get("message", "").strip()
    is_manager = body.get("is_manager", False)
    team_members = body.get("team_members", [])

    if not message:
        return {"error": "消息不能为空"}

    model_cfg = AGENT_MODEL_MAP.get(agent_name)
    if not model_cfg:
        return {"error": f"未知 Agent: {agent_name}"}

    # If manager, use special system prompt for delegation
    system_prompt = model_cfg["system"]
    if is_manager and team_members:
        members_str = ", ".join([m for m in team_members if m != agent_name])
        system_prompt = f"""你是团队总监/经理，名叫{agent_name}。你需要：
1. 分析用户的需求并给出专业意见
2. 如果需要分配子任务给团队成员，在回复末尾用 @成员名 任务描述 的格式指定
3. 如果不需要分配，直接回复即可

团队成员：{members_str}

回复格式要求：
- 先给出你的分析和决策
- 如果需要分配任务，在回复末尾另起一行写：@成员名 具体任务描述
- 可以 @多个成员
- 每个任务描述要具体明确"""

    model_cfg_copy = dict(model_cfg)
    model_cfg_copy["system"] = system_prompt
    reply = _call_llm(model_cfg_copy, message)
    return {"agent": agent_name, "reply": reply}


# === Platform parsing ===
def _parse_platforms():
    """Parse gateway log for platform connection status."""
    lines = _read_log_tail(500)
    platform_status = {p: "offline" for p in KNOWN_PLATFORMS}

    for line in lines:
        # ✓ xxx connected
        m = re.search(r"✓\s+(\w+)\s+connected", line)
        if m:
            name = m.group(1)
            if name in platform_status:
                platform_status[name] = "online"
            continue

        # ✗ xxx failed to connect
        m = re.search(r"✗\s+(\w+)\s+failed", line)
        if m:
            name = m.group(1)
            if name in platform_status:
                platform_status[name] = "offline"
            continue

        # Disconnected or reconnect failed
        for p in KNOWN_PLATFORMS:
            if p in line.lower() and ("disconnected" in line.lower() or "reconnect failed" in line.lower()):
                platform_status[p] = "offline"

    result = []
    for p in KNOWN_PLATFORMS:
        display_names = {
            "api_server": "API Server",
            "feishu": "飞书",
            "weixin": "微信",
            "qqbot": "QQ Bot",
        }
        result.append({
            "name": p,
            "display": display_names.get(p, p),
            "status": platform_status[p],
        })

    return result


# === Stats computation ===
def _compute_stats(sessions):
    if not sessions:
        return {
            "sessions": 0, "messages": 0, "tokens": 0, "tool_calls": 0,
            "cost": 0, "active_agents": 0, "success_rate": 0,
        }

    total_tokens = 0
    total_messages = 0
    total_tool_calls = 0
    total_cost = 0

    for s in sessions:
        total_tokens += (s["input_tokens"] or 0) + (s["output_tokens"] or 0)
        total_messages += s["message_count"] or 0
        total_tool_calls += s["tool_call_count"] or 0
        total_cost += s["estimated_cost_usd"] or 0

    # Count active agents (sessions in last 30 min)
    now_ts = _ts_now()
    active_sources = set()
    for s in sessions:
        if s["started_at"] and (now_ts - s["started_at"]) < 1800:
            active_sources.add(s["source"])

    completed = sum(1 for s in sessions if s["ended_at"])
    success_rate = round((completed / len(sessions)) * 100) if sessions else 0

    return {
        "sessions": len(sessions),
        "messages": total_messages,
        "tokens": total_tokens,
        "tool_calls": total_tool_calls,
        "cost": round(total_cost * 7.2, 2),  # USD to CNY rough conversion
        "active_agents": len(active_sources),
        "success_rate": success_rate,
    }


# === Agent computation ===
def _compute_agents(h24_ago, h30m_ago, platforms):
    """Build agent list with real data."""
    agents = []
    feishu_connected = any(
        p["name"] == "feishu" and p["status"] == "online" for p in platforms
    )

    for adef in AGENT_DEFS:
        source_filter = adef["source_filter"]
        model = adef["model"]

        # Token sum for this model in 24h
        rows = _safe_query(
            "SELECT SUM(input_tokens + output_tokens) as tokens, "
            "COUNT(*) as task_count, "
            "SUM(tool_call_count) as tool_calls, "
            "MAX(ended_at) as last_ended, "
            "MAX(started_at) as last_started, "
            "SUM(message_count) as msgs "
            "FROM sessions "
            "WHERE started_at > ? AND model = ?",
            (h24_ago, model),
        )
        row = rows[0] if rows else None

        tokens = row["tokens"] if row and row["tokens"] else 0
        task_count = row["task_count"] if row else 0
        tool_calls = row["tool_calls"] if row and row["tool_calls"] else 0
        last_started = row["last_started"] if row else None
        last_ended = row["last_ended"] if row else None

        # Status logic
        now_ts = _ts_now()
        status = "idle"
        status_text = "待命"

        if last_started and (now_ts - last_started) < 1800:
            status = "active"
            status_text = "在线"
        elif task_count > 0:
            status = "idle"
            status_text = "待命"

        # Special: Feishu Bot checks gateway log
        if adef["name"] == "Feishu Bot":
            if feishu_connected:
                status = "active"
                status_text = "在线"
            else:
                status = "error"
                status_text = "异常"

        # Special: Kanban Worker is always idle
        if adef["name"] == "Kanban Worker":
            if task_count == 0:
                status = "idle"
                status_text = "待命"

        # Task description
        task = adef["default_task"]
        if task_count > 0:
            task = f"最近 24h 完成 {task_count} 个任务"
            if last_ended:
                mins_ago = int((now_ts - last_ended) / 60)
                if mins_ago < 60:
                    task += f" · {mins_ago}分钟前结束"
                else:
                    task += f" · {mins_ago // 60}小时前结束"

        # Progress: tool_call_count as rough pct, capped at 100
        progress = min(tool_calls, 100) if tool_calls else 0

        # Uptime: from earliest session to now (if has sessions)
        if task_count > 0 and last_started:
            uptime = _format_uptime(last_started)
        else:
            uptime = "0h 00m"

        agents.append({
            "name": adef["name"],
            "role": adef["role"],
            "avatar": adef["avatar"],
            "avatarBg": adef["avatarBg"],
            "status": status,
            "statusText": status_text,
            "task": task,
            "tokens": _format_tokens(tokens),
            "tasks": task_count,
            "uptime": uptime,
            "progress": progress,
            "progressColor": adef["progressColor"],
            "model": model,
            "provider": adef["provider"],
        })

    return agents


# === Models computation ===
def _compute_models(sessions):
    """Group sessions by model, compute token usage and cost."""
    model_stats = {}
    for s in sessions:
        model = s["model"] or "unknown"
        tokens = (s["input_tokens"] or 0) + (s["output_tokens"] or 0)
        cost = s["estimated_cost_usd"] or 0
        if model not in model_stats:
            model_stats[model] = {"tokens": 0, "cost": 0, "count": 0}
        model_stats[model]["tokens"] += tokens
        model_stats[model]["cost"] += cost
        model_stats[model]["count"] += 1

    total_tokens = sum(v["tokens"] for v in model_stats.values())
    result = []

    for model, stats in sorted(model_stats.items(), key=lambda x: -x[1]["tokens"]):
        meta = MODEL_META.get(model, {
            "provider": "Unknown",
            "color": "#6b7280",
            "display": model,
        })
        pct = round((stats["tokens"] / total_tokens) * 100) if total_tokens > 0 else 0
        cost_cny = round(stats["cost"] * 7.2, 2) if stats["cost"] else 0

        result.append({
            "name": meta.get("display", model),
            "provider": meta["provider"],
            "color": meta["color"],
            "tokens": _format_tokens(stats["tokens"]),
            "cost": f"¥{cost_cny}" if cost_cny else "¥0",
            "pct": pct,
        })

    return result


# === Timeline computation ===
def _compute_timeline():
    """Get 10 most recent sessions as timeline events."""
    rows = _safe_query(
        "SELECT source, model, title, started_at, message_count "
        "FROM sessions ORDER BY started_at DESC LIMIT 10"
    )

    if not rows:
        return []

    source_icons = {
        "feishu": ("💬", "rgba(6,182,212,0.15)"),
        "cli": ("🖥️", "rgba(59,130,246,0.15)"),
        "cron": ("⏰", "rgba(139,92,246,0.15)"),
        "api_server": ("🔗", "rgba(16,185,129,0.15)"),
        "weixin": ("📱", "rgba(16,185,129,0.15)"),
    }

    source_names = {
        "feishu": "飞书",
        "cli": "CLI",
        "cron": "Cron",
        "api_server": "API",
        "weixin": "微信",
    }

    result = []
    for r in rows:
        source = r["source"] or "unknown"
        model = r["model"] or "unknown"
        title = r["title"] or "对话"
        ts = r["started_at"] or 0
        msgs = r["message_count"] or 0

        icon, icon_bg = source_icons.get(source, ("📋", "rgba(107,114,128,0.15)"))
        display_name = source_names.get(source, source)

        # Format time
        if ts > 0:
            dt = datetime.fromtimestamp(ts, TZ_SHANGHAI)
            time_str = dt.strftime("%H:%M")
        else:
            time_str = "--:--"

        # Format text
        text = f"<strong>{display_name}</strong> {title[:30]} · {msgs}条消息"
        if msgs == 0:
            text = f"<strong>{display_name}</strong> {title[:30]}"

        result.append({
            "icon": icon,
            "iconBg": icon_bg,
            "text": text,
            "time": time_str,
        })

    return result


# === Hourly tokens computation ===
def _compute_hourly_tokens(h24_ago):
    """Group sessions by hour for last 24h, sum tokens."""
    rows = _safe_query(
        "SELECT "
        "CAST((started_at - ?) / 3600 AS INTEGER) % 24 as hour, "
        "SUM(input_tokens + output_tokens) as tokens "
        "FROM sessions WHERE started_at > ? "
        "GROUP BY hour ORDER BY hour",
        (h24_ago, h24_ago),
    )

    hourly = [0] * 24
    for r in rows:
        h = r["hour"]
        if 0 <= h < 24:
            # Convert raw tokens to K for display
            hourly[h] = round((r["tokens"] or 0) / 1000, 1)

    return hourly


# === Cron jobs parsing ===
def _parse_cron_jobs():
    """Parse hermes cron list output."""
    output = _run_cron_list()
    if not output:
        return []

    jobs = []
    current_job = {}

    for line in output.split("\n"):
        line = line.strip()

        # Job ID line: e.g. "e2255c1048f9 [active]"
        m = re.match(r"^([0-9a-f]+)\s+\[(\w+)\]", line)
        if m:
            if current_job.get("id"):
                jobs.append(current_job)
            current_job = {
                "id": m.group(1),
                "status": m.group(2),
                "name": "",
                "schedule": "",
                "next_run": "",
                "last_run": "",
            }
            continue

        if not current_job:
            continue

        # Name: Name: xxx
        m = re.match(r"^Name:\s*(.+)$", line)
        if m:
            current_job["name"] = m.group(1).strip()
            continue

        # Schedule: Schedule: xxx
        m = re.match(r"^Schedule:\s*(.+)$", line)
        if m:
            current_job["schedule"] = m.group(1).strip()
            continue

        # Next run: Next run: xxx
        m = re.match(r"^Next run:\s*(.+)$", line)
        if m:
            current_job["next_run"] = m.group(1).strip()
            continue

        # Last run: Last run: xxx
        m = re.match(r"^Last run:\s*(.+)$", line)
        if m:
            current_job["last_run"] = m.group(1).strip()
            continue

    if current_job.get("id"):
        jobs.append(current_job)

    return jobs


# === Main ===
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8650)
