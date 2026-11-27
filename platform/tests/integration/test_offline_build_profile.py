"""The load and exhaustion primitives are refused only while the contract names no profile.

A derived image is built in a profile of its own, without a network
device. The catalog lint refuses the load and exhaustion primitives until
pokus-infrastructure's contract names that profile
(`potka_core.catalog.lint.OFFLINE_BUILD_PROFILE`). The pinned contract
names its Incus profiles in one row of the Incus table of
``docs/contracts.md``; when that row changes, or the table gains or loses a
row, this test fails until the table is read again: either the constant
takes the new profile's name, or what is expected here is brought up to
date.
"""

from __future__ import annotations

import re
from pathlib import Path

from potka_core.catalog import lint

CONTRACT = Path("docs") / "contracts.md"
# The profiles of the pinned contract, none of them a build profile without a network device.
PROFILES = ["target-container", "target-vm", "probe-client", "build"]
# Everything the pinned contract's Profiles row names in code, in the row's order: the
# profiles, a setting, and a disk's two size keys with the profile that has both.
NAMED = [
    "target-container",
    "target-vm",
    "vm_targets",
    "probe-client",
    "build",
    "size",
    "size.state",
    "target-vm",
]
# What of it is no profile.
NO_PROFILES = {"vm_targets", "size", "size.state"}
# The rows of the pinned contract's Incus table, none of them about such a profile.
ROWS = [
    "API",
    "Credentials",
    "Storage pool",
    "Projects",
    "Disk budget",
    "Run networks",
    "Network ACL",
    "Firewall compatibility",
    "Profiles",
    "CPU sets",
    "Guest API",
    "Events",
    "Golden images",
]


def incus_rows(text: str) -> dict[str, str]:
    """The rows of the contract's Incus table: each item with its value."""
    section = text.split("\n## Incus\n", 1)[1].split("\n## ", 1)[0]
    table = [line.split("|") for line in section.splitlines() if line.startswith("|")]
    rows = [(cells[1].strip(), cells[2]) for cells in table[2:]]
    assert len(dict(rows)) == len(rows), rows
    return dict(rows)


def profiles(text: str) -> list[str]:
    """The names in the Profiles row of the contract's Incus table."""
    return re.findall(r"`([^`]+)`", incus_rows(text)["Profiles"])


def unchanged(text: str) -> bool:
    """Whether the contract's Profiles row names in code exactly what the pinned row does."""
    return profiles(text) == NAMED


def test_a_row_that_names_anything_more_less_or_elsewhere_is_another_row() -> None:
    def contract(names: list[str]) -> str:
        row = ", ".join(f"`{name}`" for name in names)
        return f"# Contract\n\n## Incus\n\n| Item | Value |\n| --- | --- |\n| Profiles | {row} |\n"

    assert unchanged(contract(NAMED))
    # A profile named once more, a key named once more, a name less, another order.
    for other in (
        [*NAMED, "build"],
        [*NAMED, "size"],
        NAMED[:-1],
        [NAMED[1], NAMED[0], *NAMED[2:]],
    ):
        assert not unchanged(contract(other)), other
    # The profiles are what the row names that is neither the setting nor a disk's key.
    assert [name for name in dict.fromkeys(NAMED) if name not in NO_PROFILES] == PROFILES


def test_the_profiles_row_is_read_as_the_contract_writes_it() -> None:
    text = (
        "# Contract\n\n## Incus\n\n| Item | Value |\n| --- | --- |\n"
        "| Projects | `bench`, `build` |\n"
        "| Profiles | `target-container`, `target-vm` (only with `vm_targets`), `build-x` |\n"
        "\nText after the table.\n\n## Next\n\n| Profiles | `other` |\n"
    )
    assert list(incus_rows(text)) == ["Projects", "Profiles"]
    assert profiles(text) == ["target-container", "target-vm", "vm_targets", "build-x"]


def test_the_constant_follows_the_pinned_contract_s_profiles(infra_checkout: Path) -> None:
    text = (infra_checkout / CONTRACT).read_text(encoding="utf-8")
    named = profiles(text)
    if lint.OFFLINE_BUILD_PROFILE is None:
        changed = (
            "the pinned contract's Incus table changed: if it now names the build profile"
            " without a network device, OFFLINE_BUILD_PROFILE takes its name"
        )
        assert unchanged(text), changed
        assert list(incus_rows(text)) == ROWS, changed
    else:
        assert lint.OFFLINE_BUILD_PROFILE in named
        assert lint.OFFLINE_BUILD_PROFILE not in PROFILES
