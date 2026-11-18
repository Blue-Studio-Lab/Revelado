# Instalación (desde el código)

*[English](../../en/manual/install.md)*

Revelado v0.1 funciona desde el código en **macOS, Windows y Linux**. Los instaladores firmados llegarán en la v0.3.

## Requisitos

| Herramienta | Versión | Para qué |
|---|---|---|
| Python | 3.12, 3.13 o 3.14 | el motor |
| Node.js | 22 LTS (22.12 o superior) o 24 LTS | la interfaz web |
| ExifTool | reciente | leer metadatos (obligatorio) |
| RawTherapee | 5.10 o superior, con `rawtherapee-cli` | revelar RAW (Cuarto oscuro, Horno, Exportar) |
| RAM | 16 GB recomendados | un revelado a resolución completa usa varios GB |

Sin RawTherapee puedes igualmente importar, ver, usar las máscaras y seleccionar.

## Pasos

Los comandos exactos para cada sistema están en la [versión en inglés](../../en/manual/install.md#macos). En resumen:

```bash
git clone https://github.com/Blue-Studio-Lab/Revelado.git && cd Revelado
python3 -m venv .venv && source .venv/bin/activate      # Windows: .\.venv\Scripts\Activate.ps1
pip install -e "engine[dev]"                             # añade ,ai para las funciones de Claude
revelado                                                 # terminal 1: motor en 127.0.0.1:8790
cd apps/web && npm ci && npm run dev                     # terminal 2: abre http://localhost:5173
```

## Configuración

Los ajustes opcionales van en `engine/.env`. Créalo copiando `engine/.env.example`; ese archivo nunca se sube al repositorio. Claves habituales:

- `REVELADO_CACHE_DIR`: dónde se guardan el catálogo, las miniaturas y los revelados.
- `REVELADO_FILMS_DIR`: carpeta con tus escaneos de película.
- `REVELADO_PORT`: el puerto del motor.
- `REVELADO_HEAVY_RSS_CAP_GB`: el límite de memoria de un revelado.
- `REVELADO_MIN_FREE_DISK_GB`: el disco libre mínimo antes de revelar.
- `ANTHROPIC_API_KEY`: opcional.

## Problemas frecuentes

- **"No hay conexión con el motor de revelado."** Arranca `revelado` y recarga la página.
- **Falta RawTherapee.** Instálalo y comprueba que `rawtherapee-cli --version` funciona en una terminal.
- **No hay memoria libre suficiente.** Cierra aplicaciones pesadas. Revelado espera hasta 2 minutos y luego te pide reintentar.
