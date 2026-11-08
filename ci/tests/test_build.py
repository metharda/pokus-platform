from __future__ import annotations

import json
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
    "UV_OFFLINE", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON_DOWNLOADS", "UV_NO_CONFIG", "UV_NO_BINARY"
)
env = {k: os.environ[k] for k in keys if k in os.environ}
env["cache"] = "UV_CACHE_DIR" in os.environ
with open(os.environ["FAKE_UV_LOG"], "a") as log:
    log.write(json.dumps([argv, env]) + "\\n")
fail = os.environ.get("FAKE_UV_FAIL", "")
if fail and fail in argv:
    print("resolving the lock")
    print("boom in " + fail, file=sys.stderr)
    sys.exit(3)
if os.environ.get("FAKE_UV_TOUCH"):
    pathlib.Path(os.environ["FAKE_UV_TOUCH"]).touch()
"""


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
    log = tmp_path / "uv.log"
    monkeypatch.setenv("FAKE_UV_LOG", str(log))
    return log


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
    outcome = build.run(release(repo, infra).path, infra, str(uv.parent / "uv"), "3.12.15")
    assert outcome.status == "passed", outcome.lines
    base = {"UV_PYTHON_DOWNLOADS": "never", "UV_NO_CONFIG": "1", "cache": True}
    offline = {**base, "UV_OFFLINE": "1"}
    agent = {**offline, "UV_PROJECT_ENVIRONMENT": "<stage>/.agent-venvs/agent"}
    seen = calls(uv)
    for _, env in seen:
        value = env.get("UV_PROJECT_ENVIRONMENT")
        if isinstance(value, str):
            assert value.endswith("/release/.agent-venvs/agent")
            env["UV_PROJECT_ENVIRONMENT"] = "<stage>/.agent-venvs/agent"
    assert seen == [
        (
            "sync --frozen --marker-sync --all-packages --marker-fetch --group build"
            " --python 3.12.15",
            base,
        ),
        (
            "sync --frozen --marker-sync --all-packages --marker-build --group build"
            " --python 3.12.15",
            offline,
        ),
        (
            "sync --frozen --marker-sync --package potka-agent --marker-build --group build"
            " --python 3.12.15",
            agent,
        ),
        (
            "sync --frozen --marker-sync --package potka-agent --marker-prune --python 3.12.15",
            agent,
        ),
    ]
    assert outcome.lines[-1] == "the built release is unchanged and valid (built=True)"


def test_without_a_build_group_there_is_no_group_and_no_prune(
    repo: Repo, infra: Path, uv: Path
) -> None:
    outcome = build.run(release(repo, infra, False).path, infra, str(uv.parent / "uv"), "3.12")
    assert outcome.status == "passed", outcome.lines
    assert [argv for argv, _ in calls(uv)] == [
        "sync --frozen --marker-sync --all-packages --marker-fetch --python 3.12",
        "sync --frozen --marker-sync --all-packages --marker-build --python 3.12",
        "sync --frozen --marker-sync --package potka-agent --marker-build --python 3.12",
    ]


def test_the_checkout_must_be_at_the_pin(repo: Repo, infra: Path, uv: Path) -> None:
    release(repo, infra)
    Repo.at(infra).commit("Later")
    outcome = build.run(repo.path, infra, str(uv.parent / "uv"))
    assert outcome.status == "failed"
    assert outcome.lines[0].startswith("the pokus-infrastructure checkout is at ")
    assert not uv.exists()


def test_a_directory_without_the_tools_fails(repo: Repo, tmp_path: Path, uv: Path) -> None:
    empty = Repo(tmp_path / "empty")
    empty.commit("Nothing")
    repo.write("INFRA_PIN", empty.head() + "\n")
    outcome = build.run(repo.path, empty.path, str(uv.parent / "uv"))
    assert outcome.status == "failed"
    assert "cannot import its platform tools" in outcome.lines[0]


def test_a_failing_phase_fails_with_its_output(
    repo: Repo, infra: Path, uv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_UV_FAIL", "--marker-build --group")
    outcome = build.run(release(repo, infra).path, infra, str(uv.parent / "uv"))
    assert outcome.status == "failed"
    assert outcome.lines[1].startswith("build: uv sync")
    assert outcome.lines[1].endswith(": exit status 3")
    assert outcome.lines[2:] == ["resolving the lock", "boom in --marker-build --group"]


def test_a_build_that_changes_the_release_fails(
    repo: Repo, infra: Path, uv: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_UV_TOUCH", "new-file")
    outcome = build.run(release(repo, infra).path, infra, str(uv.parent / "uv"))
    assert outcome.status == "failed"
    assert outcome.lines[-1] == "the build changed or removed new-file"


def test_the_validator_s_refusal_of_the_built_release_fails(
    repo: Repo, infra: Path, uv: Path
) -> None:
    release(repo, infra).write("platform/refuse-built", "")
    outcome = build.run(repo.path, infra, str(uv.parent / "uv"))
    assert outcome.status == "failed"
    assert outcome.lines[-1] == "the validator refused the built release: refused"
