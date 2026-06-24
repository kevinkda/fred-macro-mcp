# Registering `fred-macro-mcp` with an MCP host

## Cursor (`~/.cursor/mcp.json`)

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

## Claude Desktop (`claude_desktop_config.json`)

Same shape under `mcpServers`. Restart the host after editing.

## Notes

- A free FRED API key is required:
  <https://fred.stlouisfed.org/docs/api/api_key.html>. Set it via the `env`
  block above or in a `.env` file in `cwd`.
- The server is read-only; it never writes to FRED or to your account.
- `health_check` reports whether the key is configured without sending it
  anywhere — call it first to diagnose setup.
