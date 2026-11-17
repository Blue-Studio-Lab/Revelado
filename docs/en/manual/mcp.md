# MCP: use Revelado from Claude Code or Claude Desktop

*[Español](../../es/manual/mcp.md)*

`revelado-mcp` is a small [MCP](https://modelcontextprotocol.io) server that lets **your own** Claude Code or Claude Desktop drive the Revelado engine running on your computer.

On this route your Claude does the judging, so Revelado needs **no API key**. The engine still does all the image work locally, under the same one-heavy-job rule.

## Install

- **Claude Code (plugin):**
  ```text
  /plugin marketplace add Blue-Studio-Lab/Revelado
  /plugin install revelado
  ```
- **Claude Desktop:** download the `.mcpb` bundle from the latest release and open it.
- **Any MCP client:** `uvx revelado-mcp` (stdio).

Start the engine (`revelado`) first. Then tell the server which folders it may read (`REVELADO_ALLOWED_DIRS`) and where it may export (`REVELADO_EXPORT_DIRS`). It refuses everything else. Full configuration is in [mcp/README.md](../../../mcp/README.md).

## Tools

Engine status, list/open folders, list photos, photo info, preview image, diagnose (noise/CA/shading), rate/flag, technical auto-cull, list film looks, get/patch the working recipe, develop one sample, develop batch, job status, cancel job, export files.

## Try asking

- "Open the session in ~/Pictures/MyShoot and pick the best 150; explain every reject."
- "Develop one sample with Ektar 100 and soft grain, then show me before and after."

**Terms.** Revelado is not an Anthropic product. You install the server in your own client, which uses your own account; follow Anthropic's terms and usage policies.
