# Install (from source)

*[Español](../../es/manual/instalacion.md)*

Revelado v0.1 runs from source on **macOS, Windows and Linux**. Signed installers come in v0.3.

## Requirements

| Tool | Version | Needed for |
|---|---|---|
| Python | 3.12, 3.13 or 3.14 | the engine |
| Node.js | 22 LTS (22.12 or newer) or 24 LTS | the web interface |
| ExifTool | any recent | reading metadata (required) |
| RawTherapee | 5.10 or newer, with `rawtherapee-cli` | developing RAW files (Develop, Oven, Export) |
| RAM | 16 GB recommended | one full-resolution develop uses several GB |

Without RawTherapee you can still import, view, run the diagnostic masks and cull.

## macOS

```bash
brew install python@3.13 node exiftool
brew install --cask rawtherapee
git clone https://github.com/Blue-Studio-Lab/Revelado.git && cd Revelado
python3 -m venv .venv && source .venv/bin/activate
pip install -e "engine[dev]"        # add ,ai to enable the optional Claude features
```

## Windows (PowerShell)

```powershell
winget install Python.Python.3.13 OpenJS.NodeJS.LTS OliverBetz.ExifTool RawTherapee.RawTherapee
git clone https://github.com/Blue-Studio-Lab/Revelado.git; cd Revelado
py -3.13 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e "engine[dev]"
```

If `rawtherapee-cli.exe` or `exiftool.exe` is not found, add the install folders to your `PATH`.

## Linux (Debian/Ubuntu)

```bash
sudo apt install python3 python3-venv libimage-exiftool-perl rawtherapee
# Node.js: the distro package is too old (Ubuntu 24.04 ships 18.x). Install 22 LTS or 24 LTS
# from https://nodejs.org/en/download (nvm or the official binaries), then check `node -v`.
git clone https://github.com/Blue-Studio-Lab/Revelado.git && cd Revelado
python3 -m venv .venv && . .venv/bin/activate
pip install -e "engine[dev]"
```

## Start

Open two terminals in the repository folder.

```bash
# 1) the engine (listens only on 127.0.0.1:8790)
revelado
# 2) the web interface
cd apps/web && npm ci && npm run dev
```

Open <http://localhost:5173>. The first-run screen checks your tools (Python packages, ExifTool, RawTherapee) and asks for your language and AI preference.

## Configuration

Optional settings live in `engine/.env`. Copy `engine/.env.example` to create it; the file is never committed. Common keys:

| Key | Default | Meaning |
|---|---|---|
| `REVELADO_CACHE_DIR` | `<repo>/cache` | catalog, thumbnails, masks, developed results |
| `REVELADO_FILMS_DIR` | empty | folder with your own film scans (`<stock>/scans/*.tif`) |
| `REVELADO_PORT` | `8790` | engine port (always on 127.0.0.1) |
| `REVELADO_HEAVY_RSS_CAP_GB` | see file | memory cap for one develop (whole process tree) |
| `REVELADO_MIN_FREE_DISK_GB` | `6` | free disk needed before a develop starts |
| `ANTHROPIC_API_KEY` | empty | optional; see [AI options](ai.md) |

## Troubleshooting

- **"Can't reach the development engine."** The engine is not running, or it is on a different port. Start `revelado` and reload the page.
- **RawTherapee missing.** Install it and make sure `rawtherapee-cli --version` works in a terminal.
- **Not enough free memory.** Close other heavy apps. Revelado waits up to 2 minutes for memory, then asks you to retry.
