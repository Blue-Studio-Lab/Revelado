# Opciones de IA

*[English](../../en/manual/ai.md)*

Revelado funciona **completo sin IA**. La IA es opcional, está apagada por defecto y siempre se paga con *tu* cuenta.

| Vía | Cómo se activa | Quién paga |
|---|---|---|
| **Sin IA** | No hace falta nada | Nadie |
| **Clave de API de Anthropic** | Crea la clave en [platform.claude.com](https://platform.claude.com) y escríbela en `engine/.env` como `ANTHROPIC_API_KEY=…`. Ese archivo nunca se sube y la clave nunca se muestra en el navegador. | Tu cuenta de la Consola, por uso |
| **MCP** | Añade el servidor MCP de Revelado a tu Claude Code o Claude Desktop ([MCP](mcp.md)) | Tu plan de Claude; Revelado no necesita clave |

**Sobre los planes.** Claude **Pro no incluye la API**. Los planes Max, Team y Ultra incluyen créditos API mensuales, que se reclaman en la facturación del plan. *Los planes cambian: verifícalo en platform.claude.com.*

**Qué se envía (vía API).**

- **Se envían** previsualizaciones JPEG reducidas, **sin EXIF**, con identificadores opacos (`p0001`).
- **Nunca se envían** nombres de archivo, rutas, GPS, números de serie, RAW ni nombres de clientes.

Das permiso en cada uso y ves el coste estimado antes de empezar. Un tope de presupuesto detiene el trabajo al alcanzarlo.

**Qué decide la IA.** Claude *propone*. Las reglas locales de Revelado ajustan cada valor a rangos seguros, y tú aceptas o rechazas.
