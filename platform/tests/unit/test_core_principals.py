"""principals.json and endpoints.toml, as the contract at the pin defines them."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from potka_core.endpoints import load_endpoints
from potka_core.principals import Principals, load_principals
from pydantic import ValidationError

FIXTURES = Path(__file__).parents[1] / "fixtures"


def job(**changes: object) -> dict[str, object]:
    return {
        "name": "train",
        "profile": "gpu-trainer",
        "account": "potka-train",
        "uid": 61100,
    } | changes


def principals_data() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "principals.json").read_text())
    return data


def test_the_files_load() -> None:
    principals = load_principals(FIXTURES / "principals.json")
    assert [s.name for s in principals.sockets_of("orch")] == ["api", "svc", "agent-session"]
    assert principals.service("gw").account == "potka-gw"
    with pytest.raises(KeyError):
        principals.service("nope")
    endpoints = load_endpoints(FIXTURES / "endpoints.toml")
    assert endpoints.telemetry is not None
    assert endpoints.telemetry.otlp_http == "127.0.0.1:4318"
    assert endpoints.postgres is not None
    assert dict(endpoints.postgres.roles) == {
        name: f"potka_{name}" for name in ("orch", "gw", "exec", "inject", "verify", "eval")
    }
    assert endpoints.opa is not None
    assert endpoints.opa.decision_path == "/v1/data/potka/action/decision"
    assert endpoints.incus is not None
    assert endpoints.incus.cpuset_targets == "8-15"
    assert endpoints.gpu is not None


def test_a_principals_file_with_jobs_settings_and_secrets_loads() -> None:
    data = principals_data()
    data["jobs"] = [
        {"name": "train", "profile": "gpu-trainer", "account": "potka-train", "uid": 61100}
    ]
    data["services"][0].update(config="/etc/pinka/platform/orch.json", secrets_dir="/etc/pinka/s")
    principals = Principals.model_validate_json(json.dumps(data), strict=True)
    assert principals.jobs[0].account == "potka-train"
    assert principals.services[0].config == "/etc/pinka/platform/orch.json"


def test_a_remote_inference_has_a_control_url(tmp_path: Path) -> None:
    text = (FIXTURES / "endpoints.toml").read_text()
    remote = 'mode = "remote"\ncontrol_url = "http://127.0.0.1:18710"'
    (tmp_path / "endpoints.toml").write_text(text.replace('mode = "local"', remote))
    inference = load_endpoints(tmp_path / "endpoints.toml").inference
    assert inference is not None
    assert inference.control_url == "http://127.0.0.1:18710"


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(extra=1),
        lambda d: d.pop("_comment"),
        lambda d: d.update(schema_version=2),
        lambda d: d["sockets"][0].update(mode="0666"),
        lambda d: d["services"][0].update(uid=0),
        lambda d: d["services"][0].update(uid="61001"),
        lambda d: d["services"][0].update(profile="root"),
        lambda d: d["services"][0].update(name="Orch"),
        lambda d: d["services"][0].update(account="orch"),
        lambda d: d["services"][0].update(config="relative.json"),
        lambda d: d["sockets"][0].update(name="api_1"),
        lambda d: d["sockets"][0]["clients"].update(humans=["guests"], human_groups=["x"]),
        lambda d: d.update(platform="P"),
        lambda d: d.update(contract_version=1),
        lambda d: d.update(comment=d.pop("_comment")),
        lambda d: d.update(
            jobs=[{"name": "t", "profile": "observer", "account": "potka-t", "uid": 9}]
        ),
    ],
)
def test_a_principals_file_outside_the_contract_is_refused(change: Any) -> None:
    data = principals_data()
    change(data)
    with pytest.raises(ValidationError):
        Principals.model_validate_json(json.dumps(data), strict=True)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda d: d["sockets"][0]["clients"]["human_groups"].pop(),
            "orch/api: human_groups are not the groups of its humans",
        ),
        (
            lambda d: d["sockets"][0]["clients"]["human_groups"].reverse(),
            "orch/api: human_groups are not the groups of its humans",
        ),
        (
            lambda d: d["services"][1].update(account="potka-orch"),
            "account given twice: potka-orch",
        ),
        (lambda d: d["services"][1].update(uid=61001), "uid given twice: 61001"),
        (lambda d: d["services"][1].update(name="orch"), "service or job name given twice: orch"),
        (lambda d: d["sockets"][1].update(name="api"), "socket given twice: orch/api"),
        (lambda d: d["sockets"][1].update(fd_name="api"), "fd_name given twice: orch/api"),
        (
            lambda d: d["sockets"][1].update(path=d["sockets"][0]["path"]),
            "socket path given twice: /run/pinka/platform/orch/api.sock",
        ),
        (lambda d: d.update(jobs=[job(uid=61001)]), "uid given twice: 61001"),
        (lambda d: d.update(jobs=[job(name="orch")]), "service or job name given twice: orch"),
        (lambda d: d.update(jobs=[job(account="potka-gw")]), "account given twice: potka-gw"),
    ],
)
def test_a_principals_file_that_contradicts_itself_is_refused(change: Any, message: str) -> None:
    data = principals_data()
    change(data)
    with pytest.raises(ValidationError, match=message):
        Principals.model_validate_json(json.dumps(data), strict=True)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('machine = "pinka-lab"', 'machine = "pinka-lab"\nextra = 1'),
        ('otlp_http = "127.0.0.1:4318"', 'otlp_http = "localhost:4318"'),
        ('vm_collector_grpc = "192.0.2.1:4317"', 'vm_collector_grpc = "192.0.2.1"'),
        ('researcher_endpoint = "127.0.0.1:18705"', 'researcher_endpoint = "lab:18705"'),
        ('lab_mode = "production"', 'lab_mode = "testing"'),
        ("otlp_grpc = 4317", "otlp_grpc = 70000"),
        ("[lab]", "[laboratory]"),
        ('cert_dir = "/etc/pinka/incus"', 'cert_dir = "etc/pinka/incus"'),
        ('mode = "local"', 'mode = "local"\nunknown = 1'),
    ],
)
def test_an_endpoints_file_outside_the_contract_is_refused(
    tmp_path: Path, old: str, new: str
) -> None:
    text = (FIXTURES / "endpoints.toml").read_text()
    assert old in text
    (tmp_path / "endpoints.toml").write_text(text.replace(old, new))
    with pytest.raises(ValidationError):
        load_endpoints(tmp_path / "endpoints.toml")


def test_a_toml_date_is_refused(tmp_path: Path) -> None:
    text = (FIXTURES / "endpoints.toml").read_text()
    (tmp_path / "endpoints.toml").write_text(
        text.replace('machine = "pinka-lab"', "machine = 2026-11-08")
    )
    with pytest.raises(ValueError, match=r"^endpoints\.toml holds a date or a time$"):
        load_endpoints(tmp_path / "endpoints.toml")
