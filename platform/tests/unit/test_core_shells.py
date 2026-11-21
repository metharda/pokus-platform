"""The one list of shells, and what keeps a shell off a command with it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from potka_core import shells
from potka_core.catalog.blueprint import Patchable
from potka_core.catalog.lint import lint
from potka_testing.fixture_catalog import copy_fixture, edit_yaml
from potka_testing.tool_views import LIBRARY, TOOLS
from potka_tools import context, template
from pydantic import ValidationError


@pytest.mark.parametrize(
    "word", ["sh", "bash", "/bin/sh", "/usr/bin/bash", "./dash", "a/b/zsh", "busybox", "rbash"]
)
def test_a_word_whose_last_part_is_a_shell_s_name_is_a_shell(word: str) -> None:
    assert shells.is_shell(word)


@pytest.mark.parametrize(
    "word", ["nginx", "-c", "bash.sh", "sh/", "/bin/sh/x", "shell", "SH", "", "python3", "env"]
)
def test_another_word_is_none(word: str) -> None:
    assert not shells.is_shell(word)


def test_the_list_is_one_object_for_everything_that_uses_it() -> None:
    assert len(shells.SHELLS) == 18 and {"sh", "bash", "dash", "busybox"} <= shells.SHELLS
    # The registry's templates and a run's context ask the same function.
    assert vars(template)["is_shell"] is shells.is_shell
    assert vars(context)["is_shell"] is shells.is_shell
    assert not hasattr(template, "SHELLS")


def patchable(validator: Any) -> Patchable:
    """A patchable path as a catalog's file gives it."""
    return Patchable.model_validate_json(json.dumps({"path": "/etc/nginx", "validator": validator}))


@pytest.mark.parametrize(
    "validator",
    [["sh", "-c", "nginx -t"], ["/bin/bash", "check"], ["nginx", "-t", "/usr/bin/dash"]],
)
def test_a_blueprint_s_validator_is_never_a_shell_s(validator: list[str]) -> None:
    with pytest.raises(ValidationError) as refused:
        patchable(validator)
    assert "a validator is a program and its arguments, never a shell's" in str(refused.value)
    # A run's context refuses the same words in the same words.
    with pytest.raises(ValueError, match="never a shell's"):
        context._command(tuple(validator))


def test_a_catalog_whose_blueprint_has_a_shell_for_a_validator_has_a_finding(
    tmp_path: Path,
) -> None:
    catalog = copy_fixture(tmp_path / "catalog")
    name = "blueprints/fixture-web.yaml"
    assert lint(catalog, TOOLS, LIBRARY) == []

    def shell(data: Any) -> None:
        patched = data["nodes"][0]["patchable_paths"][0]
        assert patched["validator"] == ["nginx", "-t"]
        patched["validator"] = ["/bin/sh", "-c", "nginx -t zq-marked"]

    edit_yaml(catalog, name, shell)
    findings = [(finding.path, finding.rule) for finding in lint(catalog, TOOLS, LIBRARY)]
    assert (name, "format") in findings
    said = " ".join(finding.message for finding in lint(catalog, TOOLS, LIBRARY))
    assert "never a shell's" in said and "zq-marked" not in said


def test_a_validator_without_a_shell_and_no_validator_are_taken() -> None:
    assert patchable(["nginx", "-t"]).validator == ("nginx", "-t")
    assert patchable(None).validator is None
    assert context._command(("nginx", "-t")) == ("nginx", "-t")
