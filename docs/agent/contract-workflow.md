# 契约驱动工作流

> **什么时候读这个文件**：要加/改/删任何接口；或前后端字段类型对不上；或重新生成前端类型。
> **什么时候可以删**：永不（契约是前后端唯一真相源）。但「常见错误」一节可以随踩坑补充。

`contracts/openapi.yaml` 是前后端**唯一真相源**。前后端分两边开发（前端 uni-agent、后端通用模型），
最大的风险不是写不出来，是两边理解不一致——前端以为 `id` 是 number，后端返回 string。
有了契约，两边都从它生成代码，不靠记忆对齐。

---

## 改接口的五步

1. 改 `contracts/openapi.yaml`，`info.version` 递增（1.0.1 / 1.1.0 这样）
2. `npx @redocly/cli lint contracts/openapi.yaml`
3. 重新生成前端类型：
   ```bash
   cd android && npx openapi-typescript@7 ../contracts/openapi.yaml -o types/api.ts
   ```
   ⚠️ 输出必须是 `.ts` 不是 `.d.ts`，原因见 `vapor-mode.md` 第 1 条。
4. 改 `android/api/index.ts` 里对应那一行（28 个接口一行一个，与 yaml 路径一一对应）
5. 两边各自适配编译错误

**不要手改 `android/types/api.ts`**——生成产物，下次重新生成就被覆盖了。

- **来源**：`contracts/README.md` 明文要求。
- **适用**：每次契约变更。
- **过期**：永不。

---

## 全局约定（写死在 yaml 文件头）

| 项 | 约定 | 违反的后果 |
|---|---|---|
| 时间 | ISO 8601 **带时区**，如 `2026-10-22T09:00:00+08:00` | 不用毫秒时间戳 |
| ID | **string（UUID）** | 不用自增 int，避免前端把 id 当数字运算 |
| 金额 | **integer，单位是分** | 不用浮点，避免 0.1+0.2 问题 |
| 分页 | 请求 `page`（从 1 开始）+ `page_size`（默认 20 上限 100）；响应 `total` + `has_more` | — |
| 错误 | 统一 `{code, message, request_id}`，**业务 code 与 HTTP 状态分离** | 同一个 code 可能对应 200/400/409 |
| 请求追踪 | 成功响应带 `X-Request-Id` 响应头；错误响应体里带 `request_id` | 前端报障时读响应头 |

**前端按 `ErrorCode` 枚举分支，不要匹配 `message`**——文案会改。
`android/api/http.ts` 的 `ApiFailure.code` 就是收窄后的字面量联合类型（多一个 `NETWORK`
表示客户端自己造的错，没拿到响应体）。

### 可选字段运行时可能是 `null`，生成的类型只标了 `?`

openapi-typescript 把 `birth_date?: string` 生成成可选，但服务端真的会返回 `null`
（`Patient.birth_date` 实测就是）。于是 `x !== undefined` 挡不住 `null`，
`null.trim()` / `null.length` 直接抛异常——**在 uvue 里这会把整个组件更新打断，
界面冻在旧状态、怎么点都没反应**，极难定位。

- **来源**：用户报「切换时无法选中已有就诊人」。排查到最后是 `ageOf(p.birth_date)` 里
  `null.trim()` 抛错，渲染中断，选中状态根本没画出来。跟选择器、`find`、响应式全都无关。
- **适用**：任何读契约可选字段的地方。
- **过期**：永不（这是 openapi-typescript 对 nullable 的处理方式，除非换生成器）。
- **做法**：用 `android/utils/blank.ts` 的 `hasText` / `hasList` / `hasNum`，
  别在页面里逐个写 `=== undefined && !== ''`。新增页面读可选字段一律走这三个函数。

- **来源**：`contracts/README.md` 的「为什么要单独放一份」。
- **适用**：任何接口字段设计。
- **过期**：永不。

---

## 一致性核对

后端实现与契约漂移是静默的。跑这条对得上才算数：

```bash
cd services/api && python -m uvicorn app.main:app --port 8011 &
python - <<'PY'
import json, urllib.request, yaml
live = json.load(urllib.request.urlopen('http://127.0.0.1:8011/openapi.json'))
spec = yaml.safe_load(open('contracts/openapi.yaml', encoding='utf-8'))
def ops(d): return {(m.upper(), p) for p, i in d['paths'].items() for m in i if m in ('get','post','patch','delete','put')}
print('live:', len(ops(live)), 'yaml:', len(ops(spec)), '差集:', ops(spec) ^ ops(live)))
PY
```

当前基线：**32 path / 37 operation 零差异**。
⚠️ 别把 path 数和 operation 数混为一谈——第一轮盘点就写成过「37/37 接口覆盖」，数字对但口径错。

- **来源**：`docs/上线前缺口清单.md` 第七节。
- **适用**：契约变更后、发版前。
- **过期**：永不。

---

## OpenAPI 描述不了的两处

### SSE 流式接口

`POST /v1/chat/stream` 响应是 `text/event-stream`，OpenAPI 只能标 content type。
事件格式写在 yaml 里该接口的 `description`，**客户端以那段为准，不要自己发明事件名**：

```
event: delta    data: {"delta":"先别急着下结论。"}
event: redflag  data: {"level":"urgent","message":"...","action":"call_120"}
event: done     data: {"request_id":"...","conversation_id":"...","message_id":"...","usage":{...}}
```

关键约束：
- **一个 `delta` 至少是一个完整句子**，服务端按句缓冲后才推。客户端收到即渲染，
  **不需要自己拼句**——这是后置校验能生效的前提。
- 命中红旗：先推 `redflag` 再推 `done`，**不继续生成正文**。
- 每轮 `done` 都返回 `conversation_id` / `message_id`。首轮（请求未带 `conversation_id`）
  客户端**必须**用返回的续下一轮，否则会开新会话。

### 就医路径白名单

`GET /v1/config/medical-actions` 返回的 action 服务端永远放行。客户端可缓存做本地预判
（少一次网络往返），但**最终判定必须在服务端**，否则客户端被改了就能绕过付费墙。
详见 `redlines.md`。
