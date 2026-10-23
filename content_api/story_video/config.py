from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .. import (
    config as _content_config,  # noqa: F401  -- load private dotenv configuration
)

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[1]
STORY_MODEL = "deepseek/deepseek-v4-flash-0731"
AUDIO_MODEL = "fish-audio/drama-3"
IMAGE_MODEL = "qwen/qwen-image-2.1-turbo"
VOICE = "bf322df2096a46f18c579d0baa36f41d"
DEFAULT_LOGO = DIRECTORY / "assets" / "tft-logo-trans.png"
MIN_WORDS = 1000
MAX_WORDS = 3000
MAX_SEGMENTS = 80
LEASE_SECONDS = 120


@dataclass(frozen=True)
class Settings:
    output_dir: Path
    api_key: str = field(repr=False)
    queue_limit: int = 20
    prompts_dir: Path = DIRECTORY / "prompts"
    reference_image: Path = DIRECTORY / "assets" / "style-reference.png"
    logo_image: Path = DEFAULT_LOGO

    @classmethod
    def from_env(cls) -> Settings:
        configured = os.getenv("STORY_VIDEO_OUTPUT_DIR") or str(
            ROOT / "output" / "story-videos"
        )
        output = Path(configured).expanduser()
        if not output.is_absolute():
            output = ROOT / output
        return cls(
            output_dir=output.resolve(),
            api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        )
