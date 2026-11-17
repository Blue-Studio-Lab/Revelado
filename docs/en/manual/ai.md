# AI options

*[Español](../../es/manual/ia.md)*

Revelado works **fully without AI**. AI is optional, off by default, and always paid with *your* account.

| Route | Setup | Who pays |
|---|---|---|
| **No AI** | nothing | nobody |
| **Anthropic API key** | create a key at [platform.claude.com](https://platform.claude.com) and put it in `engine/.env` as `ANTHROPIC_API_KEY=…` (the file is never committed; the key is never shown back in the browser) | your Console account, per use |
| **MCP** | add the Revelado MCP server to your Claude Code or Claude Desktop ([MCP](mcp.md)) | your Claude plan; no API key in Revelado |

**About plans.** Claude **Pro does not include API access**. Max, Team and Ultra plans include monthly API credits that you claim from your plan's billing settings and link to a Console organisation. *Plans and credits change: verify on platform.claude.com.* Revelado cannot see whether credits were claimed.

**What is sent (API route).**

- **Sent:** downscaled JPEG previews (≤ 512 px for the pre-filter, ≤ 1024 px plus one face crop for finalists) with **all EXIF removed**, labelled with opaque ids such as `p0001`.
- **Never sent:** file names, folder paths, GPS, serial numbers, RAW files or client names.

You consent per run, see the estimated cost first, and a budget cap stops the job when it is reached.

**What AI decides.** Claude *proposes*: picks, rejects with reasons, recipe adjustments. Revelado's local rules clamp every value to safe ranges, and you accept or reject the result.
