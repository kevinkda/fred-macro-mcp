# fred-macro-mcp（中文）

只读的 [Model Context Protocol](https://modelcontextprotocol.io)（MCP）
服务器，封装 [FRED](https://fred.stlouisfed.org/)（美联储经济数据，圣路易斯联储）
公共 API。

FRED 是美国宏观经济时序的权威免费来源——GDP、CPI、失业率、政策与市场利率、
国债收益率曲线。本服务器让 LLM agent 在股票/固收研究上叠加宏观背景（macro
overlay）："CPI 走势如何"、"10y-2y 利差在哪"、"下一次非农何时公布"。

> **设计上只读。** 每个工具仅对固定单一 host `https://api.stlouisfed.org`
> 发起 HTTPS `GET`，无任何写入/变更/下单路径。

## 工具

| 工具 | 用途 |
| ---- | ---- |
| `get_series` | 某序列在可选日期窗口内的观测值。 |
| `search_series` | 关键词检索 FRED 目录，找到正确的 `series_id`。 |
| `get_series_latest` | 某序列最新的单个观测值。 |
| `get_release_calendar` | 未来 N 天的 FRED 数据发布日历。 |
| `health_check` | 本地健康探针（key 是否配置？限流？），不调用 FRED。 |
| `get_server_info` | 版本、MCP SDK 版本、工具列表，不调用 FRED。 |

## 安装

```bash
uv sync --extra dev
```

需要一个免费的 [FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html)。
将 `.env.example` 复制为 `.env`，填入 `FRED_API_KEY`。

## 配置 MCP host（如 Cursor）

```json
{
  "mcpServers": {
    "fred-macro": {
      "command": "uv",
      "args": ["run", "fred-macro-mcp"],
      "cwd": "/opt/workspace/code/kevinkda/fred-macro-mcp",
      "env": { "FRED_API_KEY": "<your-fred-key>" }
    }
  }
}
```

## 安全

- **API key 是唯一密钥**：仅从环境变量读取，仅作为绑定查询参数传给 FRED，并在
  所有日志与异常消息中**脱敏**（`api_key=…` 与裸 32 位 key 均被遮蔽）。
- **防 SSRF**：host 为硬编码常量，调用方只提供 endpoint 路径与参数，无法重定向到
  其他 host；不跟随重定向。
- **防注入**：`series_id` 与日期用锚定正则校验，作为绑定查询参数传递，绝不拼接进 URL。
- **限流**：滑动 60 秒令牌桶，保持在 FRED 文档规定的 120 req/min 预算内。

详见 [`docs/SECURITY.md`](./docs/SECURITY.md) 与
[`docs/THREAT_MODEL.md`](./docs/THREAT_MODEL.md)。

## 开发

```bash
uv run pytest --cov=src --cov-fail-under=100
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy --strict src
```

测试使用 [`respx`](https://lundberg.github.io/respx/) mock FRED——测试套件
**不发起任何真实 FRED 调用**。

## 许可

MIT — 见 [`LICENSE`](./LICENSE)。数据版权归圣路易斯联储（FRED）所有，遵循其
[使用条款](https://fred.stlouisfed.org/legal/)。
