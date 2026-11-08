from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import Repo

from potka_ci import build

FAKE = {
    "__init__.py": "",
    "release.py": 'BUILD_GROUP = "build"\n',
    "agentpkg.py": 'def env_dir(service):\n    return f".agent-venvs/{service}"\n',
    "contract.py": (
        "import pathlib\nDEFAULT_DIR = pathlib.Path('.')\n"
        "class C:\n    caps = {'manifest_bytes': 1000}\n"
        "def load(d):\n    return C()\n"
    ),
    "deploy.py": (
        "UV_SYNC = ('sync', '--frozen', '--marker-sync')\n"
        "UV_FETCH = ('--marker-fetch',)\n"
        "UV_BUILD = ('--marker-build',)\n"
        "UV_AGENT_PRUNE = ('--marker-prune',)\n"
        "def agent_packages(stage, max_bytes):\n    return [('agent', 'potka-agent')]\n"
        "def snapshot(top):\n"
        "    return {str(p.relative_to(top)): p.read_bytes() for p in sorted(top.rglob('*'))"
        " if p.is_file() and '.agent-venvs' not in p.parts}\n"
    ),
    "validate.py": (
        "class R:\n    def __init__(self, problems):\n        self.problems = problems\n"
        "def validate_release(stage, contract, host, built):\n"
        "    assert built is True\n"
        "    return R(['refused']) if (stage / 'refuse-built').exists() else R([])\n"
    ),
}
FAKE_UV = """#!/usr/bin/env python3
import json, os, pathlib, sys
argv = " ".join(sys.argv[1:])
keys = (
    "UV_OFFLINE", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON_DOWNLOADS", "UV_NO_CONFIG", "UV_NO_BINARY",
    "UV_PYTHON_INSTALL_DIR", "VIRTUAL_ENV",
)
env = {k: os.environ[k] for k in keys if k in os.environ}
env["cache"] = "UV_CACHE_DIR" in os.environ
if argv.startswith("python find"):
    env["cwd"] = os.getcwd()
with open(os.environ["FAKE_UV_LOG"], "a") as log:
    log.write(json.dumps([argv, env]) + "\\n")
fail = os.environ.get("FAKE_UV_FAIL", "")
if fail and fail in argv:
    print("resolving the lock")
    print("boom in " + fail, file=sys.stderr)
    sys.exit(3)
if argv.startswith("python find"):
    print(os.environ["FAKE_PYTHON"])
if os.environ.get("FAKE_UV_TOUCH"):
    pathlib.Path(os.environ["FAKE_UV_TOUCH"]).touch()
"""
# An interpreter that reports a version, and whether it is a virtual
# environment's, as the build check's probe asks.
FAKE_PYTHON = """#!/usr/bin/env python3
import os
print(os.environ.get("FAKE_PYTHON_VERSION", "3.99.1"), os.environ.get("FAKE_PYTHON_VENV", "False"))
"""
VERSION = "3.99.1"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def infra(tmp_path: Path) -> Iterator[Path]:
    """A stand-in pokus-infrastructure checkout with stand-in modules, at a commit."""
    checkout = Repo(tmp_path / "infra")
    root = checkout.path
    package = root / "tools" / "platform" / "pinka_platform"
    package.mkdir(parents=True)
    for name, text in FAKE.items():
        (package / name).write_text(text, encoding="utf-8")
    checkout.git("add", "-A")
    checkout.commit("The tools")
    yield root
    for name in [m for m in sys.modules if m.startswith("pinka_platform")]:
        del sys.modules[name]
    while str(root / "tools" / "platform") in sys.path:
        sys.path.remove(str(root / "tools" / "platform"))


@pytest.fixture
def uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "uv"
    path.write_text(FAKE_UV, encoding="utf-8")
    path.chmod(0o755)
    python = tmp_path / "python" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_text(FAKE_PYTHON, encoding="utf-8")
    python.chmod(0o755)
    log = tmp_path / "uv.log"
    monkeypatch.setenv("FAKE_UV_LOG", str(log))
    monkeypatch.setenv("FAKE_PYTHON", str(python))
    # The caller's own uv settings (make exports some) are the test's to set.
    for key in [k for k in os.environ if k.startswith("UV_") or k == "VIRTUAL_ENV"]:
        monkeypatch.delenv(key)
    return log


def fake_python(uv: Path) -> str:
    return str(uv.parent / "python" / "bin" / "python3")


def calls(log: Path) -> list[tuple[str, dict[str, object]]]:
    return [tuple(json.loads(line)) for line in log.read_text().splitlines()]


def release(repo: Repo, infra: Path, build_group: bool = True) -> Repo:
    repo.write("INFRA_PIN", Repo.head_of(infra) + "\n")
    repo.write("platform/platform-manifest.yaml", "name: potka\n")
    group = '[dependency-groups]\nbuild = ["a-backend==1.0"]\n' if build_group else ""
    repo.write("platform/pyproject.toml", group)
    return repo


def test_without_a_checkout_it_is_skipped(repo: Repo) -> None:
    assert build.run(repo.path, None).status == "skipped"


def test_the_phases_use_infra_s_own_flags_and_environment(
    repo: Repo, infra: Path, uv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UV_NO_BINARY", "1")  # a caller's uv setting never reaches the build
    outcome = build.run(release(repo, infra).path, infra, str(uv.parent / "uv"), VERSION)
    assert outcome.status == "passed", outcome.lines
    python = fake_python(uv)
    base = {"UV_PYTHON_DOWNLOADS": "never", "UV_NO_CONFIG": "1", "cache": True}
    offline = {**base, "UV_OFFLINE": "1"}
    agent = {**offline, "UV_PROJECT_ENVIRONMENT": "<stage>/.agent-venvs/agent"}
    seen = calls(uv)[1:]
    for _, env in seen:
        value = env.get("UV_PROJECT_ENVIRONMENT")
        if isinstance(value, str):
            assert value.endswith("/release/.agent-venvs/agent")
            env["UV_PROJECT_ENVIRONMENT"] = "<stage>/.agent-venvs/agent"
    assert seen == [
        (
            "sync --frozen --marker-sync --all-packages --marker-fetch --group build"
            f" --python {python}",
            base,
        ),
        (
            "sync --frozen --marker-sync --all-packages --marker-build --group build"
            f" --python {python}",
            offline,
        ),
        (
            "sync --frozen --marker-sync --package potka-agent --marker-build --group build"
            f" --python {python}",
            agent,
        ),
        (
            f"sync --frozen --marker-sync --package potka-agent --marker-prune --python {python}",
            agent,
        ),
    ]
    assert outcome.lines[0] == f"python {VERSION}: {python}"
    assert outcome.lines[-1] == "the built release is unchanged and valid (built=True)"


def test_the_version_resolves_to_a_path_before_the_environment_is_cleaned(
    repo: Repo, infra: Path, uv: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The caller's uv settings (where its Pythons are installed) reach the
    # lookup only; the active virtual environment does not.
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(tmp_path / "pythons"))
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "venv"))
    outcome = build.run(release(repo, infra).path, infra, str(uv.parent / "uv"), VERSION)
    assert outcome.status == "passed", outcome.lines
    (find, env), *phases = calls(uv)
    assert find == f"python find {VERSION}"
    cwd = Path(str(env.pop("cwd")))
    assert env == {"UV_PYTHON_INSTALL_DIR": str(tmp_path / "pythons"), "cache": False}
    assert not (cwd / "pyproject.toml").exists()
    assert len(phases) == 4
    for argv, phase_env in phases:
        assert argv.endswith(f" --python {fake_python(uv)}")
        assert "UV_PYTHON_INSTALL_DIR" not in phase_env
        assert "VIRTUAL_ENV" in phase_env  # the caller's VIRTUAL_ENV is not a UV_* setting


def test_a_version_that_cannot_be_resolved_fails(
    repo: Repo, infra: Path, uv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_UV_FAIL", "python find")
    executable = str(uv.parent / "uv")
    outcome = build.run(release(repo, infra).path, infra, executable, VERSION)
    assert outcome.status == "failed"
    assert outcome.lines == [
        f"Python {VERSION} cannot be resolved: {executable} python find {VERSION}: exit status 3",
        "resolving the lock",
        "boom in python find",
    ]
    assert [argv for argv, _ in calls(uv)] == [f"python find {VERSION}"]


@pytest.mark.parametrize(
    ("reported", "venv", "message"),
    [
        ("3.99.2", "False", "reports Python 3.99.2"),
        (VERSION, "True", "is a virtual environment's; the build takes a base interpreter"),
    ],
)
def test_an_interpreter_that_does_not_fit_fails(
    repo: Repo,
    infra: Path,
    uv: Path,
    monkeypatch: pytest.MonkeyPatch,
    reported: str,
    venv: str,
    message: str,
) -> None:
    monkeypatch.setenv("FAKE_PYTHON_VERSION", reported)
    monkeypatch.setenv("FAKE_PYTHON_VENV", venv)
    outcome = build.run(release(repo, infra).path, infra, str(uv.parent / "uv"), VERSION)
    assert outcome.status == "failed"
    assert outcome.lines == [f"Python {VERSION}: {fake_python(uv)} {message}"]
    assert [argv for argv, _ in calls(uv)] == [f"python find {VERSION}"]


def test_an_interpreter_that_cannot_run_fails(
    repo: Repo, infra: Path, uv: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "gone" / "python3"
    monkeypatch.setenv("FAKE_PYTHON", str(missing))
    outcome = build.run(release(repo, infra).path, infra, str(uv.parent / "uv"), VERSION)
    assert outcome.status == "failed"
    assert len(outcome.lines) == 1
    assert outcome.lines[0].startswith(f"Python {VERSION}: {missing} cannot run: ")


@pytest.mark.parametrize("version", ["3.99", "", "3.99.1rc1"])
def test_a_version_not_in_full_fails(repo: Repo, infra: Path, uv: Path, version: str) -> None:
    outcome = build.run(release(repo, infra).path, infra, str(uv.parent / "uv"), version)
    assert outcome.lines == [f"the build Python must be a full version (X.Y.Z), not '{version}'"]
    assert not uv.exists()


def real_uv() -> str:
    """The uv that runs the tests (uv run names itself in UV)."""
    found = os.environ.get("UV") or shutil.which("uv")
    if not found:
        pytest.fail("no uv executable: run the tests with make (uv run sets UV)")
    return found


def test_a_uv_managed_interpreter_in_a_custom_install_directory_is_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # make setup installs the pinned version into platform/.python; inside the
    # project's virtual environment (as under uv run), uv must still name
    # that installation, not the environment.
    version = (ROOT / "BUILD_PYTHON").read_text(encoding="utf-8").strip()
    installs = ROOT / "platform" / ".python"
    if not installs.is_dir():
        pytest.fail(f"{installs} does not exist: run make setup")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(installs))
    monkeypatch.setenv("UV_MANAGED_PYTHON", "1")
    monkeypatch.setenv("VIRTUAL_ENV", str(ROOT / "platform" / ".venv"))
    path = build.interpreter(real_uv(), version, tmp_path)
    assert Path(path).is_relative_to(installs)


def test_without_its_installation_the_version_cannot_be_resolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    version = (ROOT / "BUILD_PYTHON").read_text(encoding="utf-8").strip()
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(tmp_path / "empty"))
    monkeypatch.setenv("UV_MANAGED_PYTHON", "1")
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    uv = real_uv()
    with pytest.raises(build.InterpreterError) as raised:
        build.interpreter(uv, version, tmp_path)
    assert str(raised.value) == (
        f"Python {version} cannot be resolved: {uv} python find {version}: exit status 2"
    )


def test_without_a_build_group_there_is_no_group_and_no_prune(
    repo: Repo, infra: Path, uv: Path
) -> None:
    outcome = build.run(release(repo, infra, False).path, infra, str(uv.parent / "uv"), VERSION)
    assert outcome.status == "passed", outcome.lines
    python = fake_python(uv)
    assert [argv for argv, _ in calls(uv)] == [
        f"python find {VERSION}",
        f"sync --frozen --marker-sync --all-packages --marker-fetch --python {python}",
        f"sync --frozen --marker-sync --all-packages --marker-build --python {python}",
        f"sync --frozen --marker-sync --package potka-agent --marker-build --python {python}",
    ]


def test_the_checkout_must_be_at_the_pin(repo: Repo, infra: Path, uv: Path) -> None:
    release(repo, infra)
    Repo.at(infra).commit("Later")
    outcome = build.run(repo.path, infra, str(uv.parent / "uv"), VERSION)
    assert outcome.status == "failed"
    assert outcome.lines[0].startswith("the pokus-infrastructure checkout is at ")
    assert not uv.exists()


def test_a_directory_without_the_tools_fails(repo: Repo, tmp_path: Path, uv: Path) -> None:
    empty = Repo(tmp_path / "empty")
    empty.commit("Nothing")
    repo.write("INFRA_PIN", empty.head() + "\n")
    outcome = build.run(repo.path, empty.path, str(uv.parent / "uv"), VERSION)
    assert outcome.status == "failed"
    assert "cannot import its platform tools" in outcome.lines[0]


def test_a_failing_phase_fails_with_its_output(
    repo: Repo, infra: Path, uv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_UV_FAIL", "--marker-build --group")
    outcome = build.run(release(repo, infra).path, infra, str(uv.parent / "uv"), VERSION)
    assert outcome.status == "failed"
    assert outcome.lines[2].startswith("build: uv sync")
    assert outcome.lines[2].endswith(": exit status 3")
    assert outcome.lines[3:] == ["resolving the lock", "boom in --marker-build --group"]


def test_a_build_that_changes_the_release_fails(
    repo: Repo, infra: Path, uv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_UV_TOUCH", "new-file")
    outcome = build.run(release(repo, infra).path, infra, str(uv.parent / "uv"), VERSION)
    assert outcome.status == "failed"
    assert outcome.lines[-1] == "the build changed or removed new-file"


def test_the_validator_s_refusal_of_the_built_release_fails(
    repo: Repo, infra: Path, uv: Path
) -> None:
    release(repo, infra).write("platform/refuse-built", "")
    outcome = build.run(repo.path, infra, str(uv.parent / "uv"), VERSION)
    assert outcome.status == "failed"
    assert outcome.lines[-1] == "the validator refused the built release: refused"
