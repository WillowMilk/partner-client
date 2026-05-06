"""Input directives — non-slash modifiers parsed from user input.

These differ from slash commands: slash commands control the substrate
(/checkpoint, /sleep, etc.) and the model never sees them. Directives
modify the message itself before it goes to the model — they prepare
the message, not control the client.

Currently supported:
    :image <path> [text]       Attach an image to the next user message.
                               Path may be quoted with " or ' if it contains spaces.
                               Note: image paths in plain text are also auto-
                               attached when they resolve to existing image files
                               within an allowed scope; the directive remains as
                               a power-user override (e.g. for ambiguous paths).
    :clip [text]               Attach the current clipboard image (Mac: pbpaste).

Examples:
    :image /path/to/photo.jpg what do you see?
    :image "/path with spaces/photo.jpg" describe this scene
    :image ~/Aletheia/Memory/IMG_3223.png
    :clip
    :clip what does this look like to you?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedInput:
    """Result of parsing user input for directives."""

    text: str
    image_paths: list[Path] = field(default_factory=list)
    clipboard_image: bool = False


def parse_input(raw: str) -> ParsedInput:
    """Parse leading directives from raw user input.

    Extracts `:image <path>` directives (one or more) and an optional `:clip`
    directive, in any order, from the start of the input. Returns the
    remaining text plus directive results.
    """
    text = raw
    image_paths: list[Path] = []
    clipboard_image = False

    while True:
        text = text.lstrip()
        if text.startswith(":image"):
            rest = text[len(":image"):].lstrip()
            if not rest:
                break
            path_str, remaining = _consume_path_token(rest)
            if path_str is None:
                break
            image_paths.append(Path(path_str).expanduser())
            text = remaining
        elif text.startswith(":clip"):
            # Verify this is actually the directive (not e.g. ":clipboard").
            after = text[len(":clip"):]
            if after and not (after[0].isspace() or after[0] == ":"):
                break
            clipboard_image = True
            text = after.lstrip()
        else:
            break

    return ParsedInput(
        text=text.strip(),
        image_paths=image_paths,
        clipboard_image=clipboard_image,
    )


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
