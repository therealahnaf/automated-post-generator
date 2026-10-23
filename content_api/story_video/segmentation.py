"""Parse tag boundaries and produce independently speakable narration segments."""

from __future__ import annotations

import re
from typing import Any

TAG_PATTERN = re.compile(
    r"\[[^\[\]\r\n]+\]|</?(?:whisper|emphasis|soft|fast|slow|stress|forceful)>|<\|speaker:\d+\|>",
    re.IGNORECASE,
)


def split_story(story: str) -> list[dict[str, Any]]:
    """Preserve every recognized boundary and original character offset."""
    if not story.strip():
        raise ValueError("Story is empty")
    matches = list(TAG_PATTERN.finditer(story))
    spans = []
    prefix = matches[0].start() if matches else len(story)
    if story[:prefix].strip() or not matches:
        spans.append((0, prefix, None))
    for index, match in enumerate(matches):
        start = match.start() if spans else 0
        end = matches[index + 1].start() if index + 1 < len(matches) else len(story)
        spans.append((start, end, match.group()))
    active, direction, segments = [], None, []
    for start, end, tag in spans:
        raw = story[start:end]
        text, kind = raw.strip(), "untagged"
        if tag:
            text = text[len(tag) :].strip()
            if tag.startswith("["):
                direction, kind = tag, "direction"
            elif tag.startswith("<|"):
                kind = "speaker"
            elif tag.startswith("</"):
                kind = "delivery_close"
                opening = tag.replace("</", "<", 1).lower()
                if not active or active[-1].lower() != opening:
                    raise ValueError(f"Unmatched or misnested delivery tag: {tag}")
                active.pop()
            else:
                kind = "delivery_open"
                active.append(tag)
        segments.append(
            {
                "index": len(segments) + 1,
                "tag": tag,
                "kind": kind,
                "active_tags": ([direction] if direction else []) + list(active),
                "text": text,
                "tag_only": not bool(text),
                "raw_text": raw,
                "source_start": start,
                "source_end": end,
            }
        )
    if active:
        raise ValueError(f"Unclosed delivery tag: {active[-1]}")
    return segments


def spoken_word_count(story: str) -> int:
    return len(re.findall(r"\b[\w]+(?:['’\-][\w]+)*\b", TAG_PATTERN.sub(" ", story)))


def narration_segments(story: str) -> tuple[list[dict], list[dict]]:
    """Carry tag-only beats into adjacent speech, never synthesize an empty clip.

    Reapply active directions and balance delivery tags for each isolated TTS
    request. Preserve the full original boundary list for audit/reconstruction.
    """
    boundaries = split_story(story)
    segments, pending = [], []
    for boundary in boundaries:
        if boundary["kind"] == "speaker":
            raise ValueError(
                "Only one narrator is supported; speaker markers are not allowed"
            )
        if boundary["tag_only"]:
            pending.append(boundary)
            continue
        tags = boundary["active_tags"]
        cues = [item["tag"] for item in pending if item["kind"] == "direction"]
        directions = [tag for tag in tags if tag.startswith("[")]
        if directions and (not cues or directions[-1] != cues[-1]):
            cues.extend(directions)
        openings = [tag for tag in tags if tag.startswith("<")]
        closings = [tag.replace("<", "</", 1) for tag in reversed(openings)]
        tts_input = " ".join(
            [*cues, "".join(openings) + boundary["text"] + "".join(closings)]
        )
        segments.append(
            {
                "index": len(segments) + 1,
                "text": boundary["text"],
                "tts_input": tts_input,
                "active_tags": tags,
                "source_boundaries": [item["index"] for item in pending]
                + [boundary["index"]],
                "tag": boundary["tag"],
                "kind": "narration",
                "tag_only": False,
            }
        )
        pending = []
    if not segments:
        raise ValueError("Story contains no spoken words")
    if pending:
        trailing = [item["tag"] for item in pending if item["kind"] == "direction"]
        segments[-1]["tts_input"] += (" " + " ".join(trailing)) if trailing else ""
        segments[-1]["source_boundaries"].extend(item["index"] for item in pending)
    return boundaries, segments
