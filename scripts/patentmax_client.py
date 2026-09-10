#!/usr/bin/env python3
"""PatentMax API 客户端 —— 检索与查新。

给 AI 用的命令行工具。每个子命令往 stdout 吐一份 JSON，出错时 JSON 里带
error 和给人看的中文提示，不抛栈。

几件事在这里处理掉，免得让模型自己踩：

1. **临时 id**。检索返回的 id 只活 60 分钟，详情类接口只认这个 id，不认公开号。
   所以拿公开号查详情必须先跑一次检索换 id —— 这一步封在 resolve() 里，
   并且带进程内缓存，同一个公开号连查权利要求和法律状态不会重复计费。

2. **字段名不稳定**。上游同一个东西可能叫 pn、publicationNumber 或 documentNumber，
   pick() 按候选名依次取，取不到返回 None，不让 KeyError 冒出来。

3. **按次计费**。每个响应都带 _cost 字段说明这次花了多少，查新任务前会先报价。
   报告类一次 ¥15，不能让模型顺手就调。

依赖：只用标准库，不需要 pip install。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Windows 控制台默认按 GBK 解码，中文专利标题会乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = os.environ.get("PATENTMAX_BASE_URL", "https://api.ip930.com").rstrip("/")
API_KEY = os.environ.get("PATENTMAX_API_KEY", "").strip()
TIMEOUT = 60

# 单价（元）。跟服务端 defaultPatentDataPrices 对齐，只用于给出提示，真实扣费以服务端为准。
PRICES = {
    "search": 0.10, "detail": 0.10, "legal": 0.10, "citation": 0.10,
    "similar": 0.10, "img": 0.10, "claims": 0.20, "fulltext": 0.20,
    "pdf": 1.00, "portrait": 1.00, "ration": 1.00, "novelty": 15.00,
}

# 公开号 → 临时 id。进程内有效，避免同一篇专利反复检索换 id。
_id_cache = {}


def die(message, **extra):
    """出错也走 stdout 的 JSON，让调用方只解析一个地方。"""
    payload = {"error": True, "message": message}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(1)


def request(path, params=None, method="GET", body=None, headers=None, raw=False):
    if not API_KEY:
        die("未设置 API 密钥。请先 export PATENTMAX_API_KEY=pm_live_xxx，"
            "密钥在 https://api.ip930.com/features/api-platform 创建。")

    url = f"{BASE_URL}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None and v != ""}
        url += "?" + urllib.parse.urlencode(clean)

    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {API_KEY}")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            payload = response.read()
            return payload if raw else json.loads(payload.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        hint = {
            400: "参数有问题，检查必填项和取值范围。",
            401: "密钥无效或已撤销。确认 PATENTMAX_API_KEY 是 pm_live_ 或 pm_test_ 开头的完整密钥。",
            402: "余额不足。到 https://api.ip930.com/features/api-platform 充值。",
            403: "该密钥没有此接口的权限。",
            404: "资源不存在。若是查详情，多半是临时 id 已过期，重新检索一次。",
            409: "任务冲突，可能是重复提交。",
            429: "触发限流。测试密钥每天 30 次，换生产密钥或稍后再试。",
        }.get(exc.code, "")
        die(f"HTTP {exc.code}：{hint}", detail=detail, url=url.split("?")[0])
    except urllib.error.URLError as exc:
        die(f"网络请求失败：{exc.reason}")
    except json.JSONDecodeError:
        die("响应不是合法 JSON，可能上游异常。")


def pick(row, *names):
    """上游字段名不统一，按候选名依次取第一个非空值。"""
    if not isinstance(row, dict):
        return None
    for name in names:
        value = row.get(name)
        if value not in (None, "", []):
            return value
    return None


def rows_of(payload):
    """检索结果的列表字段有好几种叫法。"""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for name in ("patents", "list", "data", "records", "rows"):
        value = payload.get(name)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
    return []


def normalize(row):
    """把一行检索结果压成稳定的字段名。"""
    return {
        "patent_number": pick(row, "documentNumber", "publicationNumber", "pn"),
        "title": pick(row, "title", "ti"),
        "applicant": pick(row, "applicant", "currentAssignee", "pa"),
        "inventor": pick(row, "inventor", "pi"),
        "application_date": pick(row, "applicationDate", "ad"),
        "publication_date": pick(row, "publicationDate", "pd"),
        "ipc": pick(row, "mainIpc", "ipc"),
        "legal_status": pick(row, "legalStatus", "currentStatus"),
        "abstract": pick(row, "abstract", "ab"),
        "_id": pick(row, "id", "temporaryId"),  # 临时 id，查详情要用
    }


def do_search(args):
    payload = request("/api/search", {
        "q": args.q,
        "ds": "cn" if args.scope == "cn" else "all",
        "page": args.page,
        "size": args.size,
    })
    rows = [normalize(r) for r in rows_of(payload)]
    total = payload.get("total") if isinstance(payload, dict) else None
    return {
        "query": args.q,
        "total": total if total is not None else len(rows),
        "page": args.page,
        "returned": len(rows),
        "results": rows,
        "_cost": f"¥{PRICES['search']:.2f}",
        "_note": "结果里的 _id 是临时标识，60 分钟过期，查详情时用它而不是公开号。",
    }


def resolve(patent_number):
    """公开号 → 临时 id。详情类接口只认 id，所以要先检索换一次。"""
    key = patent_number.replace(" ", "").upper()
    if key in _id_cache:
        return _id_cache[key]

    payload = request("/api/search", {"q": patent_number, "ds": "all", "page": 1, "size": 10})
    rows = rows_of(payload)
    if not rows:
        die(f"没有找到 {patent_number}。确认公开号是否正确，注意要带国别代码，如 CN109761224A。")

    def number_of(row):
        return (pick(row, "documentNumber", "publicationNumber", "pn") or "").replace(" ", "").upper()

    matched = next((r for r in rows if number_of(r) == key), rows[0])
    temporary_id = pick(matched, "id", "temporaryId")
    if not temporary_id:
        die(f"检索到了 {patent_number} 但没拿到临时 id，无法查详情。")

    _id_cache[key] = temporary_id
    return temporary_id


def do_brief(args):
    """单篇速览：一次把要看的几块都取回来，省得模型分好几轮调。"""
    temporary_id = resolve(args.patent)
    result = {"patent_number": args.patent}
    cost = PRICES["search"]  # 换 id 那次检索

    base = request("/api/detail", {"id": temporary_id})
    row = rows_of(base)[0] if rows_of(base) else (base.get("patent") if isinstance(base, dict) else None) or base
    result["basic"] = normalize(row) if isinstance(row, dict) else base
    cost += PRICES["detail"]

    if args.claims:
        result["claims"] = request("/api/claims", {"id": temporary_id})
        cost += PRICES["claims"]
    if args.legal:
        result["legal_events"] = request("/api/legal", {"id": temporary_id})
        cost += PRICES["legal"]
    if args.fulltext:
        result["description"] = request("/api/fulltext", {"id": temporary_id})
        cost += PRICES["fulltext"]

    result["_cost"] = f"¥{cost:.2f}"
    return result


def do_similar(args):
    payload = request("/api/similar", {"id": resolve(args.patent)})
    return {
        "patent_number": args.patent,
        "similar": [normalize(r) for r in rows_of(payload)][: args.limit],
        "_cost": f"¥{PRICES['search'] + PRICES['similar']:.2f}",
    }


def do_citation(args):
    payload = request("/api/citation", {"id": resolve(args.patent)})
    return {
        "patent_number": args.patent,
        "citation": payload,
        "_cost": f"¥{PRICES['search'] + PRICES['citation']:.2f}",
        "_note": "被引次数常被当作技术影响力的参考，但受公开时间影响，新专利天然偏低。",
    }


def do_novelty(args):
    """创建查新任务。这是花钱的操作，默认要 --yes 确认。"""
    if not args.yes:
        die(f"查新任务一次约 ¥{PRICES['novelty']:.2f}，确认要跑请加 --yes。",
            hint="想先免费试跑，把 PATENTMAX_API_KEY 换成 pm_test_ 开头的测试密钥，会立即返回模拟结果且不计费。")

    body = {
        "title": args.title,
        "technical_solution": args.solution,
        "purpose": args.purpose,
        "depth": args.depth,
        "regions": args.regions.split(",") if args.regions else ["global"],
        "legal_status": args.legal_status,
        "source_scope": {"patents": True, "papers": not args.patents_only, "web": not args.patents_only},
    }
    headers = {"Idempotency-Key": args.idempotency_key} if args.idempotency_key else {}
    created = request("/api/v1/novelty/tasks", method="POST", body=body, headers=headers)

    data = created.get("data", created) if isinstance(created, dict) else created
    task_id = pick(data, "task_id", "taskId", "id")
    if not task_id:
        return {"created": created, "_warning": "响应里没找到 task_id，原样返回。"}

    result = {"task_id": task_id, "status": pick(data, "status") or "pending", "_cost": f"¥{PRICES['novelty']:.2f}"}
    if not args.wait:
        result["_next"] = f"轮询状态：python patentmax_client.py novelty-status --task-id {task_id}"
        return result

    return {**result, **poll(task_id, args.timeout)}


def poll(task_id, timeout):
    """轮询任务状态。退避从 5 秒起，最多 30 秒一次 —— 查新要跑几分钟，密集轮询没意义。"""
    started = time.time()
    interval = 5
    while time.time() - started < timeout:
        payload = request(f"/api/v1/novelty/tasks/{task_id}")
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        status = (pick(data, "status") or "").lower()

        if status in ("succeeded", "success", "completed", "done"):
            result = request(f"/api/v1/novelty/tasks/{task_id}/result")
            return {"status": status, "result": result,
                    "_docx": f"python patentmax_client.py novelty-docx --task-id {task_id} --out 查新报告.docx"}
        if status in ("failed", "error", "cancelled", "canceled"):
            return {"status": status, "detail": data}

        time.sleep(interval)
        interval = min(interval * 1.5, 30)

    return {"status": "timeout",
            "message": f"等了 {timeout} 秒还没完成，任务还在跑，不用重新提交。",
            "_next": f"python patentmax_client.py novelty-status --task-id {task_id}"}


def do_novelty_status(args):
    payload = request(f"/api/v1/novelty/tasks/{args.task_id}")
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    status = (pick(data, "status") or "").lower()
    out = {"task_id": args.task_id, "status": status, "detail": data}
    if status in ("succeeded", "success", "completed", "done"):
        out["result"] = request(f"/api/v1/novelty/tasks/{args.task_id}/result")
    return out


def do_novelty_docx(args):
    payload = request(f"/api/v1/novelty/tasks/{args.task_id}/report.docx", raw=True)
    with open(args.out, "wb") as handle:
        handle.write(payload)
    return {"saved": args.out, "bytes": len(payload)}


def main():
    parser = argparse.ArgumentParser(description="PatentMax 专利检索与查新")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="检索专利")
    p.add_argument("--q", required=True, help="检索式，支持 AND / OR / NOT 和括号")
    p.add_argument("--scope", default="all", choices=["all", "cn"], help="all 全球，cn 仅中国")
    p.add_argument("--page", type=int, default=1, help="1-100")
    p.add_argument("--size", type=int, default=20, help="1-50")
    p.set_defaults(func=do_search)

    p = sub.add_parser("brief", help="单篇速览")
    p.add_argument("--patent", required=True, help="公开号，如 CN109761224A")
    p.add_argument("--claims", action="store_true", help="带上权利要求（+¥0.20）")
    p.add_argument("--legal", action="store_true", help="带上法律事件（+¥0.10）")
    p.add_argument("--fulltext", action="store_true", help="带上说明书全文（+¥0.20，很长）")
    p.set_defaults(func=do_brief)

    p = sub.add_parser("similar", help="相似专利")
    p.add_argument("--patent", required=True)
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=do_similar)

    p = sub.add_parser("citation", help="引证关系")
    p.add_argument("--patent", required=True)
    p.set_defaults(func=do_citation)

    p = sub.add_parser("novelty", help="创建查新任务")
    p.add_argument("--title", default="", help="技术方案名称")
    p.add_argument("--solution", required=True, help="技术方案描述，越具体结果越准")
    p.add_argument("--purpose", default="novelty",
                   choices=["novelty", "inventiveness", "pre_filing", "project_screening", "competitor_scan"])
    p.add_argument("--depth", default="standard", choices=["quick", "standard", "deep"])
    p.add_argument("--regions", default="global", help="逗号分隔：global,CN,US,EP,JP,KR,WO")
    p.add_argument("--legal-status", dest="legal_status", default="all",
                   choices=["all", "active", "pending", "inactive"])
    p.add_argument("--patents-only", action="store_true", help="只查专利，不查论文和网页")
    p.add_argument("--wait", action="store_true", help="等到出结果")
    p.add_argument("--timeout", type=int, default=900, help="等待上限（秒）")
    p.add_argument("--idempotency-key", dest="idempotency_key", default=None, help="防重复提交与重复扣费")
    p.add_argument("--yes", action="store_true", help="确认扣费")
    p.set_defaults(func=do_novelty)

    p = sub.add_parser("novelty-status", help="查任务状态")
    p.add_argument("--task-id", dest="task_id", required=True)
    p.set_defaults(func=do_novelty_status)

    p = sub.add_parser("novelty-docx", help="下载查新报告 DOCX")
    p.add_argument("--task-id", dest="task_id", required=True)
    p.add_argument("--out", default="查新报告.docx")
    p.set_defaults(func=do_novelty_docx)

    args = parser.parse_args()
    print(json.dumps(args.func(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
