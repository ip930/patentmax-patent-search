#!/usr/bin/env python3
"""PatentMax API 客户端 —— 专利检索与统计分析。

给 AI 用的命令行工具。每个子命令往 stdout 吐一份 JSON，出错时 JSON 里带
error 和给人看的中文提示，不抛栈。

几件事在这里处理掉，免得让模型自己踩：

1. **一篇专利 = 一次调用**。`brief` 打的是整篇接口，一次拿回著录 + 权利要求 +
   说明书全文，计一次价。不要为了"只要权项"去单独打 claims —— 那是按字段零售，
   同样的内容会收好几遍钱。

2. **字段名不稳定**。上游同一个东西可能叫 pn、publicationNumber 或 documentNumber，
   pick() 按候选名依次取，取不到返回 None，不让 KeyError 冒出来。

3. **花了多少钱由服务端说了算**。输出里的 `_spent` / `_balance` 全部来自响应头，
   不是本地估算。客户端**不做**任何价格推算——历史上本地估价和实际账单对不上，
   比不给还糟。

依赖：只用标准库，不需要 pip install。

────────────────────────────────────────────────────────────────
v3.0.0 相对 v2 的变化（2026-09-22 服务端改版后）：

· 整篇接口 /api/patent 上线：brief 从「换 id + 取详情 + 取权项 + 取全文」
  四次调用压成一次，单篇从 ¥0.25 降到 ¥0.10。
· **临时 id 那一整套没了**。所有按篇取数的接口现在都直接认公开号（pn），
  换 id 的前置检索由服务端承担、不进用户账单。于是删掉：落盘 id 缓存、
  TTL、404 作废重取、以及散落各处的「传 --id 能省 ¥0.10」提示。
  ——那些机制当初存在的唯一理由就是躲开那笔过路费，过路费取消了，它们
  只剩下复杂度和失效 id 带来的 404。
· 不再输出每条命令的 `_cost` 估算，只输出服务端回的真实 `_spent`。
· 检索和整篇每天前 30 次免费，输出里会带 `_free_remaining`。
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

# 单价（元），跟服务端 defaultPatentDataPrices 对齐。
#
# **只给预算闸门做放行估算用，不打印给模型看。**
# 真实扣费一律以响应头里的 x-patentmax-charged-cents 为准：本地表迟早和服务端
# 走偏（v2 就是这样，表里还写着 claims ¥0.20 而服务端早已是 ¥0.05），
# 而一旦打印出来，模型就会拿这个错数去跟用户交代花了多少钱。
PRICES = {
    "search": 0.10, "patent": 0.10,
    "detail": 0.05, "claims": 0.05, "fulltext": 0.05,
    "legal": 0.05, "citation": 0.05, "similar": 0.10, "img": 0.05,
    "pdf": 0.30, "portrait": 0.30, "ration": 0.30,
}


def die(message, exit_code=1, **extra):
    """出错也走 stdout 的 JSON，让调用方只解析一个地方。

    退出码约定：1 = 参数错/网络错/上游错（改了再试）；
    3 = 预算拦截（不是故障，是"本次任务的钱不够了"，要先问用户）。
    """
    payload = {"error": True, "message": message}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


# 本次进程内累计的实扣、最近一次返回的余额、以及每日免费额度用量（单位：分）
# saw_header 区分"服务端说这次扣了 0 元"和"服务端根本没回这个头"：
# 老版本服务端没有计费头，此时绝不能输出 _spent: ¥0.00 —— 那等于告诉模型本次免费，
# 比不给还糟。只有真收到过头才输出这些字段。
_billing = {"charged_cents": 0, "balance_cents": None, "saw_header": False,
            "free_used": None, "free_quota": None}


def _record_billing(response_headers):
    """从响应头收集计费信息。头缺失时静默跳过——老版本服务端没有这些头。"""
    def as_int(name):
        value = response_headers.get(name)
        return None if value is None else int(value)

    try:
        charged = as_int("x-patentmax-charged-cents")
        if charged is not None:
            _billing["charged_cents"] += charged
            _billing["saw_header"] = True
        balance = as_int("x-patentmax-balance-cents")
        if balance is not None:
            _billing["balance_cents"] = balance
        used, quota = as_int("x-patentmax-free-used"), as_int("x-patentmax-free-quota")
        if used is not None and quota is not None:
            _billing["free_used"], _billing["free_quota"] = used, quota
    except (TypeError, ValueError):
        pass


def billing_summary():
    """附加到每个命令输出里的计费信息。

    三个数全部来自服务端，客户端不做算术：
      _spent          本次命令的合计实扣
      _balance        扣完后的账户余额（权威值）
      _free_remaining 今天还剩几次免费调用
    """
    if not _billing["saw_header"]:
        # 服务端没回计费头（未升级），保持沉默好过报一个编出来的数
        return {}
    out = {"_spent": f"¥{_billing['charged_cents'] / 100:.2f}"}
    if _billing["balance_cents"] is not None:
        out["_balance"] = f"¥{_billing['balance_cents'] / 100:.2f}"
        if _billing["balance_cents"] < 500:
            out["_balance_warning"] = (
                "余额不足 ¥5，继续批量检索会很快耗尽。"
                "请先提醒用户到 https://api.ip930.com/features/api-platform 充值。"
            )
    if _billing["free_quota"]:
        left = max(0, _billing["free_quota"] - (_billing["free_used"] or 0))
        out["_free_remaining"] = f"{left}/{_billing['free_quota']} 次（每日重置，仅检索与整篇）"
    return out


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
            # 服务端每个响应都回本次实扣和扣完后的余额。记下来，供 _spent/_balance 输出。
            # 拿服务端的真值，而不是在本地累加估价：跨进程累加迟早算岔，
            # 而余额是权威的——对账类的数字错一次就再也不被信任。
            _record_billing(response.headers)
            return payload if raw else json.loads(payload.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        hint = {
            400: "参数有问题，检查必填项和取值范围。",
            401: "密钥无效或已撤销。确认 PATENTMAX_API_KEY 是 pm_live_ 或 pm_test_ 开头的完整密钥。",
            402: "余额不足。到 https://api.ip930.com/features/api-platform 充值。",
            403: "该密钥没有此接口的权限。",
            404: "没找到这篇专利。确认公开号正确且带国别代码，如 CN109761224A。",
            409: "任务冲突，可能是重复提交。",
            429: "触发限流。生产密钥 600 次/分，测试密钥每天 30 次。",
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
    """取出结果列表。不同接口的列表字段名不一样。

    2026-09-22 实测修正：相似专利接口把列表放在 **patentLikeList**，
    而这里原来只认 patents/list/data/records/rows —— 于是 `similar` **永远返回 0 条**，
    表现得像"服务端接口不可用"。SKILL.md 又把语义扩展当作检索空转时的救命动作，
    等于那条路一直是断的。

    所以除了列举已知字段名，最后再兜一层：**取第一个"元素是对象"的列表**。
    上游以后再冒出新名字也不会静默返回空。
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for name in ("patents", "patentLikeList", "list", "data", "records", "rows"):
        value = payload.get(name)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
    for value in payload.values():
        if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
            return value
    return []


def normalize(row):
    """把一行结果压成稳定的字段名。

    2026-09-22 实测修正两处一直取不到的字段：

    · **摘要在 `summary` 里，不叫 abstract**（上游压根没有 abstract 这个字段）。
      原来每行返回的 abstract 都是 null —— 而整个「先筛后读」策略的前提就是
      "检索结果自带摘要，靠摘要判断相不相关"。摘要为空时模型只能逐篇取正文，
      这正是费用失控的根因之一。
    · **公开日在 `documentDate` 里**，publicationDate 同样不存在。
    """
    return {
        "patent_number": pick(row, "documentNumber", "publicationNumber", "pn"),
        "title": pick(row, "title", "ti"),
        "applicant": pick(row, "applicant", "currentAssignee", "pa"),
        "inventor": pick(row, "inventor", "pi"),
        "application_date": pick(row, "applicationDate", "ad"),
        "publication_date": pick(row, "documentDate", "publicationDate", "pd"),
        "ipc": pick(row, "mainIpc", "ipc"),
        "type": pick(row, "type", "documentType"),
        "legal_status": pick(row, "legalStatus", "currentStatus"),
        "abstract": pick(row, "summary", "abstract", "ab"),
    }


def patent_number_of(args):
    """取本次要操作的公开号。

    所有按篇取数的接口都直接认公开号，不再需要先换临时 id。
    保留 --id 只为兼容：手上已有内部 id 时可以直接用，价钱一样。
    """
    given = (getattr(args, "id", None) or "").strip()
    if given:
        return {"id": given}
    number = (getattr(args, "patent", None) or "").strip()
    if not number:
        die("请给 --patent（公开号，如 CN109761224A）。")
    return {"pn": number.replace(" ", "").upper()}


def do_search(args):
    payload = request("/api/search", {
        "q": args.q,
        "ds": "cn" if args.scope == "cn" else "all",
        "page": args.page,
        "size": args.size,
        "sort": getattr(args, "sort", None),
        "hl": "1" if getattr(args, "highlight", False) else None,
    })
    rows = [normalize(r) for r in rows_of(payload)]
    total = payload.get("total") if isinstance(payload, dict) else None
    return {
        "query": args.q,
        "total": total if total is not None else len(rows),
        "page": args.page,
        "returned": len(rows),
        "results": rows,
        # 摘要就在检索结果里，这是免费初筛的前提：先读摘要挑出少数几篇，
        # 再对那几篇调 brief。不要拿到一串公开号就逐篇 brief 过去。
        "_note": "每行都带 abstract，先读摘要筛选，只对入围的少数几篇调 brief。",
    }


def do_brief(args):
    """单篇速览：一次调用拿回著录 + 权利要求 + 说明书全文。

    这三块是**打包**返回的，关掉任何一项都不省钱，只是少占上下文。
    真正另外收钱的只有法律事件流水（单独的接口）。
    """
    target = patent_number_of(args)
    payload = request("/api/patent", target)

    result = {"patent_number": args.patent or f"(by id {args.id})"}
    if isinstance(payload, dict):
        basic = payload.get("basic")
        row = rows_of(basic)[0] if rows_of(basic) else (
            basic.get("patent") if isinstance(basic, dict) else None) or basic
        result["basic"] = normalize(row) if isinstance(row, dict) else basic
        if not args.no_claims:
            result["claims"] = payload.get("claims")
        # 说明书全文动辄上万字，默认不回给模型——**不是为了省钱**（它已经在
        # 这次调用里一起取回来了），是为了不把上下文占满。要逐段分析时才开。
        if args.fulltext:
            result["description"] = payload.get("description")
        if payload.get("unavailable"):
            result["_unavailable"] = payload["unavailable"]
    else:
        result["raw"] = payload

    # 法律事件是另一个接口，确实另外收钱
    if args.legal:
        result["legal_events"] = request("/api/legal", target)

    return result


def do_similar(args):
    payload = request("/api/similar", patent_number_of(args))
    rows = [normalize(r) for r in rows_of(payload)]
    # 上游把锚点专利自己排在第一位。留着它占一个名额，还容易让模型把"找到了自己"
    # 当成一条命中写进报告，所以按公开号剔掉。
    anchor = (args.patent or "").replace(" ", "").upper()
    if anchor:
        rows = [r for r in rows if (r.get("patent_number") or "").replace(" ", "").upper() != anchor]
    return {
        "patent_number": args.patent,
        "similar": rows[: args.limit],
        "_note": "语义相似，不是关键词匹配；用词完全不同但方案接近的专利靠它捞回来。",
    }


def do_citation(args):
    payload = request("/api/citation", patent_number_of(args))
    return {
        "patent_number": args.patent,
        "citation": payload,
        "_note": "被引次数常被当作技术影响力的参考，但受公开时间影响，新专利天然偏低。",
    }


def do_figure(args):
    """摘要附图存到本地文件。返回的是图片，不适合直接塞给模型看。"""
    data = request("/api/img", patent_number_of(args), raw=True)
    path = args.out or f"{(args.patent or 'patent').replace(' ', '')}.png"
    try:
        with open(path, "wb") as fh:
            fh.write(data)
    except OSError as exc:
        die(f"图片写入失败：{exc}")
    return {"patent_number": args.patent, "saved_to": os.path.abspath(path),
            "bytes": len(data),
            "_note": "写报告要配图时引用这个路径；图片内容本身不用读进上下文。"}


#: /api/ration 支持的统计维度。每个维度最多返回前 20 项，不返回长尾。
DIMENSIONS = {
    "applicant": "申请人", "inventor": "发明人",
    "applicationYear": "申请年份", "documentYear": "公开年份",
    "ipc": "IPC 完整分类", "ipc1": "IPC 部", "ipc2": "IPC 大类",
    "ipc3": "IPC 小类", "ipc4": "IPC 大组",
    "countryCode": "国家/地区", "province": "省份", "city": "城市",
    "legalStatus": "法律状态", "type": "专利类型", "loc": "外观设计分类",
}


def do_stats(args):
    """按维度做 Top 20 统计。管理层看布局通常先看这个，不是逐篇读专利。"""
    if args.dimension not in DIMENSIONS:
        die(f"未知维度 {args.dimension}。可选：" + "、".join(f"{k}({v})" for k, v in DIMENSIONS.items()))

    payload = request("/api/ration", {"q": args.q, "c": args.dimension, "ds": "cn" if args.scope == "cn" else "all"})

    # analysis_total 有时是数组、有时是 JSON 字符串，两种都得认
    items = payload.get("analysis_total", payload) if isinstance(payload, dict) else payload
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except json.JSONDecodeError:
            items = []
    if not isinstance(items, list):
        items = []

    return {
        "query": args.q,
        "dimension": args.dimension,
        "dimension_name": DIMENSIONS[args.dimension],
        "items": items,
        # 一次只回一个维度，四个维度就是四次调用。这是本客户端最贵的一档，
        # 先想清楚要哪个维度再调，不要逐个维度扫一遍。
        "_note": "只返回前 20 项，不含长尾；申请人未做集团归一化，同一企业可能分散在多行。",
    }


def do_company(args):
    """企业专利画像。名称必须用工商全称，简称匹配不到。"""
    payload = request("/api/a/portrait", {"en": args.name})
    return {
        "company": args.name,
        "portrait": payload,
        "_note": "名称匹配严格，需用工商全称。集团旗下不同主体需分别查询后合并。",
    }


# ── 两道闸门：金额闸 + 检索次数闸 ────────────────────────────
#
# 金额闸只拦钱，不拦命令类型。
#
# 第一版做成了"档位 + 命令白名单"（快速摸底只准 search 5 次），实测是灾难：
# 用户只想查一篇专利，被拦下来问要不要升档；而且上一轮任务用完的额度
# 会把一个全新对话拦死。**预算是用来防失控的，不是用来决定能用哪些功能的。**
#
#   · 没设预算       → 不拦金额
#   · 设了预算       → 每次调用前估价，装得下就放行，装不下才拒
#   · 闲置超 1 小时  → 视为上一个任务已结束，自动失效，绝不影响新任务
#
# ── 检索次数闸（2026-09-22 新增）──
#
# 实测教训：一次任务跑了 **49 次检索**，其中 2 分钟内连着 22 次。
# 起因是语义扩展接口当时有 bug 返回 0 条，agent 只好用同义词硬凑，
# 换了二十几个检索式——每一次都在花钱，而且越换越偏。
#
# 金额闸拦不住这个：单次检索才 ¥0.10，跑到 49 次也才 ¥4.9，多数预算都装得下。
# **失控的形式是"次数"，不是"单价"**，所以要单独数次数。
#
# 这一条是照着竞品的做法补的：他们同样翻过车（752 次调用），结论是
# 「没有执行前预估、没有预算上限、**没有中途检查点**」，三样里我们缺的正是检查点。
#
# 阈值 15：快速摸底规定 4-5 式，标准档 8-10 式，完整档 10-12 式。
# 到 15 次说明要么方向不对、要么该换策略了，继续换同义词是空转。
# 不是硬禁止——拦下来让 agent 去问用户，用户点头后放宽即可。
BUDGET_IDLE_TIMEOUT = 3600
BILLED_COMMANDS = ("search", "stats", "company", "brief", "similar", "citation", "figure")
DEFAULT_SEARCH_LIMIT = 15


def _session_path():
    root = os.environ.get("PATENTMAX_CACHE_DIR") or os.path.join(os.path.expanduser("~"), ".patentmax")
    return os.path.join(root, "budget.json")


def _load_session():
    try:
        with open(_session_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    # 闲置太久就当上一个任务已经结束——陈旧预算拦住新任务是最糟的体验
    if time.time() - float(data.get("touched_at", 0)) > BUDGET_IDLE_TIMEOUT:
        return None
    return data


def _save_session(data):
    try:
        path = _session_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data["touched_at"] = time.time()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
    except Exception:
        pass


def estimate_cost(args):
    """这条命令大概要花多少。**只用于放行判断，不打印**，实际以 _spent 为准。"""
    command = args.command
    if command not in BILLED_COMMANDS:
        return 0.0
    if command == "brief":
        # 整篇一口价，权项和全文都包含在内；只有法律事件是另一次调用
        return PRICES["patent"] + (PRICES["legal"] if getattr(args, "legal", False) else 0.0)
    return PRICES.get(
        {"stats": "ration", "company": "portrait", "figure": "img"}.get(command, command),
        PRICES["search"])


def _counter_session():
    """拿到用于计数的会话；没有就现造一个（不设金额上限）。

    次数闸必须**默认生效**，所以不能依赖用户先跑 `budget start`——
    真实场景里 agent 基本不会主动设预算，而失控恰恰发生在没人设闸的时候。
    """
    session = _load_session()
    if session is None:
        session = {"limit": 0.0, "spent": 0.0, "search_calls": 0,
                   "search_limit": DEFAULT_SEARCH_LIMIT, "started_at": time.time()}
    return session


def enforce_search_budget(args):
    """检索次数闸。退出码 3 = 该停下来问人了（不是故障）。"""
    if args.command != "search":
        return
    session = _load_session()
    if not session:
        return
    used = int(session.get("search_calls", 0))
    limit = int(session.get("search_limit", DEFAULT_SEARCH_LIMIT))
    if limit <= 0 or used < limit:
        return
    die(f"本次任务已经跑了 {used} 次检索，达到上限 {limit} 次。",
        hint="**停下来，先把已有结果交给用户。** 连着换同义词重检说明方向可能不对，"
             "继续跑多半是空转还要花钱。给他三个选择："
             "A 换个思路继续（说清楚打算怎么改检索式）、"
             "B 就用现有候选收尾出报告、"
             "C 到此为止。"
             f"用户同意继续后，跑 `budget start --limit <金额> --searches <更大的数> --force` 放宽。",
        exit_code=3,
        searches_used=used, searches_limit=limit)


def enforce_budget(args):
    """装不下才拒。退出码 3 = 预算不够（不是故障）。"""
    enforce_search_budget(args)
    session = _load_session()
    if not session or args.command not in BILLED_COMMANDS:
        return
    limit = float(session.get("limit", 0))
    spent = float(session.get("spent", 0))
    need = estimate_cost(args)
    if limit <= 0 or spent + need <= limit + 1e-9:
        return
    die(f"本次任务预算 ¥{limit:.2f} 不够了：已用 ¥{spent:.2f}，这一步还要约 ¥{need:.2f}。",
        hint="停下来告诉用户还差多少、继续大概要花多少，让他决定："
             "追加预算（budget start --limit <更大的数>）、缩小范围收尾、或就此结束。"
             "不要自行降级参数硬凑。",
        exit_code=3)


def record_usage(command):
    """记一笔：花了多少钱、跑了第几次检索。

    花费用服务端回的实扣，不用估价——估价和账单对不上时，预算会在错误的时点拦人。
    次数则**无条件记**，哪怕服务端没回计费头、哪怕这次走的是免费额度：
    免费的检索一样会把上下文塞满、一样说明 agent 在空转，次数闸管的是这个。
    """
    session = _counter_session()
    if _billing["saw_header"]:
        session["spent"] = round(
            float(session.get("spent", 0)) + _billing["charged_cents"] / 100, 2)
    if command == "search":
        session["search_calls"] = int(session.get("search_calls", 0)) + 1
    _save_session(session)


def do_budget(args):
    if args.action == "start":
        existing = _load_session()
        # 只有金额上限才算"已有预算"；自动建出来记次数的那种不算，不该挡住用户设预算
        if existing and float(existing.get("limit", 0)) > 0 and not args.force:
            return {"error": True,
                    "message": f"已有进行中的预算：上限 ¥{existing['limit']:.2f}，已用 ¥{existing.get('spent', 0):.2f}。",
                    "hint": "同一个任务继续跑就不用重设；换任务了加 --force 重开，或先 budget clear。"}
        _save_session({"limit": float(args.limit), "spent": 0.0,
                       "search_calls": 0, "search_limit": int(args.searches),
                       "started_at": time.time()})
        return {"limit": f"¥{float(args.limit):.2f}", "spent": "¥0.00",
                "search_limit": int(args.searches),
                "_note": "两道闸：总花费超上限会拦，检索次数超上限也会拦（退出码都是 3）。"
                         "不限制用哪些命令。闲置 1 小时自动失效。"}
    if args.action == "status":
        session = _load_session()
        if not session:
            return {"limit": None, "_note": "当前没有进行中的任务，两道闸都没有计数。"}
        used = int(session.get("search_calls", 0))
        cap = int(session.get("search_limit", DEFAULT_SEARCH_LIMIT))
        out = {"searches": f"{used}/{cap} 次"}
        money = float(session.get("limit", 0))
        if money > 0:
            out["limit"] = f"¥{money:.2f}"
            out["spent"] = f"¥{session.get('spent', 0):.2f}"
            out["remaining"] = f"¥{money - session.get('spent', 0):.2f}"
        else:
            out["limit"] = None
            out["spent"] = f"¥{session.get('spent', 0):.2f}"
            out["_note"] = "没设金额上限，只在数检索次数。"
        return out
    try:
        os.remove(_session_path())
    except Exception:
        pass
    return {"limit": None, "_note": "预算已清除。"}


def add_patent_target(parser):
    """按篇取数的公共参数。公开号直接可用，不需要先检索换 id。"""
    parser.add_argument("--patent", default=None, help="公开号，如 CN109761224A")
    parser.add_argument("--id", default=None, help="内部 id（可选）。手上没有就用 --patent，价钱一样")


def emit(result, json_out):
    """把结果交出去。

    默认打到 stdout；给了 --json-out 就由**本进程**把 UTF-8 字节写进文件，
    stdout 只留一行纯 ASCII 的确认。

    为什么要有这个开关：stdout 捕获的解码方式**不归我们管**。
    Windows PowerShell 5.1 按 `[Console]::OutputEncoding` 解码外部程序输出，
    默认是 GBK；中文 JSON 会被解成乱码，而且是**不可逆的**——非法字节直接被替换掉，
    事后再怎么转码都救不回来。更糟的是：

      · 每条命令常常是一个新的 PowerShell 进程，上一条里设的编码带不过来；
      · `Out-String` 默认按 80 列折行，会往 JSON 里插换行，这跟编码无关。

    让 Python 自己写文件就绕开了整条链路：文件里一定是 UTF-8，调用方直接读文件。
    确认行用 ensure_ascii=True，保证它在任何代码页的控制台上都不会坏。
    """
    if not json_out:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    try:
        directory = os.path.dirname(os.path.abspath(json_out))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        die(f"结果写入失败：{exc}")
    receipt = {"saved_to": os.path.abspath(json_out)}
    for key in ("_spent", "_balance", "_free_remaining"):
        if isinstance(result, dict) and key in result:
            receipt[key] = result[key]
    print(json.dumps(receipt, ensure_ascii=True, indent=2))


def main():
    parser = argparse.ArgumentParser(description="PatentMax 专利检索与统计分析")
    sub = parser.add_subparsers(dest="command", required=True)

    # 所有子命令共用的输出开关。放在 parents 里而不是顶层，
    # 是为了让它能写在子命令**后面**（`search --q ... --json-out x.json`），
    # 顶层参数必须写在子命令前面，那个顺序很容易记错。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json-out", default=None, metavar="PATH",
                        help="把完整 JSON 写到这个文件（UTF-8），stdout 只回一行确认。"
                             "**Windows 上一律用它**——控制台捕获会按 GBK 解码中文，乱码且不可逆")

    p = sub.add_parser("search", parents=[common], help="检索专利（结果自带摘要，用于免费初筛）")
    p.add_argument("--q", required=True,
                   help="检索式。支持 AND/OR/NOT 与括号，也支持字段式："
                        "documentNumber:CN106328959A、t:区块链、legalStatus:有效专利、"
                        "type:发明授权、applicationYear:[2024 TO 2024]")
    p.add_argument("--scope", default="all", choices=["all", "cn"], help="all 全球，cn 仅中国")
    p.add_argument("--page", type=int, default=1, help="1-100")
    p.add_argument("--size", type=int, default=20, help="1-50")
    p.add_argument("--sort", default=None,
                   choices=["relation", "applicationDate", "!applicationDate",
                            "documentDate", "!documentDate", "rank"],
                   help="排序：relation 相关度、applicationDate 申请日升序、"
                        "!applicationDate 申请日降序、documentDate/!documentDate 公开日、rank 综合")
    p.add_argument("--highlight", action="store_true", help="返回关键词高亮标记")
    p.set_defaults(func=do_search)

    p = sub.add_parser("stats", parents=[common], help="按维度做 Top 20 统计（本工具最贵的一档）")
    p.add_argument("--q", required=True, help="检索式，圈定统计范围")
    p.add_argument("--dimension", required=True, help="统计维度，见 --help 列表")
    p.add_argument("--scope", default="all", choices=["all", "cn"])
    p.set_defaults(func=do_stats)

    p = sub.add_parser("company", parents=[common], help="企业专利画像")
    p.add_argument("--name", required=True, help="企业工商全称，简称匹配不到")
    p.set_defaults(func=do_company)

    p = sub.add_parser("brief", parents=[common], help="单篇速览：著录+权项+全文，一次调用一口价")
    add_patent_target(p)
    p.add_argument("--fulltext", action="store_true",
                   help="把说明书全文也回给模型。**不额外计费**（已随本次调用取回），"
                        "但全文很长会占满上下文，只在需要逐段分析时开")
    p.add_argument("--no-claims", action="store_true",
                   help="不回权利要求书。**不省钱**，只是少占上下文")
    p.add_argument("--legal", action="store_true",
                   help="额外取法律事件流水（这项走单独接口，确实另外收费）。"
                        "只想知道有没有失效的话不用开——检索结果里已经带法律状态")
    p.set_defaults(func=do_brief)

    p = sub.add_parser("similar", parents=[common], help="相似专利（语义近似，不是关键词匹配）")
    add_patent_target(p)
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=do_similar)

    p = sub.add_parser("citation", parents=[common], help="引证关系")
    add_patent_target(p)
    p.set_defaults(func=do_citation)

    p = sub.add_parser("figure", parents=[common], help="下载摘要附图到本地文件")
    add_patent_target(p)
    p.add_argument("--out", default=None, help="保存路径，默认 <公开号>.png")
    p.set_defaults(func=do_figure)

    p = sub.add_parser("budget", parents=[common], help="设置/查看本次任务的花费上限")
    p.add_argument("action", choices=["start", "status", "clear"])
    p.add_argument("--limit", type=float, default=5.0, help="本次任务的花费上限（元），默认 5")
    p.add_argument("--searches", type=int, default=DEFAULT_SEARCH_LIMIT,
                   help=f"本次任务的检索次数上限，默认 {DEFAULT_SEARCH_LIMIT}。"
                        "用户同意继续深挖时才调大")
    p.add_argument("--force", action="store_true", help="覆盖已有的进行中预算")
    p.set_defaults(func=do_budget)

    args = parser.parse_args()
    # 预算不够才拦，且拦在调接口之前——被拦的请求不该打上游，更不该计费
    enforce_budget(args)
    result = args.func(args)
    record_usage(args.command)
    # 每个命令的输出都带上权威余额与本次实扣，模型不用自己跨调用累加。
    # 只在返回的是字典时附加——二进制/原始输出不动。
    if isinstance(result, dict):
        result.update(billing_summary())
    emit(result, getattr(args, "json_out", None))


if __name__ == "__main__":
    main()
