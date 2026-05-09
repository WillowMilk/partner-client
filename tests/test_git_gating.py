from __future__ import annotations

from partner_client.client import is_git_push_allowlisted


def test_git_push_allowlist_requires_configured_match() -> None:
    assert not is_git_push_allowlisted("https://github.com/WillowMilk/partner-client.git", [])
    assert not is_git_push_allowlisted(
        "https://github.com/WillowMilk/partner-client.git",
        ["github.com/other/repo"],
    )
    assert is_git_push_allowlisted(
        "https://github.com/WillowMilk/partner-client.git",
        ["github.com/WillowMilk/partner-client"],
    )
