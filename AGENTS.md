# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

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
archive/                 # Film roll archive (archive library) — no tkinter import
  models.py              # Roll entity — one roll = one archive record
  store.py               # JSON persistence — atomic write, corruption recovery, migration
  service.py             # ArchiveService facade — auto/manual filing, query, import
  thumbs.py              # Thumbnail generation + disk cache
ui/
  app.py                 # Tkinter desktop app — UI, templates, preview, batch export
  rounded.py             # Canvas-drawn widgets (RoundedCard/PillButton/SegmentedControl)
  aa.py                  # Antialiased shape rasterizer (PIL supersampling + LRU cache)
  icons.py               # Code-drawn icon set
  archive_page.py        # Archive library page — grid, search, filters, detail panel
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

### Film Roll Archive
Turns the one-shot render task into a persisted record. Layers are strictly
one-directional — `models` → `store` → `service` → `thumbs`, none import tkinter,
so the desktop app and any future API/Web frontend can share them.

- **The archive grid must compute its column count from the grid canvas's own
  width**, never from the page width. The page also holds a 286px detail panel;
  sizing columns against the page pushes cards underneath it (visible as the
  rightmost card half-covered). `ArchivePage._grid_avail_width()` exists for this.
- **Card height is computed, never hardcoded.** `card_text_height(fonts, lines)`
  measures the real `linespace` and multiplies by the actual row count (3 without
  a camera, 4 normally, 5 with the "file missing" warning). A hardcoded
  `CARD_TEXT_H` silently clipped the date row as soon as a field was added.
- Rows are sized with `minsize` (from `card_height(fonts, warn=True)`) and cards
  use `sticky="n"` — `"nsew"` stretches them and distorts the text area.

- **Roll id is derived** from `roll_no + shoot_date + film` (sha1), never uuid.
  Re-rendering the same roll must land on the same record, otherwise the library
  fills with duplicates.
- **Merge, never replace** (`Roll.merge_from`) — a second lightbox render must not
  drop the previous contact sheet; empty info fields don't overwrite filled ones.
- **Atomic writes** — temp file + `fsync` + `os.replace`. Archive data is
  long-lived; a half-written file must not destroy everything.
- **Corruption doesn't lose data** — an unparseable file is renamed to
  `.corrupt-<timestamp>` and treated as an empty library.
- **Deleting a roll keeps its files** by default. The archive is an index, not
  ownership.
- **Location is resolvable** — `FILMSHEET_ARCHIVE_DIR` env var >
  `archive_dir` in `filmsheet_config.json` > platform config dir. The env var wins
  because it works even when the config file itself is unreadable. Moving the
  library copies first and only then rewrites the config, so a failed copy leaves
  the old location authoritative.
- `FilmProcessor(config, on_done=cb)` — `on_done` is optional. Not passing it
  leaves behavior identical (API path). Callback exceptions are swallowed and
  logged: an archive write failure must not turn an already-rendered image into
  a reported failure.
- The archive page receives its available width from `App._layout_pages()` via
  `on_container_resized()`, not from its own `<Configure>` binding.

### UI Rendering — no antialiasing in Tk
Tk Canvas has **no antialiasing API**. `create_polygon(..., smooth=True)` emits
cubic Béziers (visible as `curveto` in exported PostScript) but rasterizes them to
pixels **without color blending** — edges are hard steps, and on a Retina display
(1 logical px = 2 physical px) the steps are magnified into visible jaggies.

`ui/aa.py` works around it: draw the shape with PIL at N× size, then
`resize(..., LANCZOS)` back down. The resize performs the edge blending, which
*is* antialiasing. Measured at SS=4: 136x34 button ≈ 1.2 ms, 186x200 card ≈ 11.5 ms.

- `CanvasAA` wraps a Canvas and holds `PhotoImage` references — Tk garbage-collects
  them otherwise and the widget renders blank.
- `prune()` drops references for items already removed by `delete("all")`;
  `drop_tag(tag)` must be called **before** `canvas.delete(tag)`, otherwise
  `find_withtag` can't find the stale ids and the `PhotoImage`s leak.
- Widgets draw their **background shape as a bitmap but text and icons as vectors** —
  letting Tk rasterize glyphs keeps them crisp.
- Only `SlimScrollbar` is left vector-drawn (plain rectangles, no curved edges).

`CanvasAA` holds `PhotoImage` references — Tk garbage-collects them otherwise
and the widget renders blank. Two ordering rules matter:
- `drop_tag(tag)` must be called **before** `canvas.delete(tag)`; afterwards
  `find_withtag` can't find the stale ids and the references leak forever.
- `prune()` is the safety net for `delete("all")` paths, and also cleans up when
  the ordering above was wrong.

### ttk background inside cards
`TLabel` / `Muted.TLabel` use the **page** background, because most labels live
directly on the page. Labels inside a `RoundedCard` sit on the **card surface**,
so they must use `Card.TLabel` / `CardMuted.TLabel` / `CardTitle.TLabel` — otherwise
each label gets a mismatched rectangle behind the text, glaring in dark mode.

`RoundedCard._apply` calls `_fix_ttk()` to re-style its whole subtree
recursively; it previously touched only the direct children of `body`, which left
deeply nested labels wrong. `TCheckbutton` / `TRadiobutton` already used surface.

`tests/test_ui_aa.py` locks this in by asserting the presence of
**semi-transparent edge pixels**. Do not measure antialiasing along a diagonal —
that path misses the blend on shapes ≥80px and reports zero.

### Config I/O Robustness
`utils/helpers.save_config()` writes atomically (temp file + `fsync` +
`os.replace`) and returns a bool. A direct `open(path,'w')` overwrite could leave
a truncated JSON on crash; the next launch would then read it back.

`load_config()` catches `json.JSONDecodeError` (which is **not** an `OSError`
subclass) and renames an unparseable file to `.corrupt-<timestamp>` before
falling back to defaults. Without that, a corrupted config crashed the app at
startup and took the user's templates with it.

### Packaging
`main.spec` lists every first-party package explicitly (including `archive.*`).
PyInstaller's static analysis misses function-body attribute access such as
`thumbs_mod.clear_thumb(...)`. Verify with a real build, not just import checks:
`python -m PyInstaller main.spec --distpath /tmp/x/dist --workpath /tmp/x/build`,
then confirm the modules appear in `Analysis-00.toc` and the app survives launch.
`setup.py` (py2app) needs the same list in `packages` and `includes`.

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
python -m unittest tests.test_core -v       # Rendering pipeline (no GUI)
python -m unittest tests.test_archive -v   # Archive library (no GUI)
python -m unittest tests.test_config -v    # Config I/O robustness (no GUI)
python -m unittest tests.test_ui_aa -v     # Antialiasing quality + performance
python tests/selftest_archive_ui.py        # Archive UI headless self-test
python tests/preview_archive_ui.py         # Render the archive page to a PNG
```
Tests need Pillow. On macOS the WorkBuddy-managed Python lacks it — use
`/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`.
`tests.test_api` additionally needs `httpx2`.

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

- **`sub_format` is a cross-field constraint** — its legal values depend on `film_format` (135 uses `标准 36×24`/`半格 18×24`/`方形 24×24`/`XPan 65×24`; 120 uses frame numbers `645`/`66`/`67`/`68`/`69`/`612`/`617`/`624`). It can't use the plain `"valid"` list in `FIELD_DEFS`, so `config_schema._validate_sub_format()` checks it against `SUB_FORMATS_BY_FORMAT`. **Without this, an unknown value silently falls back to a default ratio inside `image_pipeline` and returns a wrong-aspect sheet with HTTP 200.** Blank/missing `sub_format` is legal and resolves via `default_sub_format(film_format)`; `sanitize_config()` must resolve it *before* the generic `FIELD_DEFS` pass, or a missing value picks up 135's default regardless of format.
- **API shares the desktop renderers** — `processor/api_render.py` wraps `Renderer135` / `Renderer120` with a `FilmProcessorStub`, so `api/main.py` no longer duplicates layout logic (an earlier inline `_render_135_api` is gone). Both 135 and 120 are fully supported by the endpoint.
- **Preview uses reduced thumb width (80px)** for speed — see `FilmProcessor.render_preview()`.
- **Batch export** generates both lightbox and contact_sheet styles by creating a second FilmProcessor instance with swapped render_style.
- **Perforation types**: KS (民用/rounded rectangle) vs BH (电影/cut-corner ellipse). Auto-detection based on film name keywords.
- **Font loading** is platform-aware: Windows uses msyh.ttc/simhei.ttf, macOS uses PingFang/STHeiti, Linux uses wqy/DejaVu. Falls back to PIL default.
