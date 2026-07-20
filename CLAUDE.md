# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FilmSheet is a film scanning layout tool that converts digital film scans into professional-grade contact sheets — simulating lightbox viewing or classic contact printing. It supports 135 (35mm) and 120 medium format films with physical-level perforation accuracy (SMPTE standards), structured edge text, pack image overlays, bilingual info labels, and batch export.

Three frontends share the same rendering engine:
- **Desktop** — Tkinter GUI (main.py)
- **Mobile web** — Single-page HTML app served by the API
- **REST API** — FastAPI backend for NAS/cloud deployment

## Directory Structure

```
main.py                  # Desktop entry point (Tkinter)
engine/                  # Physical film constants (SMPTE dimensions)
  film_engine.py         # Strict135FilmEngine — perforation geometry, pitch, types
processor/               # Core rendering pipeline
  film_processor.py      # FilmProcessor — orchestrates image processing + rendering
  renderer.py            # BaseRenderer — shared canvas/info/pack/watermark logic
  renderers_135.py       # Renderer135 — perforations, sub-formats, edge text
  renderers_120.py       # Renderer120 — no perforations, configurable aspect ratios
  image_pipeline.py      # Pure functions: crop, resize, cover, process images
  edge_text.py           # Pure function: brand/type mapping (Kodak→KODAK, etc.)
  config_schema.py       # Field definitions, validation, sanitization
  filename_utils.py      # Output filename generation from info fields
ui/
  app.py                 # Tkinter desktop app — UI, templates, preview, batch export
api/
  main.py                # FastAPI server — POST /render, GET /health, serves index.html
  index.html             # Mobile web frontend (single-page, vanilla JS)
  Dockerfile             # Container image for API deployment
utils/
  helpers.py             # Config I/O, font loading, style colors, constants
```

## Key Architecture Concepts

### Rendering Pipeline
1. **Image preprocessing** — Open → convert RGB → optional invert (negative mode) → rotate if landscape → crop to format ratio → resize to thumbnail width
2. **Layout computation** — Calculate canvas size from cols, rows, strip height, margins, info block height, pack image height
3. **Canvas construction** — Create RGBA canvas at 4x scale for antialiasing (except preview)
4. **Draw layers** — Pack image → info block → film strip rows (perforations + edge text + images) → watermark
5. **Downscale** — LANCZOS resize from 4x to final resolution
6. **Save** — PNG (lossless) or JPG (configurable quality)

### BaseRenderer Class (processor/renderer.py)
Abstract base that subclasses share. Key methods:
- `render()` — public entry point, orchestrates the full pipeline
- `compute_layout()` — overridden per format (135 vs 120)
- `draw_strip_decoration()` — perforations for 135, edge text for both
- `_draw_pack_image()`, `_draw_info_block()`, `_draw_watermark()` — shared across formats

### Config System
- Config persisted to platform-standard dirs (`%APPDATA%/FilmSheet/`, `~/.config/FilmSheet/`, `~/Library/Application Support/FilmSheet/`)
- `config_schema.py` defines FIELD_DEFS with types, defaults, ranges, valid values
- Templates saved in config for quick preset switching
- Pack image history (last 30 paths)

### Edge Text Generation
`edge_text.py` is a pure function that maps raw film names to structured edge text:
- Chinese brands → English (柯达→KODAK, 富士→FUJIFILM, etc.)
- Cinema film keywords (Vision3, 5207, etc.) → EASTMAN brand
- Custom user text bypasses all mapping

### Style Colors
Two render styles defined in `helpers.py` STYLE_COLORS:
- `lightbox` — white canvas, warm orange edge text, dark film base
- `contact_sheet` — black canvas, white edge text, dark gray film base

## Common Commands

### Tests
```bash
python -m unittest tests.test_core -v    # Run all core tests
```

### Desktop App
```bash
pip install -r requirements.txt    # Pillow, ttkthemes
python main.py                      # Launch desktop GUI
```

### API Server
```bash
pip install -r api/requirements.txt # fastapi, uvicorn, python-multipart, pillow
uvicorn api.main:app --reload --port 8000  # Dev server
# API docs at http://localhost:8000/docs
```

### Docker (API)
```bash
docker build -t filmsheet-api api/
docker run -d -p 8000:8000 filmsheet-api
```

### Packaging
```bash
# macOS (py2app)
pip install py2app
python setup.py py2app

# Windows (PyInstaller)
pip install pyinstaller
pyinstaller --onefile --windowed main.py
```

### CI/CD
GitHub Actions workflow at `.github/workflows/build.yml` builds macOS (.app) and Windows (.exe) on tag pushes (v*).

## Important Details

- **120 API support is missing** — `api/main.py` line 555 returns HTTP 501 for film_format=="120". The desktop app and renderers support 120 fully; the API endpoint just needs to route to `_render_120_api()` similar to `_render_135_api()`.
- **API uses its own inline 135 renderer** (`api/main.py:_render_135_api`) rather than importing `Renderer135` — this avoids UI dependencies but duplicates layout logic. Consider consolidating.
- **Preview uses reduced thumb width (80px)** for speed — see `FilmProcessor.render_preview()`.
- **Batch export** generates both lightbox and contact_sheet styles by creating a second FilmProcessor instance with swapped render_style.
- **Perforation types**: KS (民用/rounded rectangle) vs BH (电影/cut-corner ellipse). Auto-detection based on film name keywords.
- **Font loading** is platform-aware: Windows uses msyh.ttc/simhei.ttf, macOS uses PingFang/STHeiti, Linux uses wqy/DejaVu. Falls back to PIL default.
