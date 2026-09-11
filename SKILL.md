---
name: patentmax-patent-search
description: 全球专利检索与技术方案查新。当用户提到专利检索、查新、现有技术、新颖性、相似专利、引证分析、专利布局、竞品专利、可专利性，或直接给出一个专利号、公开号、技术方案描述时使用。Use when the user asks to search patents, check novelty or prior art, find similar patents, or analyze patent citations. Requires a PatentMax API key.
version: "1.0.3"
user-invocable: true
argument-hint: "[专利号 / 检索式 / 技术方案描述]"
allowed-tools: Bash, Read, Write, WebFetch
---

# 全球专利检索与查新

> **让 AI 真的会查专利 —— 接的是真专利库，不是让它凭印象编。**

问 AI「这个方案有人申请过吗」，它答得头头是道，专利号却是编的；找个免费的查中国专利，权利要求和法律状态全是空的。

这个 Skill 基于 PatentMax 专利数据接口，把一个真实的全球专利库接到 AI 手里。**用日常语言描述需求即可**——Agent 会自己完成检索式构造、分轮收窄、候选筛选与证据核对，交付一份带检索式、带公开号、**可逐条复核**的结果。查得到就是查得到，查不到就说查不到。

## 能做什么

**专利检索**

- 关键词与布尔式检索，支持 `AND` / `OR` / `NOT` 与括号分组
- 单篇速览：著录项目、摘要、权利要求书、法律事件流水、摘要附图
- 语义相似专利：找出用词完全不同、技术方案却雷同的那些，关键词检索会漏掉这类
- 引证分析：引了谁、被谁引用、引了哪些论文与标准

**技术方案查新**

- 给一段技术方案描述，返回对比文件、相似度分析与查新结论
- 可按查新目的（新颖性 / 创造性 / 申请前查新 / 立项筛查 / 竞品扫描）、检索深度、地域、法律状态调整
- 可选择是否把论文与网页计入现有技术——查新的现有技术不限于专利
- **直接导出 Word 报告**，立项材料、申请前自查可以拿来就用

## 数据

来自 iprdb 全球专利库，覆盖中国、美国、欧洲、日本、韩国和 WIPO。**中国专利收录最完整**——权利要求、法律事件流水、摘要附图都有，不是只给个标题就完事。

## 怎么开始

到 [api.ip930.com/features/api-platform](https://api.ip930.com/features/api-platform) 注册创建密钥，设成环境变量 `PATENTMAX_API_KEY` 即可，一分钟的事。

**新用户注册赠送体验额度**，够把检索跑透。也可以先申请一把 `pm_test_` 测试密钥免费试跑：检索类每天 30 次，查新返回模拟结果，全程不计费，参数拼对了再换生产密钥。

只用 Python 标准库，不需要 `pip install`；走标准 HTTP 接口，任何能执行命令的 AI 客户端都能用。

> 同一套数据也提供 **MCP 接入**——端点 `https://api.ip930.com/api/mcp`，Streamable HTTP，支持 OAuth 2.1 一键授权，可直接接进 Claude、Cursor、扣子等任何兼容 Model Context Protocol 的客户端。这个 Skill 走 HTTP 接口是为了不挑客户端，两条路都通。

配置与排查见 [INSTALL.md](INSTALL.md)，计费明细见 [references/faq.md](references/faq.md)。

---

以下是给 AI 看的操作说明。

## 开工前

确认环境变量 `PATENTMAX_API_KEY` 已设置：

```bash
echo ${PATENTMAX_API_KEY:0:8}
```

输出 `pm_live_` 或 `pm_test_` 说明配好了。空的就把 [INSTALL.md](INSTALL.md) 的步骤给用户，**不要改用网页搜索凑答案**。

`pm_test_` 是测试密钥：检索类每天 30 次，查新会立即返回模拟结果且不计费。想让用户先免费试试就用它。

**每一条专利信息都必须来自接口返回值。** 不确定就说不确定——不要凭记忆补公开号、申请日、申请人或法律状态。编一个格式正确的公开号出来，比查不到糟得多。

## 两种调用方式

优先用脚本，它把临时 id、字段名不统一、任务轮询这些都处理掉了：

```bash
python scripts/patentmax_client.py --help
```

环境里没有 Python，就直接 curl，接口清单见 [references/api-reference.md](references/api-reference.md)。

## 路由

| 用户想要 | 走哪条 |
| --- | --- |
| 找某个主题的专利 | [检索工作流](references/search-workflow.md) |
| 判断一个想法有没有人做过、能不能申请 | [查新工作流](references/novelty-workflow.md) |
| 看懂某一件专利 | `brief` → `citation` → `similar`，见[检索工作流](references/search-workflow.md#吃透一件专利) |
| 找技术方案相近的专利 | `similar` |
| 要一份正式查新报告文档 | [查新工作流](references/novelty-workflow.md#导出-docx) |

## 常用命令

```bash
# 检索
python scripts/patentmax_client.py search --q "(固态电池 OR 全固态电池) AND 电解质" --size 20

# 单篇速览（带权利要求和法律状态）
python scripts/patentmax_client.py brief --patent CN109761224A --claims --legal

# 相似专利
python scripts/patentmax_client.py similar --patent CN109761224A --limit 10

# 引证关系
python scripts/patentmax_client.py citation --patent CN109761224A

# 查新（会扣费，必须加 --yes）
python scripts/patentmax_client.py novelty --title "阀门卡滞诊断装置" \
  --solution "采集阀门转轴角度与启闭扭矩，根据扭矩增量判断卡滞并控制振动装置清理" \
  --purpose pre_filing --wait --yes
```

## 花钱的地方

检索类一次 ¥0.10，权利要求和全文 ¥0.20，**查新任务一次约 ¥15**。

检索随便跑，查新不行。跑查新之前必须：

1. 告诉用户这次大约花多少
2. 等用户明确说要跑
3. 确认技术方案描述已经写够细——描述太笼统等于白花钱

用户只是想"看看有没有类似的"，用检索加相似专利就够了，不要动查新。

## 几条硬规矩

**查新看权利要求，不看摘要。** 摘要读着像不等于落进保护范围，要把技术特征逐条比。

**法律状态必须交代。** 授权、驳回、撤回、届满、无效意义完全不同。失效专利不构成侵权障碍，但仍然是有效的现有技术。

**「没搜到」不等于「没有」。** 专利从申请到公开有约 18 个月窗口期，这期间的申请谁也查不到。给查新结论时要讲明白。

**临时 id 会过期。** 检索返回的 `_id` 只活 60 分钟，隔了很久再查详情会 404，重新检索一次即可。脚本里已经带了缓存和自动换取，用 curl 的话要自己处理。

## 结果怎么交付

不要只甩列表，交代三件事：

1. 用了什么检索式、覆盖什么范围——让用户能判断要不要补检
2. 每条为什么相关——一句话说清它和用户的方案在哪一点重合
3. 结论的边界——只能证明「检索范围内找到了什么」，不能证明「不存在」

表格列：公开号、标题、申请人、申请日、法律状态、相关点。

## 边界

不出具有法律效力的 FTO 意见书、无效检索报告——那需要代理人签字。查新报告可以生成，但要提示用户正式场合仍需专业机构复核。

涉及侵权、无效、许可的判断，提示用户以各国专利局官方登记簿为准。
