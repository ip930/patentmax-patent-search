# 安装与配置

只有两步：拿密钥、设环境变量。没有配置文件要改，不用重启客户端。

## 第一步：拿密钥

到 [api.ip930.com/features/api-platform](https://api.ip930.com/features/api-platform) 注册，创建一个 API 密钥。

注册即送 ¥10，第一次调用成功后再送 ¥40，合计 ¥50——按检索 ¥0.10/次算够跑 500 次。不用绑卡。

密钥有两种：

| 前缀 | 用途 |
| --- | --- |
| `pm_live_` | 生产密钥，真实调用，按次扣费 |
| `pm_test_` | 测试密钥，检索类每天 30 次，查新返回模拟结果，**全程不计费** |

**建议先拿测试密钥跑通流程**，确认参数拼得对、结果结构符合预期，再换生产密钥。

密钥**只在创建时显示一次**，复制好。

## 第二步：设环境变量

**macOS / Linux / Git Bash**

```bash
export PATENTMAX_API_KEY="pm_live_你的密钥"
```

**Windows PowerShell**

```powershell
$env:PATENTMAX_API_KEY="pm_live_你的密钥"
```

**Windows CMD**

```cmd
set PATENTMAX_API_KEY=pm_live_你的密钥
```

> `export` / `set` 只对当前终端会话有效，换个窗口要重设。想永久生效，写进 `~/.bashrc`、`~/.zshrc`，或 Windows 的系统环境变量。

验证一下：

```bash
echo ${PATENTMAX_API_KEY:0:8}
```

输出 `pm_live_` 或 `pm_test_` 就对了。

## 第三步：跑一次看看

```bash
python scripts/patentmax_client.py search --q "固态电池 AND 电解质" --size 5
```

返回带公开号的 JSON 就说明通了。

`scripts/patentmax_client.py` **只用 Python 标准库**，Python 3.7 以上都能跑，不需要 `pip install` 任何东西。

## 没有 Python 怎么办

直接 curl，接口清单和示例见 [references/api-reference.md](references/api-reference.md)。

先测连通性（这个接口不需要密钥）：

```bash
curl -s "https://api.ip930.com/api/v1/health"
```

再测鉴权：

```bash
curl -s -H "Authorization: Bearer $PATENTMAX_API_KEY" \
  --data-urlencode 'q=固态电池' --data-urlencode 'size=5' \
  -G "https://api.ip930.com/api/search"
```

> 检索式里有中文和括号时，务必用 `--data-urlencode -G`，别手工拼 URL。

用 curl 要自己处理一件事：**检索返回的 `id` 是临时标识，60 分钟过期**，详情类接口只认它，不认公开号。想查某件专利的详情，得先用公开号跑一次检索换 id。脚本里这一步是自动的。

## 各客户端

**Claude Code / Cursor / 任何能跑 Bash 的客户端** — 设好环境变量就能用，Skill 会自己调脚本。

**Claude Desktop** — 需要开启命令执行能力。不方便的话，让它按 [api-reference.md](references/api-reference.md) 用 WebFetch 直接请求也行。

**其他 Agent 平台** — 只要能执行 shell 或发 HTTP 请求即可。

**也可以走 MCP** — 同一套数据提供 MCP 接入，端点 `https://api.ip930.com/api/mcp`（Streamable HTTP），支持 OAuth 2.1 一键授权：

```json
{
  "mcpServers": {
    "patentmax": {
      "url": "https://api.ip930.com/api/mcp",
      "headers": { "Authorization": "Bearer pm_live_你的密钥" }
    }
  }
}
```

MCP 提供检索类的五个工具，查新任务走本 Skill 的脚本。两条路用同一把密钥、同一个余额。

## 出问题

先看 [references/faq.md](references/faq.md)，那里按现象列了排查步骤。

最常见的三个：

- **401** — 密钥复制不全，或 curl 时漏了 `Bearer ` 前缀
- **404** — 查详情时临时 id 过期了，重新检索一次
- **429** — 测试密钥每天 30 次用完了，换生产密钥

## 计费

| 调用 | 单价 |
| --- | --- |
| 检索、详情、法律状态、引证、相似专利 | ¥0.10 |
| 权利要求、说明书全文 | ¥0.20 |
| 查新任务（含 DOCX 报告） | 约 ¥15 |

按次扣费，**调用失败不扣**。用量和流水在[控制台](https://api.ip930.com/features/api-platform)可查。

脚本每次返回都带 `_cost` 字段提示这次花了多少。查新是花钱的操作，必须显式加 `--yes` 才会执行。
