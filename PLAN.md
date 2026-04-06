# TravelClaw 生产化路线图

> 目标：将最小化AI旅行规划团队骨架，分5个阶段演进为可生产部署的服务。

## 架构演进总览

```
Phase 1 (核心健壮性)
  │
  ├──→ Phase 2 (API服务化)
  │       │
  │       └──→ Phase 3 (持久化与记忆)
  │
  └──→ Phase 4 (质量与测试) ← 与Phase 2并行推进
          │
          └──→ Phase 5 (容器化部署) ← 依赖Phase 2+3
```

---

## Phase 1 — 核心健壮性

**目标：** 让现有流程可靠运行，不改变外部接口。

### 1a. Pydantic数据模型 + 输入校验

| 项目 | 说明 |
|------|------|
| **新建** | `src/travelclaw/models.py` |
| **修改** | `agent.py`, `orchestrator.py` |

- 用Pydantic v2替换现有`@dataclass`（`Message`, `TaskResult`）
- 新增模型：`TravelRequest`（用户输入校验）、`TaskDecomposition`（LLM输出校验）、`PlanResponse`（最终输出包装，含plan_id/耗时/使用的agent列表）
- 为Phase 2的FastAPI做准备（Pydantic模型直接用作请求/响应Schema）

### 1b. 错误处理 + 重试机制

| 项目 | 说明 |
|------|------|
| **新建** | `src/travelclaw/errors.py`, `src/travelclaw/retry.py` |
| **修改** | `agent.py`, `orchestrator.py` |

- 异常层级：`TravelClawError` → `AgentError` / `DecompositionError` / `DispatchError` / `IntegrationError`
- 重试装饰器：对`Agent.chat()`和Orchestrator的3次LLM调用加指数退避重试
- 处理 `anthropic.RateLimitError`、`APIConnectionError`、`APIStatusError`
- `decompose_tasks()`中JSON解析失败时，发修复prompt让LLM重新输出

### 1c. 结构化日志

| 项目 | 说明 |
|------|------|
| **新建** | `src/travelclaw/logging_config.py` |
| **修改** | `orchestrator.py`, `main.py`, `agent.py` |

- 替换所有`print()`为`logging`调用
- 每次`plan()`生成唯一`plan_id`作为关联ID贯穿全链路
- 支持JSON格式输出（生产环境）和人类可读格式（开发环境）

### 1d. 异步并行调度

| 项目 | 说明 |
|------|------|
| **修改** | `agent.py`, `orchestrator.py`, `team.py`, `main.py` |

- `Agent`新增`achat()` + `aexecute_task()`，使用`anthropic.AsyncAnthropic`
- `Orchestrator.adispatch_tasks()`用`asyncio.gather()`并行调度所有专家
- 加`asyncio.Semaphore`控制并发数（默认3），防止触发API限流
- 保留同步方法作为向后兼容
- `Team`新增`aplan()`，`main.py`用`asyncio.run()`调用

**依赖新增：** `pydantic>=2.0`, `tenacity>=8.0`

**关键决策：** 双sync/async接口。同步方法保留给脚本和CLI简单场景，异步方法作为API服务的主路径。

---

## Phase 2 — API服务化

**目标：** 将团队暴露为Web服务，支持REST + WebSocket流式输出。

### 2a. FastAPI应用

| 项目 | 说明 |
|------|------|
| **新建** | `src/travelclaw/api/__init__.py` |
| **新建** | `src/travelclaw/api/app.py` |
| **新建** | `src/travelclaw/api/routes.py` |

REST端点：
- `POST /api/v1/plan` — 提交旅行需求，返回完整行程
- `GET /api/v1/agents` — 列出团队成员
- `GET /api/v1/health` — 健康检查
- `GET /api/v1/plan/{plan_id}` — 查询已完成行程（Phase 3实现后可用）

架构要点：
- 应用工厂 `create_app(config_path)` → FastAPI实例
- Lifespan handler中初始化`TravelTeam`到`app.state`
- 全局异常处理器将`TravelClawError`映射为HTTP状态码

### 2b. WebSocket流式进度

| 项目 | 说明 |
|------|------|
| **新建** | `src/travelclaw/api/ws.py` |
| **修改** | `orchestrator.py` |

- `WS /api/v1/plan/stream` — 实时推送规划进度
- 将`aplan()`重构为**async generator**，yield `PlanEvent`事件：

```python
async for event in orchestrator.aplan_stream(request, agents):
    # event: {"type": "decompose_done", "tasks": [...]}
    # event: {"type": "agent_done", "agent": "food", "result": "..."}
    # event: {"type": "plan_complete", "plan": "..."}
    await websocket.send_json(event)
```

**关键决策：** async generator模式 > callback模式。一套抽象同时支持WebSocket/SSE/CLI进度显示。

**依赖新增：** `fastapi>=0.110`, `uvicorn[standard]>=0.27`, `websockets>=12.0`

**修改 `main.py`：** 新增 `--serve` 参数启动uvicorn服务

---

## Phase 3 — 持久化与记忆

**目标：** 存储行程历史、Agent会话记忆、用户偏好。

### 3a. 数据库层

| 项目 | 说明 |
|------|------|
| **新建** | `src/travelclaw/storage/__init__.py` |
| **新建** | `src/travelclaw/storage/database.py` |
| **新建** | `src/travelclaw/storage/models.py` |
| **新建** | `src/travelclaw/storage/repository.py` |
| **新建** | `alembic.ini` + `alembic/` |

数据表设计：

```
Trip:            plan_id, user_id, request, final_plan, destination, days, budget, status, timestamps
AgentResult:     result_id, plan_id(FK), agent_id, domain, content, duration_ms
ConversationMsg: message_id, plan_id(FK), agent_id, role, content, timestamp
UserPreference:  user_id, key, value
```

### 3b. Agent会话记忆

| 项目 | 说明 |
|------|------|
| **修改** | `agent.py` |

- `Agent`新增`memory_backend`参数（`"in_memory"` / `"persistent"`）
- 持久模式下从数据库加载/保存会话历史
- 上下文窗口管理：prompt中只保留最近N条，完整历史存数据库

### 3c. 用户偏好

| 项目 | 说明 |
|------|------|
| **修改** | `api/routes.py`, `orchestrator.py` |

- `GET/PUT /api/v1/user/{user_id}/preferences`
- Orchestrator的`decompose_tasks()`自动注入用户偏好到prompt

**关键决策：** 开发用SQLite（零配置），生产用PostgreSQL。SQLAlchemy async engine统一抽象。用SQLModel（SQLAlchemy + Pydantic）减少样板代码。

**依赖新增：** `sqlmodel>=0.0.16`, `aiosqlite>=0.19`, `alembic>=1.13`

---

## Phase 4 — 质量与测试

**目标：** 全面测试覆盖 + CI流水线。与Phase 2并行推进。

### 4a. 测试基础设施

| 项目 | 说明 |
|------|------|
| **新建** | `tests/conftest.py` |
| **新建** | `tests/unit/test_agent.py` |
| **新建** | `tests/unit/test_orchestrator.py` |
| **新建** | `tests/unit/test_team.py` |
| **新建** | `tests/unit/test_models.py` |
| **新建** | `tests/unit/test_api.py` |
| **新建** | `tests/integration/test_plan_flow.py` |
| **新建** | `tests/integration/test_persistence.py` |

测试策略：
- **Mock层级：** 在Anthropic Client层mock，不在HTTP层。`FakeAnthropicClient`返回预设响应
- **单元测试：** Agent的chat/history/reset、Orchestrator的decompose/dispatch/integrate、Pydantic模型校验、API端点
- **集成测试：** 完整plan流程（mock LLM）、持久化round-trip（内存SQLite）

### 4b. CI/CD流水线

| 项目 | 说明 |
|------|------|
| **新建** | `.github/workflows/ci.yml` |
| **新建** | `ruff.toml` |

```yaml
# 触发：push to main, 所有PR
# Jobs:
#   lint:       ruff check + ruff format --check
#   type-check: mypy --strict src/
#   test:       pytest --cov=travelclaw --cov-report=xml
#   build:      docker build (验证Phase 5)
```

**依赖新增（dev）：** `pytest>=8.0`, `pytest-asyncio>=0.23`, `pytest-cov>=4.0`, `httpx>=0.27`, `ruff>=0.3`, `mypy>=1.9`

---

## Phase 5 — 容器化部署

**目标：** Docker化 + 环境配置 + 健康检查 + 监控。

### 5a. Docker

| 项目 | 说明 |
|------|------|
| **新建** | `Dockerfile` |
| **新建** | `docker-compose.yml` |
| **新建** | `.dockerignore` |

- 多阶段构建，`python:3.12-slim`基础镜像，非root用户
- docker-compose: `app`（TravelClaw API）+ `db`（PostgreSQL）
- Volume挂载 `config.yaml` 和 `agents/` 目录

### 5b. 统一环境配置

| 项目 | 说明 |
|------|------|
| **新建** | `src/travelclaw/config.py` |
| **修改** | `team.py`, `orchestrator.py`, `agent.py` |

用Pydantic `BaseSettings`集中管理：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `ANTHROPIC_API_KEY` | (必填) | API密钥 |
| `DATABASE_URL` | `sqlite+aiosqlite:///travelclaw.db` | 数据库连接 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `LOG_FORMAT` | `text` | 日志格式(json/text) |
| `MAX_CONCURRENT_AGENTS` | `3` | 并行调度上限 |
| `RETRY_MAX_ATTEMPTS` | `3` | 重试次数 |
| `CONFIG_PATH` | `config.yaml` | 团队配置路径 |

### 5c. 健康检查与监控

| 项目 | 说明 |
|------|------|
| **新建** | `src/travelclaw/middleware.py` |
| **修改** | `api/routes.py`, `api/app.py` |

- `GET /api/v1/health/live` — 存活探针（Kubernetes）
- `GET /api/v1/health/ready` — 就绪探针（检查DB + API连通性）
- 请求计时中间件 + `X-Request-ID` header
- 可选：Prometheus metrics（`prometheus-fastapi-instrumentator`）

**依赖新增：** `pydantic-settings>=2.0`

---

## 文件变更汇总

### 新建文件（按阶段）

| Phase | 新文件 |
|-------|--------|
| 1 | `models.py`, `errors.py`, `retry.py`, `logging_config.py` |
| 2 | `api/__init__.py`, `api/app.py`, `api/routes.py`, `api/ws.py` |
| 3 | `storage/__init__.py`, `storage/database.py`, `storage/models.py`, `storage/repository.py`, `alembic.ini`, `alembic/` |
| 4 | `tests/` 全部, `.github/workflows/ci.yml`, `ruff.toml` |
| 5 | `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `config.py`, `middleware.py` |

### 核心文件修改热度

| 文件 | 被修改的Phase |
|------|---------------|
| `orchestrator.py` | 1, 2, 3, 5 (最热) |
| `agent.py` | 1, 3, 5 |
| `team.py` | 1, 5 |
| `main.py` | 1, 2 |
| `pyproject.toml` | 1, 2, 3, 4, 5 |

---

## 5个关键架构决策

1. **Pydantic v2统一数据层** — 同时服务于：输入校验、API Schema（FastAPI）、环境配置（BaseSettings）
2. **Async Generator调度模式** — `aplan()`以yield事件的方式输出进度，一套抽象支持WebSocket/SSE/CLI
3. **双Sync/Async接口** — 简单脚本用同步，API服务用异步，不强制async
4. **Client层Mock测试** — 注入`FakeAnthropicClient`而非mock HTTP，既快又覆盖真实代码路径
5. **SQLite开发/PostgreSQL生产** — SQLAlchemy统一抽象，CLI零配置可用，部署连真实数据库
