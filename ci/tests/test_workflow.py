from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def run_scripts(node: object, where: str) -> Iterator[tuple[str, str]]:
    """Every ``run:`` script in a workflow, with where it is."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "run" and isinstance(value, str):
                yield where, value
            else:
                yield from run_scripts(value, f"{where}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from run_scripts(item, f"{where}[{index}]")


def test_no_run_script_holds_an_expression() -> None:
    # A value of the event reaches a step only through env:, never pasted
    # into the script the shell runs.
    files = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
    if not files:
        pytest.skip("no workflow in .github/workflows")
    scripts = [
        (f"{path.name}:{where}", script)
        for path in files
        for where, script in run_scripts(yaml.safe_load(path.read_text(encoding="utf-8")), "")
    ]
    assert scripts
    assert [where for where, script in scripts if "${{" in script] == []


def test_the_required_job_needs_every_other_job() -> None:
    workflow = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    jobs = set(workflow["jobs"])
    assert "generated" in jobs
    assert set(workflow["jobs"]["required"]["needs"]) == jobs - {"required"}


def test_the_walk_finds_an_expression_in_a_run_script() -> None:
    workflow = yaml.safe_load(
        "jobs:\n  a:\n    steps:\n      - run: echo ok\n      - run: echo ${{ github.ref_name }}\n"
    )
    found = [where for where, script in run_scripts(workflow, "") if "${{" in script]
    assert found == [".jobs.a.steps[1]"]
