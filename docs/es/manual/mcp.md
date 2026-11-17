# MCP: usar Revelado desde Claude Code o Claude Desktop

*[English](../../en/manual/mcp.md)*

`revelado-mcp` es un servidor [MCP](https://modelcontextprotocol.io) que deja que **tu propio** Claude Code o Claude Desktop maneje el motor de Revelado de tu ordenador. Con esta vía no hace falta clave de API: tu Claude juzga y el motor hace el trabajo de imagen en local.

## Instalación

- **Claude Code (plugin):** `/plugin marketplace add Blue-Studio-Lab/Revelado` y después `/plugin install revelado`.
- **Claude Desktop:** descarga el paquete `.mcpb` de la última versión y ábrelo.
- **Cualquier cliente MCP:** `uvx revelado-mcp`.

Primero arranca el motor (`revelado`). Después indica qué carpetas puede leer el servidor (`REVELADO_ALLOWED_DIRS`) y dónde puede exportar (`REVELADO_EXPORT_DIRS`); rechaza todo lo demás. Los detalles están en [mcp/README.md](../../../mcp/README.md).

## Prueba a pedirle

- "Elige las 150 mejores y explícame cada rechazo."
- "Revela una muestra con Ektar 100 y grano suave."

**Términos.** Revelado no es un producto de Anthropic. Usa el servidor según los términos y las políticas de uso de Anthropic.
