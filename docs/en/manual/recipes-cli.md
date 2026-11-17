# Recipes and CLI

*[Español](../../es/manual/recetas-cli.md)*

## Recipes

Every setting is a **recipe**: a small JSON document. The same recipe on the same RAW, with the same engine version, gives the same pixels. Each developed photo keeps its *resolved* recipe in a sidecar, including the automatic values that were measured (exposure, detections, healed spots), so it can be reproduced later.

```json
{
  "film": "kodak_ektar_100", "film_strength": 1.0, "exposure_ev": 0.0, "grain": 1.0,
  "skin_retouch": 0, "blemishes": 0, "hair": "off", "accent": 40,
  "reframe": "suggest", "sharpen": 1.0, "nr": "auto", "ca": "auto"
}
```

The engine publishes the schema (ranges, defaults, labels) at `GET /api/recipe/schema`. Values out of range are clamped. Unsupported values are accepted and listed as ignored in the job log.

## Command line (v0.1)

`revelado` starts the engine:

```bash
revelado --host 127.0.0.1 --port 8790
```

Scripting goes through the local HTTP API. Every request needs the token from `engine/.runtime_token`, sent in the `X-Revelado-Token` header:

```bash
TOKEN=$(cat engine/.runtime_token)
curl -s -H "X-Revelado-Token: $TOKEN" http://127.0.0.1:8790/api/health
curl -s -H "X-Revelado-Token: $TOKEN" -H 'Content-Type: application/json' \
     -d '{"path": "/path/to/session"}' http://127.0.0.1:8790/api/library/open
```

Batch commands (`revelado develop`, `revelado export`) that take a recipe file are planned. Until then, the [MCP server](mcp.md) is the most convenient way to automate a session.
