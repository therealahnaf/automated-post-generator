from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from string import Template

CONTEXT_SECTION = "\n## X tweet context\n"
PLACEHOLDER = "{{tweet_context}}"


@dataclass(frozen=True)
class PromptSet:
    story: str
    first_image: str
    next_image: str

    @classmethod
    def load(cls, directory: Path) -> PromptSet:
        return cls(
            *(
                (directory / name).read_text(encoding="utf-8-sig")
                for name in ("story.md", "image_first.md", "image_next.md")
            )
        )

    def hashes(self) -> dict[str, str]:
        return {
            name: hashlib.sha256(text.encode()).hexdigest()
            for name, text in (
                ("story", self.story),
                ("first_image", self.first_image),
                ("next_image", self.next_image),
            )
        }

    def story_messages(
        self, source: dict, instructions: str | None
    ) -> list[dict[str, str]]:
        if self.story.count(CONTEXT_SECTION) != 1:
            raise ValueError("Story prompt must have one source context section")
        system, user = self.story.split(CONTEXT_SECTION, 1)
        if not system.strip() or PLACEHOLDER in system or user.count(PLACEHOLDER) != 1:
            raise ValueError(
                "Story prompt must separate trusted instructions and source data"
            )
        if instructions:
            system += "\n\nAdditional trusted writing instructions:\n" + instructions
        return [
            {"role": "system", "content": system.strip()},
            {
                "role": "user",
                "content": user.replace(
                    PLACEHOLDER, json.dumps(source, ensure_ascii=False)
                ),
            },
        ]

    def image_prompt(self, segments: list[dict], position: int) -> str:
        fields = ("index", "text", "tag", "kind", "active_tags", "tag_only")

        def context(index: int) -> str:
            value = (
                {name: segments[index][name] for name in fields}
                if 0 <= index < len(segments)
                else None
            )
            return json.dumps(value, ensure_ascii=False)

        return Template(
            self.first_image if position == 0 else self.next_image
        ).substitute(
            current_segment=context(position),
            previous_segment=context(position - 1),
            next_segment=context(position + 1),
            aspect_ratio="16:9",
            reference_roles="Image 1 is the ORIGINAL STYLE REFERENCE ONLY. No generated images are attached.",
        )
