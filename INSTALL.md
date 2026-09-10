# 安装与配置

## 第一步：拿密钥

到 [api.ip930.com/features/api-platform](https://api.ip930.com/features/api-platform) 注册，创建一个 API 密钥。

注册即送 ¥10，第一次调用成功后再送 ¥40，合计 ¥50，按检索 ¥0.10/次算够跑 500 次。不用绑卡。

密钥形如 `pm_live_` 开头的一串字符，**只在创建时显示一次**，复制好。

## 第二步：配置 MCP

服务端点：

```
https://api.ip930.com/api/mcp
```

传输方式是 Streamable HTTP，支持协议版本 `2025-06-18`、`2025-03-26`、`2024-11-05`。

### Claude Desktop / Claude Code

编辑配置文件，加一段：

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

配置文件位置：

- macOS `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows `%APPDATA%\Claude\claude_desktop_config.json`
- Claude Code 用 `claude mcp add` 命令，或改项目根目录的 `.mcp.json`

改完重启客户端。

### Cursor

设置里找 MCP Servers，新建一个，类型选 HTTP，地址和请求头同上。

### 只能填地址的客户端

有些客户端只给一个 URL 输入框，填不了请求头。把密钥挂在查询参数上：

```
https://api.ip930.com/api/mcp?apikey=pm_live_你的密钥
```

> 这种方式密钥会出现在 URL 里，可能被日志记录。能填请求头就优先用请求头。

### OAuth 2.1 一键授权

支持标准的 MCP 授权流程，不用手动复制密钥：

客户端拿不带凭据的请求打过来，会收到 401 和这样一个响应头：

```
WWW-Authenticate: Bearer realm="patentmax-mcp",
  resource_metadata="https://api.ip930.com/.well-known/oauth-protected-resource"
```

顺着它读到授权服务器元数据，走标准授权码 + PKCE 流程，用户在浏览器点一下确认即可。

支持的规范：RFC 6749 授权码与客户端凭据、RFC 7009 撤销、RFC 7591 动态客户端注册、RFC 8414 授权服务器元数据、RFC 9728 受保护资源元数据。

## 第三步：验证接通了

在客户端里问一句：

```
帮我查一下固态电池电解质相关的专利
```

能返回带公开号的结果就说明通了。也可以直接让它调 `patent_search`，关键词填 `固态电池`。

## 排查

**返回 401**

- 检查 `Authorization` 的值有没有 `Bearer ` 前缀，少了这个前缀会被判为格式错误
- 注意：只要请求里带了 `Authorization` 头，URL 上的 `?apikey=` 就不会被读取。两种方式选一种，别混用
- 密钥可能已被撤销或过期，到控制台看一眼状态

**找不到工具 / 工具列表是空的**

- 确认配置文件改对了位置，且客户端已重启
- 有些客户端要手动在设置里把这个 server 打开
- 先单独跑 `initialize` 和 `tools/list` 看握手是否成功

**返回额度不足**

- 到 [控制台](https://api.ip930.com/features/api-platform)看余额
- 体验额度的第二笔（¥40）要第一次调用成功后才到账，如果一直没到，确认第一次调用确实返回了 200

**统计类接口报无权限**

- `tech_landscape` 用的是统计类接口，单价 ¥1.00，确认余额够
- 如果余额充足仍然报错，把错误码发给我们

## 计费

| 调用 | 单价 |
| --- | --- |
| 检索、详情、法律状态、引证、相似专利、附图 | ¥0.10 |
| 权利要求、说明书全文 | ¥0.20 |
| PDF 全文、企业画像、领域统计 | ¥1.00 |

按次扣费，调用失败不扣。用量和流水在控制台可查。
