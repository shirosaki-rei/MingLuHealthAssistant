# 后端命令与运维

> **什么时候读这个文件**：跑测试、起服务、改模型、动迁移、用 docker。
> **什么时候可以删**：单节条目随情况过期，逐条标了。

## 测试

```bash
cd services/api
.venv\Scripts\python.exe -m pytest                               # 36 个，约 1 秒
.venv\Scripts\python.exe -m pytest tests/test_contract.py -k paywall        # 按名字筛
```

31 个的分布：`test_contract.py` 10（付费墙契约）、`test_architecture.py` 5（依赖规则）、
`test_journey.py` 4（主链路冒烟）、`test_safety.py` 12（红旗规则）。

**付费墙那 10 条是产品红线**，改 `modules/entitlement.py` 前后都必须跑，详见 `redlines.md`。

- **来源**：`contracts/README.md` 建议先写这 12 条（现落地 10 条）。
- **适用**：任何后端改动。
- **过期**：永不。

## 起服务

```bash
cd services/api
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000   # 手机连这个

必须用 .venv 的解释器：main.py 挂了运营后台（sqladmin），系统 python 没这个依赖会 import 失败。
换新机器先 `.venv\Scripts\python.exe -m pip install -r requirements.txt`。
```

默认 `DATABASE_URL` 是 sqlite（`services/api/minglu.db`），**开发不需要 docker**。

`/healthz` 是健康检查端点（`include_in_schema=False`，不进 openapi.json）。

## 迁移

```bash
# 改完 app/models.py 后，重新生成迁移
DATABASE_URL="sqlite:////tmp/empty.db" python -m alembic revision --autogenerate -m "说明"

# 空库验证（务必做，autogenerate 有坑，见下）
DATABASE_URL="sqlite:////tmp/empty.db" python -m alembic upgrade head
python -c "import sqlite3; print(len(sqlite3.connect('/tmp/empty.db').execute(\"select name from sqlite_master where type='table'\").fetchall()))"
# 应该是 25（24 张业务表 + alembic_version）
```

### autogenerate 的两个坑

1. **自定义类型不会自动 import**。`app/database.py` 里的 `IsoDateTime`（TypeDecorator）会被
   生成成 `app.database.IsoDateTime(...)` 但不带 import，`upgrade` 时直接 NameError。
   生成后手动在迁移文件顶部加 `import app.database  # noqa: F401`。
2. **必须对着空库生成**。对着已有数据的库 autogenerate 会产出空迁移。

- **来源**：`migrations/versions/` 曾经是**空的**——`alembic upgrade head` 是空操作，
  而 `main.py` 的 startup 又无条件 `create_all()`，等于「禁止发布脚本跑 DDL」
  这条架构约束从没被遵守过。已修：补了初始迁移 + `create_all` 收窄到 dev。
- **适用**：每次改模型。
- **过期**：永不。

### 非 dev 环境不起迁移就没有表

`Base.metadata.create_all()` 只在 `MINGLU_ENV=dev` 跑。生产/容器必须 `alembic upgrade head`
（compose 里由一次性的 `migrate` 服务先跑完再起 api）。

## docker

```bash
cp .env.example .env          # 填 JWT_SECRET 等
docker compose up --build
```

三段式：`db`（postgres:16-alpine，不对外暴露端口）→ `migrate`（一次性，`alembic upgrade head`）
→ `api`。容器自己不跑 DDL（架构 §7.3）。

**改了 `POSTGRES_PASSWORD` 后若报 `password authentication failed`**：postgres 的 entrypoint
只初始化**空**数据目录，旧 `pgdata` 卷沿用旧密码。开发库 `docker compose down -v` 删卷重来。

- **来源**：实测踩到。卷的创建时间早于容器，就是这个原因。
- **适用**：改密码后。
- **过期**：永不（这是 postgres 镜像的固定行为）。

## 环境变量与生产自检

`MINGLU_ENV=prod` 下启动自检（`app/config.py` 的 `_guard_production`）会拒绝以下配置：

- `JWT_SECRET` 还是默认值 `dev-only-insecure-secret`
- `PAYMENT_MOCK` 还开着（沙箱支付，直接置 paid）
- `SMS_ECHO` 还开着（会把验证码写进服务端日志）

守卫放在 config.py 而**不是** compose 的 `${JWT_SECRET:?...}`——那个写法会让
`docker compose ps`、`down` 全因缺变量失败，stack 就没法运维了。

- **来源**：第一版用了 `${JWT_SECRET:?}`，结果连 `ps` 都跑不了，回退了。
- **适用**：改 compose 或 config 时。
- **过期**：永不。

`MINGLU_ENV` 只对 `prod` 收紧，`test` 环境 conftest 会开 `SMS_ECHO` 和 `PAYMENT_MOCK`，
不该被挡。

## 依赖

`requirements.txt` 里曾经有个 PyPI 上不存在的 `fastapi==0.159.1`，导致 `pip install` 直接失败。
现已修正，且 CI 里加了「`pip install -r requirements.txt` 必须成功」这条兜底。
`psycopg[binary]` 是 compose 走 postgres 才需要的。

## 错误响应的 HTTP 状态码：业务错误也必须非 2xx

2026-10-29 踩的坑，跨前后端，值得单独记：

`entitlement.py` 里 4 处额度检查（对话日限 / 提醒条数 / 角色名额 / 报告月限）都写了
`http_status=200`，初衷是「不阻断：客户端展示三条出口，不是付费墙」。但**客户端
`android/api/http.ts` 的 `request()` 是按 HTTP 状态码判成败的**（契约里错误走
`default`，本来就是非 2xx），于是 200 + 错误体被当成成功对象 resolve：

- 免费用户点「提交审核」→ `createPersona` resolve 出一个错误体 → `created.id` 是
  `undefined` → 弹层静默关闭、列表不出现（第一个现象）；
- 选了头像的 → 接着拿 `undefined` 去上传 → URL 变成 `/v1/personas/undefined/avatar`
  → 服务端 403「角色不存在或无权访问」（第二个现象）。

已修：4 处 `http_status=200` 全删掉，用 `ApiError` 默认的 400。业务码
`PERSONA_QUOTA_EXCEEDED` 照给，客户端按 `ErrorCode` 分支引导会员页（CLAUDE.md 规则 3），
「不是付费墙」指的是别用 402 和付费专用状态，不是让你返 200。

**规则**：新增业务错误一律走 `ApiError(code, message)`，不要手写 `http_status=200`。
要「不阻断」就在客户端侧处理，不在状态码上做文章。
