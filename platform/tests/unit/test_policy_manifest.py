"""The manifest's policy: the bundle the release ships, at the path the executor asks."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from potka_executor.bundle import bundle_hash, kept_hash
from potka_executor.opa import DECISION_PATH_RE

PLATFORM = Path(__file__).resolve().parents[2]
MANIFEST = yaml.safe_load((PLATFORM / "platform-manifest.yaml").read_text(encoding="utf-8"))


def test_the_manifest_names_the_bundle_and_asks_for_the_policy_engine() -> None:
    assert MANIFEST["policy"] == {"bundle": "policy", "decision_path": "potka/action/decision"}
    assert "opa" in MANIFEST["requires"]


def test_the_decision_path_is_the_decisions_package_and_rule() -> None:
    path = MANIFEST["policy"]["decision_path"]
    assert DECISION_PATH_RE.fullmatch(path)
    *package, rule = path.split("/")
    rego = (PLATFORM / MANIFEST["policy"]["bundle"] / "potka" / "action.rego").read_text(
        encoding="utf-8"
    )
    assert f"package {'.'.join(package)}\n" in rego
    assert re.search(rf"^{rule} := ", rego, re.MULTILINE)


def test_the_bundle_claims_its_own_roots_and_none_of_the_engines() -> None:
    bundle = PLATFORM / MANIFEST["policy"]["bundle"]
    manifest = json.loads((bundle / ".manifest").read_text(encoding="utf-8"))
    assert manifest == {"roots": ["potka", "tools", "registry"]}
    top = sorted(path.name for path in bundle.iterdir() if path.is_dir())
    assert top == sorted(manifest["roots"])
    packages = set()
    for rego in bundle.rglob("*.rego"):
        found = re.search(r"^package ([a-z_.]+)$", rego.read_text(encoding="utf-8"), re.MULTILINE)
        assert found is not None, rego
        packages.add(found[1].split(".")[0])
    assert packages == {"potka"}
    assert not {"system", "pinka"} & set(top)


def test_the_bundle_the_manifest_names_holds_its_own_hash() -> None:
    bundle = PLATFORM / MANIFEST["policy"]["bundle"]
    assert kept_hash(bundle) == bundle_hash(bundle)
