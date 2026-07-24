# -*- coding: utf-8 -*-
"""Thin wrappers for API/headless rendering using the shared renderer classes.

These functions accept pre-processed PIL Images + a config dict,
and return a rendered PIL Image — no file I/O.
Used by api/main.py to replace inline renderers.
"""

from .renderers_135 import Renderer135
from .renderers_120 import Renderer120
from .renderer_stub import FilmProcessorStub


def render_135(images, config, is_preview=False):
    """Render a 135 film sheet and return a PIL Image."""
    stub = FilmProcessorStub(config)
    renderer = Renderer135(
        config, stub, images,
        status_callback=lambda _: None,
        progress_callback=lambda *_: None,
        is_preview=is_preview,
    )
    return renderer.render()


def render_120(images, config, is_preview=False):
    """Render a 120 film sheet and return a PIL Image."""
    stub = FilmProcessorStub(config)
    renderer = Renderer120(
        config, stub, images,
        status_callback=lambda _: None,
        progress_callback=lambda *_: None,
        is_preview=is_preview,
    )
    return renderer.render()
