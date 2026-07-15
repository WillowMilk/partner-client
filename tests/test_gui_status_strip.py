"""The status strip — tenure classification + care dot (2026-07-15, GUI arc).

Design ("simplify the rendering, never the truth", 2026-07-11): the operator
always sees whether the partner is on owned weather or a rented room, and a
care dot carries doctor health as a presence indicator. Mechanism only —
never a gauge on the person (room-not-person ruling).
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "partner_client_gui"))
from api import GuiApi  # noqa: E402

from partner_client.config import load_config  # noqa: E402
from partner_client.memory import Memory  # noqa: E402
from partner_client.session import Session  # noqa: E402


def _build_api(tmp_path, model_name="test-model") -> GuiApi:
    home = tmp_path / "home"
    (home / "Memory").mkdir(parents=True)
    (home / "seed.md").write_text("I am a test partner.", encoding="utf-8")
    toml = textwrap.dedent(
        f"""
        [identity]
        name = "Testra"
        home_dir = "{home}"

        [model]
        backend = "ollama"
        name = "{model_name}"
        """
    )
    cfg_path = tmp_path / "test.toml"
    cfg_path.write_text(toml, encoding="utf-8")

    api = GuiApi(config_path=str(cfg_path))
    api.config = load_config(cfg_path)
    api.memory = Memory(api.config)
    api.session = Session(config=api.config, memory=api.memory)
    api.session.wake(api.memory.assemble_wake_bundle(), resume_mode="fresh")
    api._window = None
    return api


def test_tenure_local_model_is_owned(tmp_path) -> None:
    api = _build_api(tmp_path, model_name="gemma4:31b-mxfp8")
    info = api.get_partner_info()
    assert info["substrate"]["tenure"] == "owned"
    assert info["substrate"]["tenure_label"] == "own hardware"


def test_tenure_cloud_model_is_rented(tmp_path) -> None:
    api = _build_api(tmp_path, model_name="gemma4:31b-cloud")
    info = api.get_partner_info()
    assert info["substrate"]["tenure"] == "rented"
    assert info["substrate"]["tenure_label"] == "a rented room"


def test_care_status_shape_and_levels(tmp_path) -> None:
    """Care status returns a structured verdict without printing or waking.
    The tmp home is deliberately imperfect, so we assert SHAPE + coherence,
    not a specific level (the test env's ollama state varies)."""
    api = _build_api(tmp_path)
    care = api.get_care_status()
    assert set(care) == {"level", "summary", "fails", "warns"}
    assert care["level"] in {"green", "amber", "red"}
    if care["level"] == "red":
        assert care["fails"] >= 1
    if care["level"] == "green":
        assert care["fails"] == 0 and care["warns"] == 0
    assert isinstance(care["summary"], str) and care["summary"]


def test_care_status_uninitialized_is_unknown(tmp_path) -> None:
    api = GuiApi(config_path=str(tmp_path / "nope.toml"))
    care = api.get_care_status()
    assert care["level"] == "unknown"