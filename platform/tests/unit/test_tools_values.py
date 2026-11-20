"""What a value may hold, and paths by their text: the normal form, whole components."""

from __future__ import annotations

import pytest
from potka_tools import values


def test_a_string_is_printable_ascii_and_a_text_any_printing_character() -> None:
    assert all(values.printable(text) for text in ("a", " ", "~", "a b/c.d-e_f@g"))
    for text in ("", "a\n", "\ta", "a\x00", "a\x7f", "caf\u00e9", "a\u202eb", "\ud800"):
        assert not values.printable(text), repr(text)
    assert all(values.is_text(text) for text in ("a", "a\nb\tc", "caf\u00e9", "\u4e2d\u6587", " "))
    for text in ("", "a\rb", "a\x00", "a\x1b[0m", "a\x7f", "a\u0085b", "a\u200bb", "a\u202eb"):
        assert not values.is_text(text), repr(text)
    for text in ("a\u2028b", "a\u2029b", "a\ud800b", "a\ue000b", "a\U000e0001b", "a\uffffb"):
        assert not values.is_text(text), repr(text)


def test_paths_are_compared_by_whole_components() -> None:
    assert all(values.is_normal(path) for path in ("/a", "/a/b", "/a.b/..c", "/ a"))
    # A component of more than 255 bytes is in no normal form, as normalize has none for it.
    assert values.is_normal("/a/" + "b" * 255) and not values.is_normal("/a/" + "b" * 256)
    assert not values.is_normal("/a/" + "\u00e9" * 128) and values.is_normal("/\u00e9")
    for path in ("/", "", "a", "a/b", "/a/", "//a", "/a//b", "/a/./b", "/a/../b", "/a/.."):
        assert not values.is_normal(path), path
    roots = ("/etc/nginx", "/srv")
    assert values.within("/etc/nginx", roots) and values.within("/etc/nginx/conf.d/a", roots)
    assert not values.within("/etc/nginx2", roots) and not values.within("/etc", roots)
    assert values.below("/srv/a", roots) and not values.below("/srv", roots)
    assert not values.below("/srvx/a", roots)
    assert [values.refused(p) for p in ("/dev", "/proc/1/environ", "/sys/fs", "/devices", "/")] == [
        True,
        True,
        True,
        False,
        False,
    ]


@pytest.mark.parametrize(
    ("path", "normal"),
    [
        ("/", "/"),
        ("//", "/"),
        ("/a", "/a"),
        ("/a/", "/a"),
        ("//a///b//", "/a/b"),
        ("/a/./b/.", "/a/b"),
        ("/a/b/..", "/a"),
        ("/a/../b", "/b"),
        ("/a/b/../../c", "/c"),
        ("/a/..", "/"),
        ("/...", "/..."),
        ("/a b/.c", "/a b/.c"),
        ("/" + "a" * 255, "/" + "a" * 255),
        ("/" + "a" * 256, None),
        ("/" + "\u00e9" * 128, None),
        ("/..", None),
        ("/a/../..", None),
        ("/../a", None),
        ("", None),
        ("a", None),
        ("./a", None),
        ("a/b", None),
    ],
)
def test_a_path_s_normal_form_is_made_by_its_text_alone(path: str, normal: str | None) -> None:
    assert values.normalize(path) == normal
    if normal not in (None, "/"):
        assert values.is_normal(normal) and values.normalize(normal) == normal
