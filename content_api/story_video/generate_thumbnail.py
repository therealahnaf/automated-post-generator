"""Generate a source-grounded two-reference Qwen thumbnail (no title call).

Run from the repository root:
    python -m content_api.story_video.generate_thumbnail X_URL --output-dir output/story-thumbnails/example

This standalone experiment does not modify the video pipeline, generate audio,
publish, or upload. Existing output directories are refused to avoid paid reruns.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .errors import PipelineError
from .packaging import generate_thumbnail_from_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Public X/Twitter status URL")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="A new directory for this generation",
    )
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error(
            "Output directory already exists; inspect its results before explicitly choosing another paid run."
        )
    try:
        result = generate_thumbnail_from_url(args.url, args.output_dir)
    except PipelineError as exc:
        print(f"Thumbnail generation failed: {exc.code}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- keep HTTP credentials and provider details private
        print(
            f"Thumbnail generation failed ({type(exc).__name__}); inspect the saved status.",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "thumbnail": str(
                    (args.output_dir / result["thumbnail_file"]).resolve()
                ),
                "metadata": str((args.output_dir / "packaging.json").resolve()),
            },
            indent=2,
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
