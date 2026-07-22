# -*- coding: utf-8 -*-
"""Configuration schema for FilmSheet.

Centralizes all configuration field definitions, types, defaults and validation.
This enables future porting to Web/API by providing a single source of truth
for the configuration contract.
"""

from typing import Any

SCHEMA_VERSION = 1

# All valid sub-format values per film format
SUB_FORMATS_135 = ["标准 36×24", "半格 18×24", "方形 24×24", "XPan 65×24"]
SUB_FORMATS_120 = ["645", "66", "67", "68", "69", "612", "617"]

# All valid render styles
RENDER_STYLES = ["lightbox", "contact_sheet"]

# All valid film formats
FILM_FORMATS = ["135", "120"]

# All valid processing modes
PROCESSING_MODES = ["positive", "negative"]

# All valid output formats
OUTPUT_FORMATS = ["PNG", "JPG"]

# All valid perf modes
PERF_MODES = ["Auto", "KS (民用)", "BH (电影)"]

# All valid pack positions
PACK_POSITIONS = ["left", "right"]

# All valid info languages
INFO_LANGS = ["zh", "en"]

# Field definitions: {key: {"type": ..., "default": ..., "range": ...}}
FIELD_DEFS: dict[str, dict[str, Any]] = {
    # Basic
    'input_folder':    {"type": "string", "default": ""},
    'output_file':     {"type": "string", "default": "filmsheet_output.jpg"},

    # Parameters
    'thumb_width':     {"type": "int",    "default": 400,  "range": (300, 800)},
    'spacing':         {"type": "int",    "default": 20},
    'columns':         {"type": "int",    "default": 6,    "range": (3, 10)},
    'force_landscape': {"type": "bool",   "default": True},
    'edge_text':       {"type": "string", "default": ""},

    # Output
    'output_format':   {"type": "string", "default": "JPG", "valid": OUTPUT_FORMATS},
    'quality':         {"type": "int",    "default": 95,    "range": (1, 100)},

    # Film format
    'film_format':     {"type": "string", "default": "135", "valid": FILM_FORMATS},
    'sub_format':      {"type": "string", "default": "标准 36×24"},
    'info_lang':       {"type": "string", "default": "en",  "valid": INFO_LANGS},

    # Pack image
    'pack_image':      {"type": "string", "default": ""},
    'pack_position':   {"type": "string", "default": "left", "valid": PACK_POSITIONS},
    'pack_border_stroke': {"type": "bool", "default": True},
    'render_style':    {"type": "string", "default": "lightbox", "valid": RENDER_STYLES},
    'pack_size':       {"type": "int",    "default": 80,    "range": (10, 100)},

    # Processing
    'processing_mode': {"type": "string", "default": "positive", "valid": PROCESSING_MODES},
    'perf_mode':       {"type": "string", "default": "Auto", "valid": PERF_MODES},

    # Info fields
    'info_roll':       {"type": "string", "default": ""},
    'info_camera':     {"type": "string", "default": ""},
    'info_film':       {"type": "string", "default": ""},
    'info_shoot_date': {"type": "string", "default": ""},
    'info_dev_date':   {"type": "string", "default": ""},
    'info_proc':       {"type": "string", "default": ""},
    'info_lab':        {"type": "string", "default": ""},
    'info_scanner':    {"type": "string", "default": ""},

    # Batch / watermark
    'batch_export_enabled': {"type": "bool", "default": False},
    'signature':       {"type": "string", "default": ""},
}

# Output path is computed, not stored in config
COMPUTED_FIELDS = {'output_path'}


def validate_config(cfg: dict) -> tuple[bool, list[str]]:
    """Validate a config dict against FIELD_DEFS.

    Returns (is_valid, list_of_error_messages).
    """
    errors: list[str] = []
    for key, defs in FIELD_DEFS.items():
        val = cfg.get(key)
        if val is None:
            continue

        if "valid" in defs and val not in defs["valid"]:
            errors.append(f"'{key}' invalid value '{val}', must be one of {defs['valid']}")

        if defs["type"] == "int" and "range" in defs:
            lo, hi = defs["range"]
            if not (lo <= val <= hi):
                errors.append(f"'{key}' value {val} out of range [{lo}, {hi}]")

        if defs["type"] == "bool" and not isinstance(val, bool):
            errors.append(f"'{key}' must be boolean, got {type(val).__name__}")

        if defs["type"] == "string" and not isinstance(val, str):
            errors.append(f"'{key}' must be string, got {type(val).__name__}")

    return len(errors) == 0, errors


def sanitize_config(cfg: dict) -> dict:
    """Apply defaults for missing fields and coerce types.

    Returns a new config dict with all known fields populated.
    """
    result = {}
    for key, defs in FIELD_DEFS.items():
        val = cfg.get(key)
        if val is None:
            result[key] = defs["default"]
        else:
            result[key] = val
    return result
