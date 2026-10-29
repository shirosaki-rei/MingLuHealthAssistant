# services/api · 明路后端

FastAPI + SQLAlchemy 2 + Pydantic v2。接口契约见 [`../../contracts/openapi.yaml`](../../contracts/openapi.yaml)，
架构依据见 [`../../docs/明路_架构设计_v1.0.md`](../../docs/明路_架构设计_v1.0.md)。

**改接口先改 yaml**，再改这里的 schemas 与路由。契约是唯一真相源。

## 跑起来

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
# 交互文档 http://127.0.0.1:8000/docs
```

首次启动自动建表并写种子数据（科室映射、10 位演示医生、11 条红旗规则、5 条知识词条、4 个预设角色）。

本地开发默认 `sqlite:///minglu.db`，不需要装 PostgreSQL，也不用设 `MINGLU_ENV`（默认 `dev`）。

上生产用：

```bash
# Linux / macOS / Git Bash
export DATABASE_URL=postgresql+psycopg://user:pass@host/minglu
export MINGLU_ENV=prod JWT_SECRET=<32+ 字节随机串> SMS_ECHO=false PAYMENT_MOCK=false
alembic upgrade head     # 生产迁移走 Alembic，不在启动脚本里跑 DDL
```

```bat
:: Windows CMD
set DATABASE_URL=postgresql+psycopg://user:pass@host/minglu
set MINGLU_ENV=prod
set JWT_SECRET=<32+ 字节随机串>
set SMS_ECHO=false
set PAYMENT_MOCK=false
alembic upgrade head
```

```powershell
# Windows PowerShell
$env:DATABASE_URL="postgresql+psycopg://user:pass@host/minglu"
$env:MINGLU_ENV="prod"; $env:JWT_SECRET="<32+ 字节随机串>"
$env:SMS_ECHO="false"; $env:PAYMENT_MOCK="false"
alembic upgrade head
```

> Windows 上 `VAR=value command` 这种前置赋值不成立（那是 shell 语法），必须用 `set` 或 `$env:`。

验证码：dev 下 `SMS_ECHO=1` 会把验证码写进服务端日志（`[dev] 验证码 phone=... code=...`）。
**生产必须关掉**，并且这条日志本身就该删掉。

## 测试

```bash
python -m pytest
```

三组，对应三份不同的验收要求：

| 文件 | 覆盖 |
|---|---|
| `tests/test_safety.py` | 红旗召回 100%、注入拦截、输出校验、资历表述、脱敏、按句缓冲 |
| `tests/test_contract.py` | 契约 README 的 12 条，核心是「就医路径在任何会员状态下都返回 200」 |
| `tests/test_journey.py` | 主链路：问 → 问清 → 找医生 → 支付 → 沉淀时间线 → 导出摘要 |

## 三条不能破的红线

### 1. 就医路径不可付费墙

PRD §1.4。任何「用户能不能看上病」的路径永久免费。

```python
# app/modules/entitlement.py
MEDICAL_ACTIONS = frozenset({...})   # 唯一真相源
```

每个对外接口都必须显式声明 action：

```python
@router.post("/v1/preconsult/start")
def start(..., _: None = Depends(require_action("preconsult.start"))):
```

`require_action` 拿到白名单里的 action 就是空操作；非白名单动作按注册顺序跑
额度/频控检查（`register_checks`）。

**启动自检**（`main.py::_assert_all_actions_declared`）遍历所有 `/v1/*` 路由，
漏声明 action 或 action 拼错 → `RuntimeError`，进程起不来。所以「漏配一个路由」
和「action 拼错导致付费墙被绕过」都是启动即失败，不是线上事故。

新增接口时如果启动失败报 action 未知，去 `MEDICAL_ACTIONS` 或 `register_checks`
里补，别把自检关掉。

### 2. 红旗不依赖模型

`app/modules/safety.py` 是纯规则，不 import `platform.llm`。模型全挂时红旗照样生效。
红旗检测在意图识别**之前**（`chat.py::_orchestrate`）——否则「我胸痛，挂什么科」
会先进科室匹配，延迟急救引导。

改红旗规则时跑 `tests/test_safety.py`：正例（必须命中）+ 负例（不该触发）两组都在。

### 3. 模型不出境

`config.ALLOWED_LLM_BASE_URLS` 是硬编码白名单。运行时从环境变量注入白名单外的
base_url 会被 `OpenAICompatProvider` 直接拒绝并降级。要加新厂商走评审改这个集合。

## 目录

```
app/
├── main.py             装配 + 启动自检 + request_id 中间件
├── config.py           环境变量、境内模型白名单
├── database.py         引擎/会话/Base、IsoDateTime 列类型
├── deps.py             鉴权、分页、require_action
├── errors.py           统一错误体 {code, message, request_id}
├── models.py           全库表定义
├── schemas.py          请求/响应模型（与 yaml 一一对应）
├── seed.py             种子数据
├── platform/           llm（模型网关）、storage（对象存储）
└── modules/            identity profile persona chat preconsult match
                        consult reminder health memory entitlement safety knowledge
```

`modules` 之间不跨模块查表，只调对方的 service 函数（`profile.get_owned_patient`、
`preconsult.get_owned_session`、`consult.get_owned_order`）。架构 §2.2 的依赖规则，
上 CI 时用 import-linter 静态检查。

## 已知的偷懒（带 `ponytail:` 注释）

| 位置 | 现状 | 什么时候必须换 |
|---|---|---|
| `entitlement._hit` | 频控在内存 dict 里 | 多实例部署时换 Redis INCR，接口不变 |
| `knowledge.retrieve` | 全表扫 + 子串匹配 | 词条上几千条时换 pg_bigm GIN，打分逻辑不动 |
| `health._ocr` | 未接入，返回空 → 标 failed | 接境内 OCR，只替换这一个函数 |
| `consult.pay_order` | `PAYMENT_MOCK=true` 直接置 paid | 生产必须关，改由支付渠道异步回调推进 |
| `consult.sweep_expired_orders` | 全表扫 | 日单量上千后按 created_at/paid_at 加范围索引 |
| `platform/llm` | 未配 LLM_BASE_URL 时用确定性本地模板 | 配好境内模型即切走，调用方不用改 |
| `chat._extract_memories` | 关键词抽取 | 上小模型抽取前，误抽由用户确认卡兜住 |
