# 接口速查

环境里没有 Python 时直接用这些。有 Python 的话优先用 `scripts/patentmax_client.py`——临时 id、字段名归一、任务轮询它都处理好了。

**Base URL** `https://api.ip930.com`
**认证** 所有接口都要 `Authorization: Bearer <API_KEY>`

---

## 一个必须先懂的机制：临时 id

`/api/search` 返回的每条结果里有个 `id`，这是**临时标识，60 分钟过期**。

详情类接口（detail / claims / fulltext / legal / citation / similar）**只认这个 id，不认公开号**。

所以想查某件专利的详情，必须先用公开号跑一次检索拿到 id：

```bash
# 1. 用公开号检索，从结果里取 id
curl -s -H "Authorization: Bearer $PATENTMAX_API_KEY" \
  "https://api.ip930.com/api/search?q=CN109761224A&ds=all&size=10"

# 2. 用拿到的 id 查详情
curl -s -H "Authorization: Bearer $PATENTMAX_API_KEY" \
  "https://api.ip930.com/api/detail?id=<上一步的 id>"
```

字段名不统一：公开号可能叫 `documentNumber`、`publicationNumber` 或 `pn`；临时 id 可能叫 `id` 或 `temporaryId`。取值时按候选名依次试。

结果列表也有好几种叫法：`patents` / `list` / `data` / `records`。

---

## 检索

### GET /api/search — 全球专利检索

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `q` | 是 | 检索式，支持 `AND` / `OR` / `NOT` 和括号，最长 4000 字符 |
| `ds` | 否 | `all` 全球（默认）、`cn` 仅中国 |
| `page` | 否 | 1–100，默认 1 |
| `size` | 否 | 1–50，默认 20 |

```bash
curl -s -H "Authorization: Bearer $PATENTMAX_API_KEY" \
  --data-urlencode 'q=(固态电池 OR 全固态电池) AND 电解质' \
  --data-urlencode 'ds=all' --data-urlencode 'size=20' \
  -G "https://api.ip930.com/api/search"
```

> 检索式里有中文和括号，一定用 `--data-urlencode -G`，别手工拼 URL。

**¥0.10 / 次**

### 详情类

以下都是 `GET`，都只接受一个 `id` 参数（临时 id，不是公开号）：

| 接口 | 返回 | 单价 |
| --- | --- | --- |
| `/api/detail` | 著录项目：标题、申请人、发明人、申请日、公开日、IPC、法律状态 | ¥0.10 |
| `/api/claims` | 权利要求书 | ¥0.20 |
| `/api/fulltext` | 说明书全文（很长） | ¥0.20 |
| `/api/legal` | 法律事件流水 | ¥0.10 |
| `/api/citation` | 引用与被引用，含非专利文献 | ¥0.10 |
| `/api/similar` | 语义相似专利 | ¥0.10 |

```bash
curl -s -H "Authorization: Bearer $PATENTMAX_API_KEY" \
  "https://api.ip930.com/api/claims?id=<temporary_id>"
```

---

## 查新

### POST /api/v1/novelty/tasks — 创建任务

**约 ¥15 / 次。** 异步执行，先返回 task_id，再轮询。

```bash
curl -s -X POST "https://api.ip930.com/api/v1/novelty/tasks" \
  -H "Authorization: Bearer $PATENTMAX_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: my-unique-key-001" \
  -d '{
    "title": "阀门卡滞诊断与清理装置",
    "technical_solution": "采集阀门转轴角度、启闭扭矩及前后水位，根据扭矩增量和积雪含水状态判断卡滞，并控制振动装置清理。",
    "purpose": "pre_filing",
    "depth": "standard",
    "regions": ["global"],
    "date_range": {"type": "all"},
    "legal_status": "all",
    "source_scope": {"patents": true, "papers": true, "web": true}
  }'
```

| 字段 | 必填 | 取值 |
| --- | --- | --- |
| `technical_solution` | **是** | 技术方案描述，越具体结果越准 |
| `title` | 否 | 方案名称 |
| `purpose` | 否 | `novelty` \| `inventiveness` \| `pre_filing` \| `project_screening` \| `competitor_scan` |
| `depth` | 否 | `quick` \| `standard` \| `deep` |
| `regions` | 否 | `["global"]` 或 `["CN","US","EP","JP","KR","WO"]` |
| `legal_status` | 否 | `all` \| `active` \| `pending` \| `inactive` |
| `source_scope` | 否 | `{"patents":true,"papers":true,"web":true}` |

返回 `201`，body 里 `data.task_id` 是任务号。

> **`Idempotency-Key` 建议一定带上。** 网络重试时它能防止重复建任务、重复扣 ¥15。

### GET /api/v1/novelty/tasks/{task_id} — 查状态

```bash
curl -s -H "Authorization: Bearer $PATENTMAX_API_KEY" \
  "https://api.ip930.com/api/v1/novelty/tasks/<task_id>"
```

`data.status` 走 `pending` → `running` → `succeeded` / `failed`。

**轮询间隔从 5 秒起逐步退避到 30 秒**，查新要跑几分钟，密集轮询没有意义。

### GET /api/v1/novelty/tasks/{task_id}/result — 取结构化结果

状态 `succeeded` 之后调。

### GET /api/v1/novelty/tasks/{task_id}/report.docx — 下载报告

返回二进制流，直接落盘：

```bash
curl -s -H "Authorization: Bearer $PATENTMAX_API_KEY" \
  "https://api.ip930.com/api/v1/novelty/tasks/<task_id>/report.docx" \
  -o 查新报告.docx
```

不额外收费，包含在那 ¥15 里。

---

## 健康检查

```bash
curl -s "https://api.ip930.com/api/v1/health"
```

**不需要密钥**，用来确认网络通不通。

---

## 状态码

| 码 | 含义 | 怎么办 |
| --- | --- | --- |
| 400 | 参数有问题 | 检查必填项和取值范围 |
| 401 | 密钥无效或已撤销 | 确认密钥完整、`Bearer ` 前缀没漏 |
| 402 | 余额不足 | 去控制台充值 |
| 403 | 该密钥无此接口权限 | 换密钥或联系支持 |
| 404 | 资源不存在 | 查详情时多半是临时 id 过期，重新检索 |
| 409 | 任务冲突 | 多半是重复提交，用同一个 Idempotency-Key 重试是安全的 |
| 429 | 限流 | 测试密钥每天 30 次；生产密钥稍后重试 |

调用失败不扣费。

---

## 测试密钥

`pm_test_` 开头的密钥：

- 检索类接口每天 30 次
- 查新任务**立即返回模拟结果**，不调用外部服务，不计费

拿来验证参数拼得对不对最合适，确认无误再换 `pm_live_`。
