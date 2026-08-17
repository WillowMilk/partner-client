"""Wire-level adaptation for template-strict substrates (2026-08-17).

Regression for Aletheia's first night on Qwen3.8: the checkpoint flow
appended its system marker mid-conversation (correct for the record);
Qwen's chat template raises "System message must be at the beginning"
on any non-leading system message, and every request after it 500'd.
The record keeps true roles; the wire adapts. Both backends.
"""
from __future__ import annotations

from partner_client.client import adapt_midstream_system_for_wire


def test_leading_system_block_untouched():
    msgs = [
        {"role": "system", "content": "wake bundle"},
        {"role": "system", "content": "[SESSION NUM:31]"},
        {"role": "system", "content": "[The following are the last few exchanges...]"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "warmly"},
    ]
    out = adapt_midstream_system_for_wire(msgs)
    assert [m["role"] for m in out] == ["system", "system", "system", "user", "assistant"]
    assert out[0]["content"] == "wake bundle"


def test_her_exact_session_shape_renders_no_midstream_system():
    """The night's actual failure: [MOSAIC /checkpoint invoked by Willow]
    at index 30 of a 33-message session."""
    msgs = (
        [{"role": "system", "content": f"lead-{i}"} for i in range(4)]
        + [{"role": "user", "content": "u"}, {"role": "assistant", "content": "a"}] * 13
        + [{"role": "system", "content": "[MOSAIC /checkpoint invoked by Willow] ..."}]
        + [{"role": "user", "content": "her next message"}]
    )
    out = adapt_midstream_system_for_wire(msgs)
    first_non_system = next(i for i, m in enumerate(out) if m["role"] != "system")
    assert all(m["role"] != "system" for m in out[first_non_system:]), (
        "a mid-stream system message survived to the wire"
    )
    # The notice still travels — as an enveloped user message, clearly not-Willow
    adapted = out[-2]
    assert adapted["role"] == "user"
    assert adapted["content"].startswith("[house notice — mechanical")
    assert "[MOSAIC /checkpoint invoked by Willow]" in adapted["content"]


def test_record_never_mutated():
    msgs = [
        {"role": "system", "content": "lead"},
        {"role": "user", "content": "u"},
        {"role": "system", "content": "[SUBSTRATE CHANGED] ..."},
    ]
    adapt_midstream_system_for_wire(msgs)
    assert msgs[2]["role"] == "system", "the session record must keep true roles"
    assert msgs[2]["content"] == "[SUBSTRATE CHANGED] ..."
