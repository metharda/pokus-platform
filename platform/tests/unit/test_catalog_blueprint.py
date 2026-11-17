"""blueprints/<name>.yaml: the nodes of a system, their images, paths and endpoints."""

from __future__ import annotations

import json
from typing import Any

import pytest
from potka_core.catalog.blueprint import Blueprint, within
from potka_testing.catalog_tree import blueprint_data
from pydantic import ValidationError


def load(data: dict[str, Any]) -> Blueprint:
    return Blueprint.model_validate_json(json.dumps(data))


def test_a_blueprint_loads() -> None:
    blueprint = load(blueprint_data())
    assert (blueprint.name, blueprint.version) == ("fixture-web", 3)
    assert (blueprint.probe_client.blueprint, blueprint.probe_client.version) == (
        "probe-client",
        "1.0.0",
    )
    web, cache = blueprint.nodes
    assert blueprint.node("web") is web
    assert blueprint.node("cache") is cache
    assert blueprint.node("db") is None
    assert (web.golden.blueprint, web.golden.version) == ("web-nginx-flask", "1.0.0")
    assert web.derived is not None
    assert (web.derived.name, web.derived.version) == ("fixture-web-jobs", 2)
    assert cache.derived is None
    assert web.services == ("nginx.service", "webapp.service")
    builder = web.component("report-builder")
    assert builder is not None
    assert builder.settings_file == "/etc/fixture/report-builder.conf"
    assert builder.settings == {"workers": 1, "batch_size": 50, "mode": "nightly"}
    assert web.component("other") is None
    assert web.allowed_read_paths == ("/etc/nginx", "/etc/fixture", "/var/log/nginx")
    assert web.patchable_paths[0].validator == ("nginx", "-t")
    assert web.patchable_paths[1].validator is None
    assert (web.endpoints[0].scheme, web.endpoints[0].port, web.endpoints[0].path) == (
        "http",
        80,
        "/",
    )
    assert cache.endpoints[0].path is None
    assert blueprint.health[0].probe == "http_get"


@pytest.mark.parametrize(
    ("path", "roots", "inside"),
    [
        ("/etc/nginx", ("/etc/nginx",), True),
        ("/etc/nginx/nginx.conf", ("/etc/nginx",), True),
        ("/etc/nginx/sites/a.conf", ("/var", "/etc/nginx"), True),
        ("/etc/nginx-extra/a.conf", ("/etc/nginx",), False),
        ("/etc/nginx.conf", ("/etc/nginx",), False),
        ("/etc", ("/etc/nginx",), False),
        ("/etc/nginx", (), False),
    ],
)
def test_a_path_is_within_a_root_by_whole_parts(
    path: str, roots: tuple[str, ...], inside: bool
) -> None:
    assert within(path, roots) is inside


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["nodes"][1].update(alias="web"), "node alias given twice: web"),
        (
            lambda d: d["nodes"][0]["services"].append("nginx.service"),
            "service given twice: nginx.service",
        ),
        (
            lambda d: d["nodes"][0]["components"].append(dict(d["nodes"][0]["components"][0])),
            "component given twice: report-builder",
        ),
        (
            lambda d: d["nodes"][0]["endpoints"].append(dict(d["nodes"][0]["endpoints"][0])),
            "endpoint given twice: site",
        ),
        (
            lambda d: d["nodes"][0]["allowed_read_paths"].append("/etc/nginx"),
            "allowed read path given twice: /etc/nginx",
        ),
        (
            lambda d: d["nodes"][0]["patchable_paths"].append(
                {"path": "/etc/fixture", "validator": ["true"]}
            ),
            "patchable path given twice: /etc/fixture",
        ),
        (
            lambda d: d["nodes"][0]["patchable_paths"].append(
                {"path": "/etc/nginx-extra/a.conf", "validator": None}
            ),
            "patchable but not readable: /etc/nginx-extra/a.conf",
        ),
        (
            lambda d: d["nodes"][0]["patchable_paths"].append({"path": "/etc", "validator": None}),
            "patchable but not readable: /etc",
        ),
        (
            lambda d: d["nodes"][0]["patchable_paths"].append(
                {"path": "/etc/fixture/report-builder.conf", "validator": ["true"]}
            ),
            "patchable paths inside each other: /etc/fixture/report-builder.conf in /etc/fixture",
        ),
        (
            lambda d: (
                d["nodes"][0]["allowed_read_paths"].append("/etc"),
                d["nodes"][0]["patchable_paths"].append({"path": "/etc", "validator": None}),
            ),
            "inside each other: /etc/fixture in /etc, /etc/nginx/nginx.conf in /etc",
        ),
        (lambda d: d["nodes"][0].pop("derived"), "background components need a derived image"),
        (
            lambda d: d["nodes"][0].update(derived=None),
            "background components need a derived image",
        ),
        (
            lambda d: d["nodes"][0]["patchable_paths"].pop(1),
            "the settings file of report-builder is not patchable",
        ),
        (
            lambda d: d["nodes"][0]["components"][0].update(settings_file="/etc/fixture2/x.conf"),
            "the settings file of report-builder is not patchable",
        ),
        (
            lambda d: d["health"][0]["params"].update(port="${port}"),
            "a health probe uses a scenario parameter",
        ),
        (
            lambda d: d["nodes"][0]["endpoints"][0].update(path=None),
            "an http or https endpoint has a path, and a tcp endpoint has none",
        ),
        (
            lambda d: d["nodes"][1]["endpoints"][0].update(path="/"),
            "an http or https endpoint has a path, and a tcp endpoint has none",
        ),
    ],
)
def test_a_blueprint_that_contradicts_itself_is_refused(change: Any, message: str) -> None:
    data = blueprint_data()
    change(data)
    with pytest.raises(ValidationError, match=message):
        load(data)


@pytest.mark.parametrize(
    "path",
    [
        "etc/nginx",
        "/",
        "",
        "/etc/nginx/",
        "//etc/nginx",
        "/etc//nginx",
        "/etc/./nginx",
        "/etc/../nginx",
        "/etc/nginx/..",
        "/etc/nginx/.",
        "/etc/ng\ninx",
        "/etc/ng\x00inx",
        "/etc/ng\x7finx",
        "/etc/ng\x85inx",
        "/etc/ng\u2028inx",
        "/etc/\u202enginx",
        "/etc/ng\u200binx",
        "/etc/ng\u00a0inx",
        "~/nginx",
    ],
)
def test_a_path_that_is_not_absolute_and_normal_is_refused(path: str) -> None:
    changes: tuple[Any, ...] = (
        lambda d: d["nodes"][0]["allowed_read_paths"].append(path),
        lambda d: d["nodes"][0]["patchable_paths"][0].update(path=path),
        lambda d: d["nodes"][0]["components"][0].update(settings_file=path),
    )
    for change in changes:
        data = blueprint_data()
        change(data)
        with pytest.raises(ValidationError) as caught:
            load(data)
        assert caught.value.error_count() == 1
        if ".." in path or "/." in path or not path.isprintable():
            assert "not an absolute path in its normal form" in str(caught.value)


@pytest.mark.parametrize("path", ["/opt/app", "/e", "/etc/nginx.d/a b.conf", "/srv/…/x", "/a/..b"])
def test_an_absolute_normal_path_is_accepted(path: str) -> None:
    data = blueprint_data()
    data["nodes"][0]["allowed_read_paths"].append(path)
    assert path in load(data).nodes[0].allowed_read_paths


def test_readable_paths_may_lie_inside_each_other() -> None:
    data = blueprint_data()
    data["nodes"][0]["allowed_read_paths"] += ["/etc", "/etc/nginx/conf.d"]
    assert "/etc" in load(data).nodes[0].allowed_read_paths


def test_an_https_endpoint_the_largest_numbers_and_a_literal_dollar_sign() -> None:
    data = blueprint_data()
    data["version"] = 2**53 - 1
    data["nodes"][0]["derived"]["version"] = 2**53 - 1
    data["nodes"][0]["endpoints"].append(
        {"name": "secure", "scheme": "https", "port": 65535, "path": "/health"}
    )
    data["nodes"][0]["components"][0]["settings"].update(limit=2**53 - 1, ratio=-0.5, zero=0)
    data["health"][0]["params"]["body"] = "costs 5 $$"
    blueprint = load(data)
    assert blueprint.version == 2**53 - 1
    assert blueprint.nodes[0].endpoints[1].scheme == "https"
    assert blueprint.nodes[0].components[0].settings["limit"] == 2**53 - 1
    assert blueprint.health[0].render({}) == {
        "node": "web",
        "endpoint": "site",
        "status": 200,
        "body": "costs 5 $",
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(name="Fixture"),
        lambda d: d.update(name="fixture_web"),
        lambda d: d.update(version=0),
        lambda d: d.update(version=2**53),
        lambda d: d["nodes"][0]["derived"].update(version=2**53),
        lambda d: d.update(version="3"),
        lambda d: d.update(nodes=[]),
        lambda d: d.update(extra=1),
        lambda d: d.update(health=None),
        lambda d: d.update(probe_client=None),
        lambda d: d.update(probe_client="probe-client/1.0.0"),
        lambda d: d.update(probe_client={"blueprint": "probe-client"}),
        lambda d: d.update(probe_client={"blueprint": "probe-client", "version": 1.0}),
        lambda d: d.update(probe_client={"blueprint": "", "version": "1.0.0"}),
        lambda d: d["probe_client"].update(alias="client"),
        lambda d: d["probe_client"].update(derived={"name": "probes", "version": 1}),
        lambda d: d["nodes"][0].update(probe_client=True),
        lambda d: d["nodes"][0].update(alias="Web"),
        lambda d: d["nodes"][0].update(alias="web-1"),
        lambda d: d["nodes"][0].update(alias="a" * 16),
        lambda d: d["nodes"][0].update(golden="web-nginx-flask/1.0.0"),
        lambda d: d["nodes"][0]["golden"].update(version=1.0),
        lambda d: d["nodes"][0]["golden"].update(version=""),
        lambda d: d["nodes"][0]["golden"].update(blueprint=""),
        lambda d: d["nodes"][0]["golden"].update(fingerprint="0" * 64),
        lambda d: d["nodes"][0]["golden"].pop("version"),
        lambda d: d["nodes"][0]["derived"].update(name="Jobs"),
        lambda d: d["nodes"][0]["derived"].update(name="jobs-"),
        lambda d: d["nodes"][0]["derived"].update(name="-jobs"),
        lambda d: d["nodes"][0]["derived"].update(name="potka/jobs"),
        lambda d: d["nodes"][0]["derived"].update(version="2"),
        lambda d: d["nodes"][0]["derived"].update(version=0),
        lambda d: d["nodes"][0]["derived"].update(overlay="x"),
        lambda d: d["nodes"][0]["derived"].update(fingerprint="0" * 64),
        lambda d: d["nodes"][0].update(services=["nginx"]),
        lambda d: d["nodes"][0].update(services=["Nginx.service"]),
        lambda d: d["nodes"][0].update(services=["nginx.timer"]),
        lambda d: d["nodes"][0]["components"][0].update(name="Report Builder"),
        lambda d: d["nodes"][0]["components"][0].update(settings={}),
        lambda d: d["nodes"][0]["components"][0].update(settings={"a b": 1}),
        lambda d: d["nodes"][0]["components"][0].update(settings={"workers": 2**53}),
        lambda d: d["nodes"][0]["components"][0].pop("settings_file"),
        lambda d: d["nodes"][0]["patchable_paths"][0].update(validator=[]),
        lambda d: d["nodes"][0]["patchable_paths"][0].update(validator="nginx -t"),
        lambda d: d["nodes"][0]["patchable_paths"][0].update(validator=["nginx", ""]),
        lambda d: d["nodes"][0]["patchable_paths"][0].update(validator=["nginx", 1]),
        lambda d: d["nodes"][0]["patchable_paths"][0].pop("validator"),
        lambda d: d["nodes"][0]["patchable_paths"].append("/etc/nginx/nginx.conf"),
        lambda d: d["nodes"][0]["endpoints"][0].update(scheme="udp"),
        lambda d: d["nodes"][0]["endpoints"][0].update(port=0),
        lambda d: d["nodes"][0]["endpoints"][0].update(port=65536),
        lambda d: d["nodes"][0]["endpoints"][0].update(port="80"),
        lambda d: d["nodes"][0]["endpoints"][0].update(path="status"),
        lambda d: d["nodes"][0]["endpoints"][0].update(path="/a b"),
        lambda d: d["nodes"][0]["endpoints"][0].update(host="web.example.com"),
        lambda d: d["nodes"][0]["endpoints"][0].pop("path"),
    ],
)
def test_a_malformed_blueprint_is_refused(change: Any) -> None:
    data = blueprint_data()
    change(data)
    with pytest.raises(ValidationError) as caught:
        load(data)
    assert caught.value.error_count() == 1


@pytest.mark.parametrize("key", sorted(blueprint_data()))
def test_every_key_of_a_blueprint_is_required(key: str) -> None:
    data = blueprint_data()
    del data[key]
    with pytest.raises(ValidationError) as caught:
        load(data)
    assert [(error["type"], error["loc"]) for error in caught.value.errors()] == [
        ("missing", (key,))
    ]


@pytest.mark.parametrize("key", sorted(set(blueprint_data()["nodes"][0]) - {"derived"}))
def test_every_key_of_a_node_but_derived_is_required(key: str) -> None:
    data = blueprint_data()
    del data["nodes"][0][key]
    with pytest.raises(ValidationError) as caught:
        load(data)
    assert [(error["type"], error["loc"]) for error in caught.value.errors()] == [
        (
            "missing",
            (
                "nodes",
                0,
                key,
            ),
        )
    ]


@pytest.mark.parametrize(
    ("change", "errors"),
    [
        # A step is a probe or a primitive: the wrong one is a missing key and an unknown one.
        (lambda d: d["health"].append({"primitive": "file.write", "params": {}}), 2),
        # A setting's normal value is a string, an integer, a number or a boolean.
        (lambda d: d["nodes"][0]["components"][0].update(settings={"workers": [1]}), 4),
        (lambda d: d["nodes"][0]["components"][0].update(settings={"workers": None}), 4),
    ],
)
def test_a_value_no_alternative_accepts_is_refused_once_for_each(change: Any, errors: int) -> None:
    data = blueprint_data()
    change(data)
    with pytest.raises(ValidationError) as caught:
        load(data)
    assert caught.value.error_count() == errors
