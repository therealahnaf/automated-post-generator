"""Compatibility adapter for the historical shared footer entry point."""

try:
    from .advertisement_footer import (
        DEFAULT_BANNER, apply_footer, draw_footer, footer_height, footer_top,
        load_banner, make_footer_layer,
    )
except ImportError:
    from advertisement_footer import (
        DEFAULT_BANNER, apply_footer, draw_footer, footer_height, footer_top,
        load_banner, make_footer_layer,
    )

__all__ = [
    "DEFAULT_BANNER", "apply_footer", "draw_footer", "footer_height",
    "footer_top", "load_banner", "make_footer_layer",
]
