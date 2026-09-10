---
name: patentmax-patent-search
description: 全球专利检索与分析。当用户提到专利检索、查新、现有技术、相似专利、引证分析、专利布局、竞品专利、技术趋势、专利家底，或直接给出一个专利号、公开号、技术方案描述时使用。Use when the user asks to search patents, check prior art, find similar patents, analyze citations, or profile a company's patent portfolio. Requires the PatentMax MCP server.
version: "1.0.1"
user-invocable: true
argument-hint: "[专利号 / 关键词 / 技术方案描述 / 公司名]"
---

# 全球专利检索

数据来自 iprdb 全球专利库，覆盖 CN / US / EP / JP / KR / WIPO，中国专利收录最完整。

## 前置检查

先确认 `patentmax` MCP 服务器已接入。工具调用返回 401 或找不到工具，说明没配好——把 [INSTALL.md](INSTALL.md) 的配置步骤给用户，不要改用网页搜索凑答案。

**任何一条专利信息都必须来自工具返回值。** 不确定就说不确定，不要根据记忆补公开号、申请日、申请人或法律状态。

## 五个工具

| 工具 | 输入 | 返回 |
| --- | --- | --- |
| `patent_search` | 关键词或布尔式，`scope`、`size` | 公开号、标题、申请人、申请日、IPC、法律状态 |
| `patent_brief` | `patent_number` | 著录项目、摘要、权利要求、法律事件、摘要附图 |
| `similar_patents` | `patent_number`、`limit` | 语义相似专利，按相似度降序 |
| `patent_citation` | `patent_number` | 引用、被引、非专利文献 |
| `tech_landscape` | `mode=field` + `query`，或 `mode=company` + `company` | 统计分布，`dimension` 控制维度 |

`patent_brief` 默认不返回说明书全文，需要时用 `include_description`；`include_claims`、`include_legal` 同理。

## 路由

| 用户想要 | 怎么走 |
| --- | --- |
| 找某个主题的专利 | `patent_search` → 挑相关的 → `patent_brief` 细看 |
| 判断一个想法是否新颖 | `patent_search` 拿候选 → `similar_patents` 比对 → `patent_brief` **看权利要求** |
| 看懂一件专利 | `patent_brief` → `patent_citation` → `similar_patents` |
| 某领域谁在申请、趋势如何 | `tech_landscape` `mode=field` |
| 某公司的专利家底 | `tech_landscape` `mode=company` |

## 执行要点

**检索别只跑一轮。** 技术主题拆成「对象 + 手段 + 效果」三个面，各扩同义词，先宽后窄跑两三轮。结果过千说明太宽，加 IPC 或时间限定；不足十条先怀疑同义词没扩够，而不是下「没有相关专利」的结论。检索式写法见 [reference/search-syntax.md](reference/search-syntax.md)。

**查新看权利要求，不看摘要。** 摘要读着像不等于落进保护范围，要把技术特征逐条比。

**法律状态必须交代。** 授权、驳回、撤回、届满、无效意义完全不同。失效专利不构成侵权障碍，但仍是有效的现有技术。

**统计前先提醒口径。** 申请人名称未做集团归一化，同一家企业可能以中文名、英文名、子公司名分散在榜单不同位置。查跨国集团时分主体查询再合并。

## 输出规范

检索结果不要只甩列表，交代三件事：

1. 用了什么检索式、覆盖什么范围——让用户能判断要不要补检
2. 每条为什么相关——一句话说清它和用户的方案在哪一点重合
3. 结论的边界——只能证明「检索范围内找到了什么」，不能证明「不存在」

表格列：公开号、标题、申请人、申请日、法律状态、相关点。

## 边界

不出具正式查新报告、FTO 意见书、无效检索报告——那些需要代理人签字，本 Skill 只做检索与分析。

涉及侵权、无效、许可的判断，提示用户以各国专利局官方登记簿为准。

给查新结论时说明：专利从申请到公开有约 18 个月窗口期，这期间的申请检索不到。
