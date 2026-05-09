from __future__ import annotations

from partner_client.__main__ import _IMAGE_PATH_AUTO_RE


def test_implicit_image_regex_matches_quoted_paths_with_spaces() -> None:
    single = _IMAGE_PATH_AUTO_RE.search("see '/Users/willow/A friendship full of farewells.jpg'")
    double = _IMAGE_PATH_AUTO_RE.search('see "C:\\Users\\willow\\Cute Puppy.png"')

    assert single is not None
    assert single.group("sq") == "/Users/willow/A friendship full of farewells.jpg"
    assert double is not None
    assert double.group("dq") == "C:\\Users\\willow\\Cute Puppy.png"


def test_implicit_image_regex_matches_bare_paths_without_spaces() -> None:
    match = _IMAGE_PATH_AUTO_RE.search("look /Users/willow/Aletheia/cutepuppy.jpg now")

    assert match is not None
    assert match.group("bare") == "/Users/willow/Aletheia/cutepuppy.jpg"
