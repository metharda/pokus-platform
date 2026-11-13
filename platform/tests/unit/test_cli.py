"""The potka command: catalog lint, its output and its exit status."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from potka_cli.main import main
from potka_core.catalog.loader import load_catalog
from potka_testing.fixture_catalog import FIXTURE_CATALOG, copy_fixture, edit_yaml

ONE = "scenarios/FIXTURE-001/1"


@pytest.fixture
def broken(tmp_path: Path) -> Path:
    """A copy of the fixture catalog with two findings."""
    root = copy_fixture(tmp_path / "catalog")
    edit_yaml(root, f"{ONE}/scenario.yaml", lambda d: d.update(confirm=[], blueprint="gone"))
    return root


def test_the_fixture_catalog_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["catalog", "lint", str(FIXTURE_CATALOG)]) == 0
    assert capsys.readouterr() == ("no finding\n", "")
    assert main(["catalog", "lint", "--json", str(FIXTURE_CATALOG)]) == 0
    assert capsys.readouterr() == ('{"findings": []}\n', "")


def test_the_path_defaults_to_the_current_directory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.chdir(FIXTURE_CATALOG)
    assert main(["catalog", "lint"]) == 0
    assert capsys.readouterr() == ("no finding\n", "")
    monkeypatch.chdir(tmp_path)
    assert main(["catalog", "lint"]) == 1
    assert capsys.readouterr() == ("taxonomy.yaml: format: missing\n1 finding(s)\n", "")


def test_findings_are_printed_one_per_line_with_their_count(
    broken: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["catalog", "lint", str(broken)]) == 1
    assert capsys.readouterr() == (
        "scenarios/FIXTURE-001/1/scenario.yaml: blueprint: the blueprint gone is not in"
        " blueprints/\n"
        "scenarios/FIXTURE-001/1/scenario.yaml: probes: no confirm probe\n"
        "2 finding(s)\n",
        "",
    )


def test_json_output_is_one_object_with_every_finding(
    broken: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["catalog", "lint", str(broken), "--json"]) == 1
    out, err = capsys.readouterr()
    assert err == ""
    assert out.count("\n") == 1
    assert json.loads(out) == {
        "findings": [
            {
                "path": "scenarios/FIXTURE-001/1/scenario.yaml",
                "rule": "blueprint",
                "message": "the blueprint gone is not in blueprints/",
            },
            {
                "path": "scenarios/FIXTURE-001/1/scenario.yaml",
                "rule": "probes",
                "message": "no confirm probe",
            },
        ]
    }


def test_a_path_that_is_no_catalog_is_a_finding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["catalog", "lint", str(tmp_path / "missing")]) == 1
    assert capsys.readouterr() == (".: format: missing\n1 finding(s)\n", "")
    (tmp_path / "file").write_text("x")
    assert main(["catalog", "lint", str(tmp_path / "file")]) == 1
    assert capsys.readouterr() == (".: format: not a directory\n1 finding(s)\n", "")


def test_a_catalog_that_cannot_be_read_gives_findings_and_no_traceback(
    broken: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lstat = os.lstat

    def refuse(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if Path(path).name in ("taxonomy.yaml", "blueprints", "scenarios"):
            raise PermissionError(13, "Permission denied", str(path))
        return lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", refuse)
    assert main(["catalog", "lint", str(broken)]) == 1
    assert capsys.readouterr() == (
        "blueprints: format: cannot be read (PermissionError)\n"
        "scenarios: format: cannot be read (PermissionError)\n"
        "taxonomy.yaml: format: cannot be read (PermissionError)\n"
        "3 finding(s)\n",
        "",
    )


def test_a_name_that_cannot_be_printed_stays_on_one_line(
    broken: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (broken / "blueprints" / "two\nlines.yaml").write_text("x")
    os.mkdir(os.fsencode(broken / "scenarios") + b"/FIXTURE-\xff01")
    assert main(["catalog", "lint", str(broken)]) == 1
    out, err = capsys.readouterr()
    assert err == ""
    assert out.encode("utf-8").decode("utf-8") == (
        "blueprints/two?lines.yaml: format: a name that is not printable UTF-8\n"
        "scenarios/FIXTURE-?01: format: a name that is not printable UTF-8\n"
        "2 finding(s)\n"
    )
    assert main(["catalog", "lint", "--json", str(broken)]) == 1
    assert len(json.loads(capsys.readouterr().out)["findings"]) == 2


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        ([], "the following arguments are required: COMMAND"),
        (["catalog"], "the following arguments are required: ACTION"),
        (["lint"], "argument COMMAND: invalid choice: 'lint'"),
        (["catalog", "show"], "argument ACTION: invalid choice: 'show'"),
        (["catalog", "lint", "a", "b"], "unrecognized arguments: b"),
        (["catalog", "lint", "--strict"], "unrecognized arguments: --strict"),
        (["catalog", "lint", "--json=yes"], "argument --json: ignored explicit argument 'yes'"),
    ],
)
def test_a_command_line_that_is_not_understood_ends_with_status_2(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(argv)
    assert caught.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("usage: potka ")
    assert message in err


def test_help_names_the_command_and_its_exit_status(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["catalog", "lint", "--help"])
    assert caught.value.code == 0
    out = " ".join(capsys.readouterr().out.split())
    assert out.startswith("usage: potka catalog lint [-h] [--json] [PATH]")
    assert (
        "Check a catalog checkout: its layout and the form of each of its files and, once every"
        " file has its form, what the files say about each other. Exit status: 0 without a"
        " finding, 1 with findings (a path that is no catalog is one), 2 for a command line"
        " that is not understood."
    ) in out
    assert "the catalog's root (default: the current directory)" in out
    assert "print the findings as one JSON object" in out


@pytest.mark.parametrize(
    ("argv", "usage", "listed"),
    [
        (["--help"], "usage: potka [-h] COMMAND ...", "catalog work on a catalog checkout"),
        (
            ["catalog", "--help"],
            "usage: potka catalog [-h] ACTION ...",
            "lint check a catalog checkout",
        ),
    ],
)
def test_help_lists_the_commands(
    argv: list[str], usage: str, listed: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as caught:
        main(argv)
    assert caught.value.code == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert " ".join(out.split()).startswith(usage)
    assert listed in " ".join(out.split())


def test_the_installed_command_runs(broken: Path) -> None:
    command = Path(sys.executable).parent / "potka"
    done = subprocess.run(  # noqa: S603 (the environment's own command)
        [str(command), "catalog", "lint", str(FIXTURE_CATALOG)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (done.returncode, done.stdout, done.stderr) == (0, "no finding\n", "")
    done = subprocess.run(  # noqa: S603 (the environment's own command)
        [str(command), "catalog", "lint", str(broken)], capture_output=True, text=True, check=False
    )
    assert (done.returncode, done.stdout.splitlines()[-1], done.stderr) == (1, "2 finding(s)", "")
    done = subprocess.run(  # noqa: S603 (the environment's own command)
        [str(command), "catalog"], capture_output=True, text=True, check=False
    )
    assert (done.returncode, done.stdout) == (2, "")
    assert done.stderr.startswith("usage: potka catalog [-h] ACTION ...\n")
    assert "Traceback" not in done.stderr


def test_the_fixture_catalog_is_what_the_tests_expect() -> None:
    catalog = load_catalog(FIXTURE_CATALOG)
    assert [(v.scenario.id, v.scenario.version) for v in catalog.scenarios] == [
        ("FIXTURE-001", 1),
        ("FIXTURE-002", 1),
        ("FIXTURE-CONTROL-000", 1),
    ]
    config, disk, control = catalog.scenarios
    assert {v.scenario.split for v in catalog.scenarios} == {"dev"}
    assert [step.primitive for step in config.scenario.inject] == [
        "nginx.corrupt_conf",
        "systemd.action",
    ]
    assert config.ground_truth.fault.category == "config.syntax_error"
    assert [step.primitive for step in disk.scenario.inject] == ["disk.fill"]
    assert disk.ground_truth.fault.category == "resource.disk_full"
    assert control.scenario.inject == ()
    assert control.ground_truth.diagnosis.accepted_labels == ("none",)
    assert control.ground_truth.actions.acceptable == ()
    assert sorted(catalog.blueprints) == ["fixture-store", "fixture-web"]
    assert sorted(catalog.overlays) == ["fixture-store-app", "fixture-web-app"]
    components = {
        name: [c.name for node in blueprint.nodes for c in node.components]
        for name, blueprint in catalog.blueprints.items()
    }
    assert components == {"fixture-store": ["ledger-compactor"], "fixture-web": ["report-builder"]}
    for overlay in catalog.overlays.values():
        assert overlay.install
        assert len(overlay.files) == 4
    for version in catalog.scenarios:
        for seed in (-(2**63), -1, 0, 1, 2**63 - 1):
            values = version.scenario.values(seed)
            assert "${" not in version.scenario.render_ticket(values)
            for step in version.scenario.steps():
                assert "${" not in json.dumps(step.render(values))
    assert disk.scenario.values(0)["percent"] in (96, 98, 100)
