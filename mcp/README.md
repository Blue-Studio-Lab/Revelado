# revelado-mcp

**Español** · [English](#english)

Servidor [MCP](https://modelcontextprotocol.io) de **Revelado**. Permite que tu propio Claude Code o Claude Desktop
(o cualquier cliente MCP) maneje el motor de Revelado que corre en tu ordenador: seleccionar fotos, diagnosticar
ruido y aberración cromática, revelar una muestra con antes/después, revelar en lote y exportar.

Incluye:

- el servidor `revelado-mcp` (Python, SDK oficial `mcp`, transporte stdio);
- un plugin para Claude Code (`plugin/revelado/`) con tres skills: `revelar-sesion`, `seleccionar` y `rescate`;
- un manifiesto `.mcpb` (`manifest.json`) para instalarlo en Claude Desktop con un clic.

> **Sobre tu suscripción de Claude.** La app Revelado **no** ofrece inicio de sesión con claude.ai ni usa tus
> credenciales. Este servidor MCP es otra vía: lo instalas **tú** en **tu** Claude Code o Claude Desktop, que usa
> tu propia cuenta. La documentación de Anthropic no cubre de forma explícita el caso de un servidor MCP o un
> plugin de terceros usado desde el Claude Code del propio usuario. Revisa y cumple los
> [términos de Anthropic](https://www.anthropic.com/legal) y sus políticas de uso. Revelado no es un producto de
> Anthropic.

## Requisitos

- El motor de Revelado en marcha en este ordenador (por defecto `http://127.0.0.1:8790`). Ver `engine/README.md`.
- [uv](https://docs.astral.sh/uv/) (incluye `uvx`), o Python ≥ 3.12 con `pip`.
- macOS, Windows o Linux.

## Configuración

El servidor **solo** abre fotos de las carpetas que permitas y **solo** exporta a las carpetas de exportación que
permitas. Sin carpetas permitidas, `open_folder` y `export` se niegan.

| Variable | Qué es |
|---|---|
| `REVELADO_ALLOWED_DIRS` | Carpetas de fotos permitidas, separadas por `:` (macOS/Linux) o `;` (Windows). |
| `REVELADO_EXPORT_DIRS` | Carpetas donde se puede exportar (mismo formato). Nunca dentro de la carpeta de originales. |
| `REVELADO_ENGINE_URL` | URL del motor. Solo se aceptan direcciones de este equipo (127.0.0.1, localhost, ::1). |
| `REVELADO_ENGINE_TOKEN` | Token del motor (opcional; si no, se lee del archivo). |
| `REVELADO_TOKEN_FILE` | Archivo con el token. El motor escribe `.runtime_token` al arrancar. |
| `REVELADO_ENGINE_DIR` | Carpeta `engine/` del repositorio (desde el código fuente): el token se lee de `engine/.runtime_token`. |
| `REVELADO_MAX_BATCH` | Máximo de fotos por lote (500). |
| `REVELADO_MCP_CONFIG` | Ruta de un archivo JSON de configuración. |

También puedes usar un archivo `mcp.json` (claves `allowed_dirs`, `export_dirs`, `engine_url`, `token_file`,
`max_batch`) en la carpeta de configuración de usuario: `~/Library/Application Support/Revelado/` (macOS),
`%APPDATA%\Revelado\` (Windows) o `~/.config/revelado/` (Linux).

```json
{ "allowed_dirs": ["~/Pictures/Sesiones"], "export_dirs": ["~/Pictures/Entregas"] }
```

Comprueba la configuración y la conexión con el motor:

```bash
uvx revelado-mcp --check
```

## Instalación en Claude Code

**Con el plugin (recomendado):** incluye el servidor y las skills.

```text
/plugin marketplace add Blue-Studio-Lab/Revelado
/plugin install revelado@revelado
```

El plugin lee `REVELADO_ALLOWED_DIRS` y `REVELADO_EXPORT_DIRS` de tu entorno, o el archivo `mcp.json` de arriba.

**Solo el servidor:**

```bash
claude mcp add revelado -s user \
  -e REVELADO_ALLOWED_DIRS=~/Pictures/Sesiones \
  -e REVELADO_EXPORT_DIRS=~/Pictures/Entregas \
  -- uvx revelado-mcp
```

## Instalación en Claude Desktop

**Paquete `.mcpb`:** descarga `revelado.mcpb` de la página de *Releases*, ábrelo con Claude Desktop y elige tus
carpetas de fotos y de exportación en el formulario.

**A mano:** en *Settings → Developer → Edit Config* (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "revelado": {
      "command": "uvx",
      "args": ["revelado-mcp"],
      "env": {
        "REVELADO_ALLOWED_DIRS": "~/Pictures/Sesiones",
        "REVELADO_EXPORT_DIRS": "~/Pictures/Entregas"
      }
    }
  }
}
```

Desde el código fuente (antes de que esté en PyPI) usa `"command": "uv"` y
`"args": ["run", "--directory", "<ruta>/Revelado/mcp", "revelado-mcp"]`.

## Herramientas

| Herramienta | Qué hace | Escribe |
|---|---|---|
| `engine_status` | Estado del motor, películas entrenadas, carpetas permitidas | — |
| `list_sessions` | Carpetas abiertas antes (dentro de la lista permitida) | — |
| `open_folder` | Abre una carpeta permitida | catálogo |
| `list_photos` | Lista con filtros: estrellas, bandera, veredicto, cámara, ráfagas | — |
| `get_photo_info` | EXIF (sin GPS ni nº de serie), perfil de cámara/objetivo, resultado del culling | — |
| `get_preview` | Vista previa ≤ 1024 px sin metadatos (cámara o revelada) | — |
| `diagnose` | Ruido, aberración cromática y sombreado de color (+ máscaras) | — |
| `set_rating` | Estrellas y bandera pick/reject | catálogo |
| `auto_cull_technical` | Culling técnico local (nitidez, ojos, exposición, ráfagas) | catálogo |
| `list_films` | Películas y si están entrenadas | — |
| `get_recipe` / `patch_recipe` | Receta de trabajo, validada contra el esquema del motor | memoria |
| `develop_sample` | Revela una foto y devuelve antes/después | caché |
| `develop_batch` | Revela un lote (trabajo en cola) | caché |
| `job_status` / `cancel_job` | Seguimiento y cancelación de trabajos | — |
| `export` | Escribe archivos finales; **requiere `confirm=true`** | archivos nuevos |

## Seguridad

- Solo carpetas permitidas; rutas canónicas (se siguen los enlaces simbólicos antes de comprobar). Nada fuera de
  ellas, ningún borrado, nunca se modifican los originales.
- Las fotos se identifican con ids opacos del motor; las listas no devuelven rutas.
- Imágenes para el modelo: ≤ 1024 px, re-codificadas sin EXIF/GPS/ICC.
- Trabajos pesados por la cola única del motor y, además, como mucho **uno a la vez por conexión** (`E_BUSY`).
- `export` está marcada como destructiva, exige `confirm=true` y una carpeta de exportación permitida que no esté
  dentro de una carpeta de originales; nunca sobrescribe.
- El token del motor nunca sale del equipo (solo se aceptan URLs locales; sin proxies).

## Desarrollo

```bash
cd mcp
uv venv && uv pip install -e ".[dev]"
uv run pytest          # pruebas con un motor simulado; no hace falta el motor real ni fotos
```

Licencia: GPL-3.0-or-later. Autor: Jorge Luis Peña Sanchez (Blue Studio Lab). Co-creado con Claude (IA de
Anthropic).

---

<a id="english"></a>

## English

[MCP](https://modelcontextprotocol.io) server for **Revelado**. It lets your own Claude Code or Claude Desktop (or
any MCP client) drive the Revelado engine running on your computer: cull photos, diagnose noise and chromatic
aberration, develop one sample with a before/after, develop batches and export.

It ships:

- the `revelado-mcp` server (Python, official `mcp` SDK, stdio transport);
- a Claude Code plugin (`plugin/revelado/`) with three skills: `revelar-sesion` (develop a shoot),
  `seleccionar` (cull) and `rescate` (rescue difficult frames);
- an `.mcpb` manifest (`manifest.json`) for one-click install in Claude Desktop.

> **About your Claude subscription.** The Revelado app does **not** offer claude.ai login and never uses your
> credentials. This MCP server is a separate path: **you** install it in **your** Claude Code or Claude Desktop,
> which uses your own account. Anthropic's documentation does not explicitly cover a third-party MCP server or
> plugin used from the user's own Claude Code. Review and follow
> [Anthropic's terms](https://www.anthropic.com/legal) and usage policies. Revelado is not an Anthropic product.

### Requirements

- The Revelado engine running on this computer (default `http://127.0.0.1:8790`); see `engine/README.md`.
- [uv](https://docs.astral.sh/uv/) (provides `uvx`), or Python ≥ 3.12 with `pip`.
- macOS, Windows or Linux.

### Configuration

The server opens photos **only** from folders you allow and exports **only** to export folders you allow. With
none configured, `open_folder` and `export` refuse.

| Variable | Meaning |
|---|---|
| `REVELADO_ALLOWED_DIRS` | Allowed photo folders, separated by `:` (macOS/Linux) or `;` (Windows). |
| `REVELADO_EXPORT_DIRS` | Allowed export folders (same format). Never inside a source folder. |
| `REVELADO_ENGINE_URL` | Engine URL. Only this machine is accepted (127.0.0.1, localhost, ::1). |
| `REVELADO_ENGINE_TOKEN` | Engine token (optional; otherwise read from the token file). |
| `REVELADO_TOKEN_FILE` | Token file. The engine writes `.runtime_token` on start. |
| `REVELADO_ENGINE_DIR` | The repository's `engine/` folder (running from source): token read from `engine/.runtime_token`. |
| `REVELADO_MAX_BATCH` | Max photos per batch (500). |
| `REVELADO_MCP_CONFIG` | Path of a JSON config file. |

Or put an `mcp.json` (keys `allowed_dirs`, `export_dirs`, `engine_url`, `token_file`, `max_batch`) in the user
config folder: `~/Library/Application Support/Revelado/` (macOS), `%APPDATA%\Revelado\` (Windows) or
`~/.config/revelado/` (Linux). Check everything with `uvx revelado-mcp --check`.

### Install in Claude Code

Plugin (server + skills):

```text
/plugin marketplace add Blue-Studio-Lab/Revelado
/plugin install revelado@revelado
```

Server only:

```bash
claude mcp add revelado -s user \
  -e REVELADO_ALLOWED_DIRS=~/Pictures/Shoots \
  -e REVELADO_EXPORT_DIRS=~/Pictures/Deliveries \
  -- uvx revelado-mcp
```

### Install in Claude Desktop

Download `revelado.mcpb` from the Releases page, open it with Claude Desktop and pick your photo and export
folders. Or edit `claude_desktop_config.json` (*Settings → Developer → Edit Config*) with the same `mcpServers`
block shown in the Spanish section. From source, use `"command": "uv"` and
`"args": ["run", "--directory", "<path>/Revelado/mcp", "revelado-mcp"]`.

### Tools and safety

See the table above. In short: allow-listed folders only (canonical paths, symlinks resolved before the check),
no deletes, originals never modified, opaque photo ids (no paths in listings), previews ≤ 1024 px with metadata
stripped, heavy jobs through the engine's single queue and at most one in flight per connection, `export`
annotated destructive and gated by `confirm=true` plus an allowed export folder outside any source folder, engine
token never leaves the machine.

### Development

```bash
cd mcp
uv venv && uv pip install -e ".[dev]"
uv run pytest          # mocked engine; no real engine or photos needed
```

License: GPL-3.0-or-later. Author: Jorge Luis Peña Sanchez (Blue Studio Lab). Co-created with Claude
(Anthropic's AI).
