# 🤖 Hermes Agent Dashboard - 多Agent监控面板

实时监控你的 Hermes Agent 编队状态，支持多模型、多平台的统一指挥中心。

## ✨ 功能特性

- **实时监控** — 6 个 Agent 卡片，显示在线状态、任务进度、Token 消耗
- **多模型支持** — MiMo V2.5、GLM-5、DeepSeek、Qwen 等模型自动识别
- **平台状态** — 飞书、微信、API Server 等平台连接状态一目了然
- **群聊系统** — 支持多 Agent 同时对话，含总监委任和任务分配
- **活动时间线** — 最近 10 条对话实时滚动
- **Token 消耗图表** — 24 小时 Token 使用趋势
- **深色主题** — 精美暗色玻璃拟态设计

## 🚀 快速开始（3 步）

### 1. 解压文件

```bash
unzip hermes-dashboard-v1.0.zip
cd hermes-dashboard
```

### 2. 安装依赖并启动

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 启动服务
python dashboard_api.py
```

> 💡 推荐使用 `start.sh` 启动（自动指定端口和热重载）：
> ```bash
> chmod +x start.sh
> ./start.sh
> ```

### 3. 打开浏览器

访问 **http://localhost:8650**

如果是远程服务器部署，在局域网内访问：
```
http://<服务器IP>:8650
```

## ⚙️ 配置说明

### 环境变量（.env）

将你的 Hermes `.env` 文件放到 `~/.hermes/.env`，或通过环境变量 `HERMES_HOME` 指定路径：

```bash
export HERMES_HOME=/path/to/your/hermes
```

`.env` 文件中需要配置以下 API Key（按需）：

| 变量名 | 说明 | 对应模型 |
|--------|------|----------|
| `XIAOMI_API_KEY` | 小米 MiMo API Key | mimo-v2.5 |
| `SILRA_QWEN_API_KEY` | Silra Qwen API Key | qwen3.5-27b |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | deepseek-v4-flash |
| `JDGLM5_API_KEY` | 京东云 GLM-5 API Key | GLM-5 |

### config.yaml 回退机制

如果你有 Hermes 的 `config.yaml`（在 `~/.hermes/config.yaml`），Dashboard 会自动读取其中的 API Key 作为回退。

例如 `config.yaml` 中的：
```yaml
providers:
  jdcloud-glm5:
    api_key: pk-xxxx-xxxx
```

会被自动用于 GLM-5 Agent。

### 高级配置

- **自定义端口**：修改 `dashboard_api.py` 底部的 `port=8650`
- **前端页面路径**：Dashboard 会自动搜索多个 HTML 文件位置
- **CORS**：默认允许所有来源（`allow_origins=["*"]`）

## 🖥️ 界面说明

### 主界面
- **顶部统计栏** — 在线 Agent 数、今日任务、Token 消耗、成本、成功率
- **Agent 卡片** — 点击卡片打开详情面板，可与 Agent 对话
- **活动时间线** — 实时展示最近对话
- **Token 图表** — 24 小时消耗趋势
- **群聊面板** — 选择多个 Agent 进行群聊

### 设置面板
- 点击右上角 ⚙️ 图标打开
- 可设置 API 服务器地址（支持远程部署）
- 测试连接按钮验证服务是否正常
- 显示系统检测信息和 Agent API Key 状态

## ❓ 常见问题

### Q: 启动后显示"部分数据加载失败"？
A: 确保 `~/.hermes/state.db` 文件存在。这是 Hermes 的核心数据库。

### Q: API Key 配置好了但对话仍然报错？
A: 点击 ⚙️ 设置按钮，在「可用 Agent」列表中确认 API Key 状态显示 🟢。

### Q: 如何在另一台电脑上使用？
A: 解压 zip 包后，确保有 Python 3.8+ 环境，运行 `pip install -r requirements.txt && python dashboard_api.py`。

### Q: 前端页面无法加载？
A: Dashboard 会自动搜索 HTML 文件位置。你可以将 HTML 文件放到与 `dashboard_api.py` 同目录下。

### Q: 如何自定义 Agent 配置？
A: 编辑 `dashboard_api.py` 中的 `AGENT_DEFS` 和 `AGENT_MODEL_MAP` 字典。

### Q: 支持哪些模型？
A: 当前支持：MiMo V2.5、GLM-5、DeepSeek V4 Flash、Qwen 3.5-27B。可在 `MODEL_META` 中扩展。

## 📁 文件结构

```
hermes-dashboard/
├── dashboard_api.py      # FastAPI 后端（核心）
├── start.sh              # 启动脚本
├── requirements.txt      # Python 依赖
├── README.md             # 本文件
└── (multi-agent-dashboard.html)  # 前端页面（需单独获取或同目录放置）
```

## 📜 许可

MIT License - 自由使用和分发
