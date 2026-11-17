# Recetas y CLI

*[English](../../en/manual/recipes-cli.md)*

**Recetas.** Cada ajuste es una **receta**, un pequeño documento JSON. La misma receta sobre el mismo RAW, con la misma versión del motor, da los mismos píxeles. Cada foto revelada guarda su receta *resuelta*, con los valores automáticos medidos (exposición, detecciones, granitos curados), para poder reproducirla. El esquema, con rangos, valores por defecto y etiquetas ES/EN, está en `GET /api/recipe/schema`.

**Línea de comandos (v0.1).** El comando `revelado` arranca el motor (`--host 127.0.0.1 --port 8790`). Para automatizar se usa la API local. Cada petición lleva el token de `engine/.runtime_token` en la cabecera `X-Revelado-Token`. Hay ejemplos con `curl` en la [versión en inglés](../../en/manual/recipes-cli.md).

Los comandos de lote (`revelado develop`, `revelado export`) con un archivo de receta están planificados. Mientras tanto, el [servidor MCP](mcp.md) es la forma más cómoda de automatizar una sesión.
