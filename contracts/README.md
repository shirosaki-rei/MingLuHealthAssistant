# 接口契约

`openapi.yaml` 是前后端的**唯一真相源**。任何一方要改接口，先改这里，再重新生成两边的类型。

## 为什么要单独放一份

前后端分开开发（前端 uni-agent、后端通用模型）时，最大的风险不是写不出来，是**两边理解不一致**：

- 前端以为 `id` 是 number，后端返回 string
- 前端以为时间是毫秒时间戳，后端给 ISO 字符串
- 前端以为金额是元，后端给的是分

这些问题联调时才暴露，一改就是两边都动。有了这份契约，两边都从它生成代码，不靠记忆对齐。

## 变更记录

| 版本 | 变更 |
|---|---|
| **1.1.0** | 新增 6 个端点（`POST /v1/orders/{id}/pay`、`GET /v1/doctors/{id}`、`POST /v1/preconsult/{id}/confirm`、`GET /v1/memories`、`PATCH`/`DELETE /v1/memories/{id}`、`DELETE /v1/timeline/{id}`、`GET /v1/exports/{token}`）；SSE `done` 补 `conversation_id` 与 `message_id`；新增 `ErrorCode` 枚举与 401 响应；`request_id` 明确走 `X-Request-Id` 响应头；`/v1/config/medical-actions` 从示例改为权威全量清单（31 条） |
| 1.0.0 | 首版 |

### 1.0 → 1.1 为什么要改

不是重设计，是补齐 1.0 里跑不通的地方：

| 缺口 | 后果 | 补法 |
|---|---|---|
| `order.pay` 在白名单里但没有支付端点 | 医生咨询链路断在下单 | 加 `POST /v1/orders/{id}/pay` |
| `memory.read` / `memory.delete` 在白名单里但没有端点 | 违反个人信息可携带与删除要求，小程序端无法合规 | 加 `GET/PATCH/DELETE /v1/memories` |
| `doctor.detail` 在白名单里但没有端点 | 医生详情页、H5 医生名片页没数据 | 加 `GET /v1/doctors/{id}` |
| 预问诊没有「确认病历」这一步 | 状态机永远到不了 `done`，时间线不写预问诊事件 | 加 `POST /v1/preconsult/{id}/confirm` |
| SSE `done` 只有 `request_id` + `usage` | 首轮新建会话后客户端拿不到 `conversation_id`，下一轮必然开新会话 | `done` 补 `conversation_id` + `message_id` |
| 导出的摘要 URL 没有取回端点 | H5 分享页无处取内容 | 加公开的 `GET /v1/exports/{token}`（token 鉴权，7 天失效） |
| 错误码只举了一个例子 | 前端没法按 code 分支，只能匹配中文 message | 加 `ErrorCode` 全集枚举（23 个） |
| `medical-actions` 是 `example` 不是权威清单 | 客户端本地预判无从对齐 | 改为 31 条全量清单，且由契约测试断言与代码一致 |

### 两处约定收紧

- **`request_id` 的位置**：成功响应保持裸业务对象（不加信封），`request_id` 走 `X-Request-Id`
  响应头；错误响应体里带 `request_id` 字段。两端都能拿到。
- **时间精度**：统一到秒。契约示例是 `2026-10-22T09:00:00+08:00`，后端不再输出微秒。

## 全局约定（摘要，完整见文件头）

| 项 | 约定 |
|---|---|
| 时间 | ISO 8601 带时区，如 `2026-10-22T09:00:00+08:00` |
| ID | string（UUID） |
| 金额 | integer，**单位是分** |
| 分页 | `page` + `page_size`，响应 `total` + `has_more` |
| 错误 | `{ code, message, request_id }`，`code` 是业务码不是 HTTP 状态 |

## 生成前端类型

在 uni-app x 工程根目录（`android/`，`@/` 指向工程根目录，没有 `src/`）：

```bash
npx openapi-typescript@7 ../contracts/openapi.yaml -o types/api.ts
```

> **必须是 `.ts`，不能是 `.d.ts`**：uni-app x 的 uts 管线能把 `@/types/api` 解析到
> `.d.ts`，但会把它当 JavaScript 解析，直接 `Expected '{', got 'interface'` 编译失败。
> 已生成：`android/types/api.ts`（openapi-typescript 7.13.0）。

> 注意 `ErrorCode` 是 `components/schemas` 里的枚举，生成后 `Error.code` 会收窄成字面量联合类型，
> 前端按它分支即可，不要匹配 `message`（文案会改）。

生成的类型直接用于：

```ts
import type { components } from '@/types/api'

type Patient = components['schemas']['Patient']

const res = await uni.request<Patient>({ url: '/v1/patients/xxx' })
```

## 生成后端模型

```bash
pip install datamodel-code-generator
datamodel-codegen \
  --input openapi.yaml \
  --output app/schemas/api.py \
  --input-file-type openapi \
  --output-model-type pydantic_v2.BaseModel
```

生成的 Pydantic 模型可以直接用在 FastAPI 里，路由声明与契约天然一致。

## 校验

```bash
npx @redocly/cli lint openapi.yaml
python -c "import yaml;yaml.safe_load(open('openapi.yaml',encoding='utf-8'))"
```

CI 里加这两步：lint 查规范，parse 查语法（本地没装 redocly 时至少能跑通 parse）。

后端还有一组契约测试会直接读这个文件，所以「yaml 改了但代码没跟上」会在
`services/api` 的 `python -m pytest` 里红：

| 测试 | 断言 |
|---|---|
| `test_architecture.py::test_contract_action_list_matches_code` | `medical-actions` 的清单与版本号必须与代码里的 `MEDICAL_ACTIONS` 完全一致 |
| `test_contract.py::test_paywall_misblock_rate_is_zero` | 白名单里的 action 在未开通会员下全部返回成功 |

## 改接口的流程

1. 改 `openapi.yaml`
2. 跑 lint
3. 重新生成前后端类型
4. 两边各自适配编译错误
5. 更新 `info.version`（1.0.1、1.1.0 这样递增）

**不要**跳过第 3 步手工改生成出来的类型文件——下次重新生成就被覆盖了。

## 两个 OpenAPI 描述不了的地方

### 1. SSE 流式接口

`POST /v1/chat/stream` 的响应是 `text/event-stream`，OpenAPI 只能标 content type，描述不了事件流。事件格式写在接口的 `description` 里：

```
event: delta
data: {"delta":"先别急着下结论。"}

event: redflag
data: {"level":"urgent","message":"...","action":"call_120"}

event: done
data: {"request_id":"...","usage":{"in":2100,"out":320}}
```

客户端实现以这段为准，不要自己发明事件名。

**关键约束**：一个 `delta` 至少是一个完整句子。服务端按句缓冲后再推，客户端拿到即渲染，不需要自己拼句——这是「后置校验能生效」的前提。

### 2. 就医路径白名单

`GET /v1/config/medical-actions` 返回的 action 列表，服务端永远放行。客户端可以缓存它做本地预判（少一次网络往返），但**最终判定必须在服务端**——客户端的预判结果不能作为是否放行的依据，否则被改了就能绕过付费墙。

## 契约测试（建议先写这 12 条）

付费墙是产品红线，光靠代码 review 守不住。建议后端先写这组测试，全部断言 HTTP 200：

| # | 用例 |
|---|---|
| 1 | 未开通会员：预问诊 start / answer / finish |
| 2 | 未开通会员：科室匹配 |
| 3 | 未开通会员：医生列表与详情 |
| 4 | 未开通会员：下单与支付 |
| 5 | 会员已过期：同上全部 |
| 6 | 会员被灰产封禁：同上全部 |
| 7 | 未登录：应返回 401（这是唯一该拒绝的） |
| 8 | 限额用尽：对话仍可进行（限额只限轮数，不限功能） |
| 9 | 就诊人满 6 个：返回明确业务码，不是 500 |
| 10 | 红旗命中：无论会员状态都弹干预卡 |
| 11 | 时间字段：全部 ISO 8601，无毫秒时间戳 |
| 12 | 错误响应：必含 code / message / request_id |

已实现：`services/api/tests/test_contract.py` 里 12 条全部落地，另加
`test_journey.py`（主链路冒烟）与 `test_architecture.py`（架构 §2.2 的依赖规则 +
契约与代码的白名单一致性）。运行 `cd services/api && python -m pytest`。
