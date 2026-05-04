"""Input directives — non-slash modifiers parsed from user input.

These differ from slash commands: slash commands control the substrate
(/checkpoint, /sleep, etc.) and the model never sees them. Directives
modify the message itself before it goes to the model — they prepare
the message, not control the client.

Currently supported:
    :image <path> [text]       Attach an image to the next user message.
                               Path may be quoted with " or ' if it contains spaces.

Examples:
    :image /path/to/photo.jpg what do you see?
    :image "/path with spaces/photo.jpg" describe this scene
    :image ~/Aletheia/Memory/IMG_3223.png
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ParsedInput:
    """Result of parsing user input for directives."""

    text: str
    image_paths: list[Path]


def parse_input(raw: str) -> ParsedInput:
    """Parse leading directives from raw user input.

    Currently extracts :image <path> directives (one or more, in sequence at
    the start of the input). Returns the remaining text and the list of
    resolved image paths (still as Path objects; caller validates existence).
    """
    text = raw
    image_paths: list[Path] = []

    while True:
        text = text.lstrip()
        if not text.startswith(":image"):
            break
        rest = text[len(":image"):].lstrip()
        if not rest:
            break

        path_str, remaining = _consume_path_token(rest)
        if path_str is None:
            break

        image_paths.append(Path(path_str).expanduser())
        text = remaining

    return ParsedInput(text=text.strip(), image_paths=image_paths)


def _consume_path_token(rest: str) -> tuple[str | None, str]:
    """Consume a path token from the start of `rest`. Path may be quoted.

    Returns (path_string_or_None, remaining_text_after_path).
    Returns (None, original_rest) if the path can't be parsed (unclosed quote, empty).
    """
    if not rest:
        return None, rest
    if rest[0] in ('"', "'"):
        quote = rest[0]
        end = rest.find(quote, 1)
        if end == -1:
            return None, rest
        return rest[1:end], rest[end + 1:].lstrip()
    space = rest.find(" ")
    if space == -1:
        return rest, ""
    return rest[:space], rest[space + 1:].lstrip()
