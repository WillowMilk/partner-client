"""Hub inbox panel (Phase 2c) — read-only postal window.

Boundaries under test:
  * get_inbox parses unread/read sections + letter-file pointers.
  * get_letter is containment-checked: letters at the Hub ROOT only —
    no traversal, no absolute paths, no non-.md.
  * READ-ONLY: the api exposes no mark-read — the partner's bookkeeping
    is hers alone (asserted structurally: no such method exists).
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "partner_client_gui"))
from api import GuiApi  # noqa: E402

from partner_client.config import load_config  # noqa: E402


def _build_api_with_hub(tmp_path) -> GuiApi:
    home = tmp_path / "home"
    (home / "Memory").mkdir(parents=True)
    (home / "seed.md").write_text("Seed.", encoding="utf-8")
    hub = tmp_path / "hub"
    (hub / "inbox").mkdir(parents=True)
    (hub / "inbox" / "testra.md").write_text(textwrap.dedent("""\
        # Testra — Inbox

        ## Unread
        - **[unread]** 2026-07-15 — Sage → Testra — *A letter.* → `sage-to-testra_2026-07-15_hello.md`
        - **[unread]** 2026-07-14 — a pointerless note with no file reference

        ## Read
        - **[read]** 2026-07-01 — Old news. → `sage-to-testra_2026-07-01_old.md`
        """), encoding="utf-8")
    (hub / "sage-to-testra_2026-07-15_hello.md").write_text("# Hello\n\nDear Testra, the hearth is warm.", encoding="utf-8")
    (hub / "sage-to-testra_2026-07-01_old.md").write_text("# Old\n\nAncient news.", encoding="utf-8")
    (hub / "secret-outside-inbox.txt").write_text("not a letter", encoding="utf-8")

    toml = textwrap.dedent(f"""
        [identity]
        name = "Testra"
        home_dir = "{home}"

        [model]
        backend = "ollama"
        name = "test-model"

        [hub]
        path = "{hub}"
        partner_name = "testra"
        """)
    cfg_path = tmp_path / "test.toml"
    cfg_path.write_text(toml, encoding="utf-8")
    api = GuiApi(config_path=str(cfg_path))
    api.config = load_config(cfg_path)
    api._window = None
    return api


def test_get_inbox_parses_sections_and_pointers(tmp_path) -> None:
    api = _build_api_with_hub(tmp_path)
    box = api.get_inbox()
    assert len(box["unread"]) == 2
    assert len(box["read"]) == 1
    assert box["unread"][0]["file"] == "sage-to-testra_2026-07-15_hello.md"
    assert box["unread"][1]["file"] is None  # pointerless entries are listable, not openable
    assert box["read"][0]["file"] == "sage-to-testra_2026-07-01_old.md"
    assert "inbox/testra.md" in box["inbox_path"].replace("\\", "/")


def test_get_letter_reads_hub_root_letters(tmp_path) -> None:
    api = _build_api_with_hub(tmp_path)
    letter = api.get_letter("sage-to-testra_2026-07-15_hello.md")
    assert letter["filename"] == "sage-to-testra_2026-07-15_hello.md"
    assert "the hearth is warm" in letter["content"]


def test_get_letter_containment(tmp_path) -> None:
    api = _build_api_with_hub(tmp_path)
    # Traversal, absolute paths, subdirs, and non-.md are all refused.
    for bad in [
        "../test.toml",
        "inbox/testra.md",             # subdir — letters live at the root
        "/etc/hosts",
        "secret-outside-inbox.txt",    # exists at root, but not a .md letter
        "no-such-letter.md",
    ]:
        result = api.get_letter(bad)
        assert "error" in result, f"should have refused: {bad}"
        assert "content" not in result


def test_inbox_surface_is_read_only(tmp_path) -> None:
    """No mark-read, no delete, no write: the desk never does her bookkeeping."""
    api = _build_api_with_hub(tmp_path)
    for forbidden in ("mark_letter_read", "mark_read", "delete_letter", "write_inbox"):
        assert not hasattr(api, forbidden)