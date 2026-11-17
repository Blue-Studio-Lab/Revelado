# Revelado user manual

*[Leer en español](../../es/manual/index.md)*

Revelado works like a small film lab on your computer:

1. **[Install](install.md)**: get the engine and the web app running from source.
2. **[Import](import.md)**: open a folder; RAW and JPG files are paired by name.
3. **[Viewer and masks](viewer.md)**: look at photos at 100 %, with noise, chromatic-aberration and colour-shading masks.
4. **[Culling](culling.md)**: pick and reject by hand, or let the technical cull (or Claude) suggest.
5. **[Develop](develop.md)**: choose a film stock, develop **one sample**, adjust the recipe.
6. **[Oven](oven.md)**: develop the whole selection, one photo at a time, with live progress.
7. **[Export](export.md)**: JPG or 16-bit TIFF to a folder of your choice.

Also in this manual:

- [Recipes and CLI](recipes-cli.md): how the settings are stored and how to script them;
- [AI options](ai.md): no AI, an Anthropic API key, or your own Claude through MCP;
- [MCP](mcp.md): driving Revelado from Claude Code or Claude Desktop;
- [Rescue mode](rescue.md): coming soon.

**Your originals are never modified.** Revelado reads source folders, never writes into them, and never overwrites an existing export.
