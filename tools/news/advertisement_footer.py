"""Shared advertisement entry point for module and direct-script execution."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from advertisement.thebitstoday.footer import (  # noqa: E402
    BANNER_SIZE,
    DEFAULT_BANNER,
    REFERENCE_WIDTH,
    apply_footer,
    draw_footer,
    footer_height,
    footer_top,
    load_banner,
    make_footer_layer,
)

__all__ = [
    "BANNER_SIZE", "DEFAULT_BANNER", "REFERENCE_WIDTH", "apply_footer",
    "draw_footer", "footer_height", "footer_top", "load_banner", "make_footer_layer",
]
