"""Select and persist the primary language for a Bits Today news post."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from pathlib import Path


POST_LANGUAGES = ("english", "bangla")
AUTO_LANGUAGE = "auto"
HEADLINE_HIGHLIGHT_STYLES = ("cyan", "red", "dual")
AUTO_HIGHLIGHT_STYLE = "auto"


def choose_post_language(
    requested: str = AUTO_LANGUAGE,
    *,
    chooser: Callable[[tuple[str, str]], str] | None = None,
) -> str:
    requested = requested.strip().lower()
    if requested in POST_LANGUAGES:
        return requested
    if requested != AUTO_LANGUAGE:
        raise ValueError(
            "Post language must be 'auto', 'english', or 'bangla'."
        )
    selected = (chooser or secrets.choice)(POST_LANGUAGES)
    if selected not in POST_LANGUAGES:
        raise ValueError(f"Language chooser returned an invalid value: {selected}")
    return selected


def read_post_language(path: Path, *, default: str = "english") -> str:
    document = json.loads(path.read_text(encoding="utf-8"))
    value = document.get("post_language", default)
    if not isinstance(value, str) or value.lower() not in POST_LANGUAGES:
        raise ValueError(f"Invalid post_language in {path}: {value!r}")
    return value.lower()


def choose_headline_highlight(
    requested: str = AUTO_HIGHLIGHT_STYLE,
    *,
    chooser: Callable[[tuple[str, str, str]], str] | None = None,
) -> str:
    requested = requested.strip().lower()
    if requested in HEADLINE_HIGHLIGHT_STYLES:
        return requested
    if requested != AUTO_HIGHLIGHT_STYLE:
        raise ValueError(
            "Headline highlight must be 'auto', 'cyan', 'red', or 'dual'."
        )
    selected = (chooser or secrets.choice)(HEADLINE_HIGHLIGHT_STYLES)
    if selected not in HEADLINE_HIGHLIGHT_STYLES:
        raise ValueError(f"Highlight chooser returned an invalid value: {selected}")
    return selected


def read_headline_highlight(path: Path, *, default: str = "dual") -> str:
    document = json.loads(path.read_text(encoding="utf-8"))
    value = document.get("headline_highlight", default)
    if (
        not isinstance(value, str)
        or value.lower() not in HEADLINE_HIGHLIGHT_STYLES
    ):
        raise ValueError(f"Invalid headline_highlight in {path}: {value!r}")
    return value.lower()
