from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from potka_ci.build import VERSION_RE
from potka_ci.repo import repository_files

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = ROOT / "platform"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def version() -> str:
    return (ROOT / "BUILD_PYTHON").read_text(encoding="utf-8").strip()


def make(*args: str) -> subprocess.CompletedProcess[str]:
    """make in platform/, with no variable of an outer make or of the caller."""
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/")}
    return subprocess.run(
        ["make", "-n", "-s", "-C", str(PLATFORM), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_build_python_is_one_full_version() -> None:
    assert VERSION_RE.fullmatch(version())


def test_no_other_file_names_the_version() -> None:
    pattern = re.compile(rf"(?<![\d.]){re.escape(version())}(?![\d.])")
    named = [
        name
        for name in repository_files(ROOT)
        if (ROOT / name).is_file()
        and not (ROOT / name).is_symlink()
        and pattern.search((ROOT / name).read_bytes().decode("utf-8", "replace"))
    ]
    assert named == ["BUILD_PYTHON"]


def test_the_makefile_installs_and_builds_with_that_version() -> None:
    quoted = f'"{version()}"'
    setup = make("setup").stdout.splitlines()
    assert setup[0].endswith(f"python install --no-bin {quoted}")
    assert setup[1].endswith(f"sync --locked --all-packages --group contract --python {quoted}")
    for target in ("build", "gate"):
        out = make(target, "INFRA=/infra").stdout
        assert f"--python {quoted}" in out, (target, out)


@pytest.mark.parametrize(
    "where", [("BUILD_PYTHON=3.99.1",), ()], ids=["command line", "environment"]
)
def test_the_makefile_takes_the_version_from_the_file_only(where: tuple[str, ...]) -> None:
    if where:
        done = make("build", "INFRA=/infra", *where)
        origin = "command line"
    else:
        env = {"PATH": os.environ["PATH"], "BUILD_PYTHON": "3.99.1"}
        done = subprocess.run(
            ["make", "-n", "-s", "-C", str(PLATFORM), "build", "INFRA=/infra"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        origin = "environment"
    assert done.returncode != 0
    assert f"BUILD_PYTHON is read from the file ../BUILD_PYTHON only, not from the {origin}" in (
        done.stderr
    )


def test_ci_names_no_python_and_installs_it_with_make_setup() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "UV_PYTHON" not in text
    assert "BUILD_PYTHON=" not in text
    assert "python-version" not in text
    for name, job in workflow["jobs"].items():
        runs = [step["run"] for step in job["steps"] if "run" in step]
        makes = [run for run in runs if run.startswith("make ")]
        if makes:
            # Every job that runs make installs the Python first.
            assert makes[0].split()[1] == "setup", name


RECORDER = """\
import json, os, sys
keys = ("UV_PYTHON_INSTALL_DIR", "UV_MANAGED_PYTHON", "UV_PYTHON_DOWNLOADS")
with open(sys.argv[1], "w") as out:
    json.dump({"argv": sys.argv[2:], "env": {k: os.environ.get(k) for k in keys}}, out)
"""


@pytest.mark.parametrize("target", ["build", "gate"])
def test_make_builds_only_with_the_installation_in_the_repository(
    tmp_path: Path, target: str
) -> None:
    # setup-uv points UV_PYTHON_INSTALL_DIR elsewhere; make overrides it, and
    # allows neither another uv-found Python nor a download.
    script = tmp_path / "record.py"
    script.write_text(RECORDER, encoding="utf-8")
    record = tmp_path / "record.json"
    env = {"PATH": os.environ["PATH"], "UV_PYTHON_INSTALL_DIR": str(tmp_path / "elsewhere")}
    done = subprocess.run(
        [
            "make",
            "-s",
            "-C",
            str(PLATFORM),
            target,
            "INFRA=/infra",
            f"CHECK={sys.executable} {script} {record}",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    seen = json.loads(record.read_text(encoding="utf-8"))
    assert seen["env"] == {
        "UV_PYTHON_INSTALL_DIR": str(PLATFORM / ".python"),
        "UV_MANAGED_PYTHON": "1",
        "UV_PYTHON_DOWNLOADS": "manual",
    }
    assert seen["argv"][0] == target
    assert seen["argv"][seen["argv"].index("--python") + 1] == version()
