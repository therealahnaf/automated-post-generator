"""Select reusable local Bits Today backgrounds for Pillow post renderers."""

from __future__ import annotations

import hashlib
import random
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BACKGROUND_DIR = PROJECT_ROOT / "assets" / "fonts" / "images"


def list_backgrounds(directory: Path = DEFAULT_BACKGROUND_DIR) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Background directory not found: {directory}")
    backgrounds = sorted(
        path for path in directory.glob("bg-*.png") if path.is_file()
    )
    if not backgrounds:
        raise FileNotFoundError(f"No bg-*.png images found in {directory}")
    return backgrounds


def stable_seed(*values: str) -> int:
    """Return a stable pseudo-random seed for a post and its language variants."""

    material = "\0".join(values).encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def select_backgrounds(
    backgrounds: list[Path],
    count: int,
    seed: int,
) -> list[Path]:
    if not backgrounds:
        raise ValueError("At least one background is required.")
    if count < 1:
        raise ValueError("Background count must be positive.")

    rng = random.Random(seed)
    selected: list[Path] = []
    for _ in range(count):
        choices = backgrounds
        if len(backgrounds) > 1 and selected:
            choices = [
                background
                for background in backgrounds
                if background != selected[-1]
            ]
        selected.append(rng.choice(choices))
    return selected
