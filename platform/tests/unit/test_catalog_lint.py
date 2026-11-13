"""The catalog lint: each rule refuses its error on a changed copy of the fixture catalog."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from potka_core.catalog.lint import PRIMITIVES, PROBES, Finding, is_control, lint, lint_catalog
from potka_core.catalog.loader import load_catalog
from potka_testing.fixture_catalog import FIXTURE_CATALOG, copy_fixture, edit_yaml

WEB = "blueprints/fixture-web.yaml"
STORE = "blueprints/fixture-store.yaml"
ONE = "scenarios/FIXTURE-001/1"
TWO = "scenarios/FIXTURE-002/1"
CONTROL = "scenarios/FIXTURE-CONTROL-000/1"
# The golden version the fixture's blueprints name, and one they do not.
_WEB_NODE = yaml.safe_load((FIXTURE_CATALOG / WEB).read_text(encoding="utf-8"))["nodes"][0]
GOLDEN: str = _WEB_NODE["golden"]["version"]
OTHER = "0.0.9"


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    return copy_fixture(tmp_path / "catalog")


def found(root: Path) -> list[tuple[str, str, str]]:
    findings = lint(root)
    assert findings == sorted(set(findings))
    return [(finding.path, finding.rule, finding.message) for finding in findings]


def new_version(root: Path, scenario: str, version: int, **changes: Any) -> str:
    """Copy version 1 of SCENARIO as VERSION, with CHANGES to its scenario.yaml."""
    path = f"scenarios/{scenario}/{version}"
    shutil.copytree(root / "scenarios" / scenario / "1", root / path)
    edit_yaml(root, f"{path}/scenario.yaml", lambda d: d.update(version=version, **changes))
    return path


def test_the_fixture_catalog_has_no_finding(catalog: Path) -> None:
    assert lint(FIXTURE_CATALOG) == []
    assert lint(catalog) == []
    assert lint_catalog(load_catalog(catalog)) == []


def test_the_fault_primitives_and_the_probes() -> None:
    assert sorted(PRIMITIVES) == [
        "app.bad_config",
        "disk.fill",
        "disk.inode_exhaust",
        "dns.break_resolver",
        "dns.wrong_record",
        "docker.crashloop",
        "docker.kill_container",
        "docker.stop_daemon",
        "file.mutate",
        "file.write",
        "net.block_port",
        "net.netem",
        "nginx.corrupt_conf",
        "nginx.set_upstream",
        "permissions.break",
        "pg.break_hba",
        "pg.exhaust_connections",
        "pg.fill_disk",
        "pg.stop",
        "proc.cpu_hog",
        "proc.fd_exhaust",
        "proc.mem_hog",
        "redis.maxmemory_oom",
        "redis.stop",
        "systemd.action",
        "systemd.break_unit",
    ]
    assert sorted(PROBES) == [
        "container_state",
        "disk_usage_below",
        "dns_resolve",
        "file_unchanged",
        "http_get",
        "inode_usage_below",
        "integrity",
        "load_below",
        "nginx_config_valid",
        "pg_query",
        "pg_ready",
        "process_absent",
        "redis_ping",
        "tcp_connect",
        "unit_state",
    ]


def test_a_file_that_does_not_load_gives_the_loaders_findings_only(catalog: Path) -> None:
    edit_yaml(catalog, f"{ONE}/scenario.yaml", lambda d: d.update(split="all", blueprint="gone"))
    edit_yaml(catalog, f"{TWO}/ground_truth.yaml", lambda d: d.update(verification=[]))
    (catalog / "taxonomy.yaml").write_text("version: 1\nlabels: [\n")
    (catalog / "scenarios" / "notes.txt").write_text("x")
    *models, yaml_error = found(catalog)
    assert models == [
        (
            "scenarios/FIXTURE-001/1/scenario.yaml",
            "format",
            "split: Input should be 'train', 'dev' or 'test'",
        ),
        ("scenarios/notes.txt", "format", "a file, not a directory"),
    ]
    assert yaml_error[:2] == ("taxonomy.yaml", "format")
    assert yaml_error[2].startswith("not YAML of the catalog's subset: ")
    assert yaml_error[2].endswith(" (line 3, column 1)")
    assert found(catalog / "missing") == [(".", "format", "missing")]


def test_the_versions_of_a_scenario_share_its_split(catalog: Path) -> None:
    new_version(catalog, "FIXTURE-001", 2)
    new_version(catalog, "FIXTURE-002", 2, split="dev")
    assert found(catalog) == []
    new_version(catalog, "FIXTURE-001", 3, split="test")
    new_version(catalog, "FIXTURE-002", 10, split="train")
    assert found(catalog) == [
        (
            "scenarios/FIXTURE-001",
            "split",
            "its versions are in different splits: 1 in dev, 2 in dev, 3 in test",
        ),
        (
            "scenarios/FIXTURE-002",
            "split",
            "its versions are in different splits: 1 in dev, 2 in dev, 10 in train",
        ),
    ]


def test_a_scenario_names_a_blueprint_of_the_catalog(catalog: Path) -> None:
    edit_yaml(catalog, f"{TWO}/scenario.yaml", lambda d: d.update(blueprint="fixture-web"))
    assert found(catalog) == []
    edit_yaml(catalog, f"{TWO}/scenario.yaml", lambda d: d.update(blueprint="fixture-web-app"))
    edit_yaml(catalog, f"{CONTROL}/scenario.yaml", lambda d: d.update(blueprint="web"))
    assert found(catalog) == [
        (
            "scenarios/FIXTURE-002/1/scenario.yaml",
            "blueprint",
            "the blueprint fixture-web-app is not in blueprints/",
        ),
        (
            "scenarios/FIXTURE-CONTROL-000/1/scenario.yaml",
            "blueprint",
            "the blueprint web is not in blueprints/",
        ),
    ]


def test_every_step_names_a_known_primitive_or_probe(catalog: Path) -> None:
    def scenario(d: Any) -> None:
        d["preconditions"].append({"probe": "http_gets", "params": {}})
        d["setup"].append({"primitive": "file.write", "params": {}})
        d["setup"].append({"primitive": "file.writes", "params": {}})
        d["inject"].insert(0, {"primitive": "nginx.corrupt", "params": {}})
        d["confirm"].append({"probe": "nginx_config", "params": {}})

    def truth(d: Any) -> None:
        d["verification"].append({"probe": "unit_state", "params": {}})
        d["verification"].append({"probe": "units_state", "params": {}})
        d["collateral"].insert(0, {"probe": "file_changed", "params": {}})

    edit_yaml(catalog, f"{ONE}/scenario.yaml", scenario)
    edit_yaml(catalog, f"{ONE}/ground_truth.yaml", truth)
    edit_yaml(catalog, STORE, lambda d: d["health"].append({"probe": "pg_readiness", "params": {}}))
    assert found(catalog) == [
        ("blueprints/fixture-store.yaml", "names", "health.2: no probe pg_readiness"),
        (
            "scenarios/FIXTURE-001/1/ground_truth.yaml",
            "names",
            "collateral.0: no probe file_changed",
        ),
        (
            "scenarios/FIXTURE-001/1/ground_truth.yaml",
            "names",
            "verification.4: no probe units_state",
        ),
        ("scenarios/FIXTURE-001/1/scenario.yaml", "names", "confirm.2: no probe nginx_config"),
        (
            "scenarios/FIXTURE-001/1/scenario.yaml",
            "names",
            "inject.0: no fault primitive nginx.corrupt",
        ),
        ("scenarios/FIXTURE-001/1/scenario.yaml", "names", "preconditions.0: no probe http_gets"),
        (
            "scenarios/FIXTURE-001/1/scenario.yaml",
            "names",
            "setup.1: no fault primitive file.writes",
        ),
    ]


def test_every_primitive_and_probe_name_is_accepted(catalog: Path) -> None:
    def scenario(d: Any) -> None:
        d["setup"] = [{"primitive": name, "params": {}} for name in sorted(PRIMITIVES)]
        d["preconditions"] = [{"probe": name, "params": {}} for name in sorted(PROBES)]

    edit_yaml(catalog, f"{CONTROL}/scenario.yaml", scenario)
    assert found(catalog) == []


def test_a_ground_truth_uses_only_its_scenarios_parameters(catalog: Path) -> None:
    def declared(d: Any) -> None:
        d["fault"]["location"] = "/etc/nginx/${section}.conf"
        d["verification"][0]["params"]["note"] = "${defect} in ${section}, 5 $$"
        d["collateral"][0]["params"]["paths"].append("/srv/${section}")
        for kind in ("acceptable", "then_required", "harmful"):
            d["actions"][kind][0]["args"]["note"] = "${section}"

    edit_yaml(catalog, f"{ONE}/ground_truth.yaml", declared)
    assert found(catalog) == []

    def undeclared(d: Any) -> None:
        d["fault"]["location"] = "/etc/nginx/${where}.conf"
        d["verification"][0]["params"]["note"] = "${checked}"
        d["collateral"][0]["params"]["paths"].append("/srv/${kept}/${section}")
        for kind in ("acceptable", "then_required", "harmful"):
            d["actions"][kind][0]["args"]["note"] = "${" + kind + "}"

    edit_yaml(catalog, f"{ONE}/ground_truth.yaml", undeclared)
    edit_yaml(
        catalog, f"{TWO}/ground_truth.yaml", lambda d: d["fault"].update(location="${section}")
    )
    assert found(catalog) == [
        (
            "scenarios/FIXTURE-001/1/ground_truth.yaml",
            "templates",
            f"a template uses the parameter {name}, which the scenario does not declare",
        )
        for name in ("acceptable", "checked", "harmful", "kept", "then_required", "where")
    ] + [
        (
            "scenarios/FIXTURE-002/1/ground_truth.yaml",
            "templates",
            "a template uses the parameter section, which the scenario does not declare",
        )
    ]


@pytest.mark.parametrize(
    ("scenario_id", "control"),
    [
        ("CONTROL-000", True),
        ("FIXTURE-CONTROL-000", True),
        ("CONTROL-ABC-123", True),
        ("FIXTURE-001", False),
        ("NGINX-001", False),
        ("CONTROLLER-001", False),
        ("CONTROL001-001", False),
        ("XCONTROL-001", False),
        ("FIXTURE-FIXTURE-CONTROL-001", False),
        ("FIXTURE-XCONTROL-001", False),
    ],
)
def test_a_control_scenario_is_known_by_its_id(scenario_id: str, control: bool) -> None:
    assert is_control(scenario_id) is control


def test_a_control_scenario_accepts_none_only_has_no_action_and_injects_nothing(
    catalog: Path,
) -> None:
    truth = f"{CONTROL}/ground_truth.yaml"
    only_none = (truth, "control", "a control scenario must accept the label none and no other")
    no_action = (truth, "control", "a control scenario must have no acceptable action")
    edit_yaml(catalog, truth, lambda d: d["diagnosis"].update(accepted_labels=["none", "unknown"]))
    assert found(catalog) == [only_none]
    edit_yaml(
        catalog,
        truth,
        lambda d: (
            d["diagnosis"].update(accepted_labels=["none"]),
            d["actions"]["acceptable"].append({"tool": "nginx_reload", "args": {}}),
        ),
    )
    assert found(catalog) == [no_action]

    def faulty(d: Any) -> None:
        d["fault"] = {"category": "service.stopped", "component": "x", "location": "y"}
        d["diagnosis"]["accepted_labels"] = ["service.stopped"]

    edit_yaml(catalog, truth, faulty)
    assert found(catalog) == [only_none, no_action]
    edit_yaml(
        catalog,
        f"{CONTROL}/scenario.yaml",
        lambda d: d["inject"].append({"primitive": "pg.stop", "params": {}}),
    )
    assert found(catalog) == [
        only_none,
        no_action,
        (f"{CONTROL}/scenario.yaml", "control", "a control scenario must have no inject step"),
    ]


def test_a_control_scenario_may_prepare_its_target(catalog: Path) -> None:
    def prepared(d: Any) -> None:
        d["setup"].append({"primitive": "file.mutate", "params": {"node": "web"}})
        d["preconditions"].append({"probe": "load_below", "params": {"node": "web"}})

    edit_yaml(catalog, f"{CONTROL}/scenario.yaml", prepared)
    edit_yaml(
        catalog,
        f"{CONTROL}/ground_truth.yaml",
        lambda d: d["actions"]["then_required"].append({"tool": "nginx_reload", "args": {}}),
    )
    assert found(catalog) == []


def test_only_a_control_scenario_accepts_the_label_none(catalog: Path) -> None:
    message = (
        "only a control scenario (one whose ID begins with CONTROL- or FIXTURE-CONTROL-)"
        " accepts the label none"
    )

    def healthy(d: Any) -> None:
        d["fault"] = {"category": "none", "component": None, "location": None}
        d["diagnosis"]["accepted_labels"] = ["none"]

    edit_yaml(catalog, f"{TWO}/ground_truth.yaml", healthy)
    assert found(catalog) == [(f"{TWO}/ground_truth.yaml", "control", message)]
    edit_yaml(
        catalog,
        f"{ONE}/ground_truth.yaml",
        lambda d: d["diagnosis"].update(accepted_labels=["config.syntax_error", "none"]),
    )
    assert found(catalog) == [
        (f"{ONE}/ground_truth.yaml", "control", message),
        (f"{TWO}/ground_truth.yaml", "control", message),
    ]


def test_every_scenario_has_a_confirm_and_a_verification_probe(catalog: Path) -> None:
    edit_yaml(catalog, f"{ONE}/scenario.yaml", lambda d: d.update(confirm=[]))
    edit_yaml(catalog, f"{CONTROL}/scenario.yaml", lambda d: d.update(confirm=[]))
    edit_yaml(catalog, f"{TWO}/ground_truth.yaml", lambda d: d.update(verification=[]))
    edit_yaml(catalog, f"{ONE}/ground_truth.yaml", lambda d: d.update(collateral=[]))
    assert found(catalog) == [
        ("scenarios/FIXTURE-001/1/scenario.yaml", "probes", "no confirm probe"),
        ("scenarios/FIXTURE-002/1/ground_truth.yaml", "probes", "no verification probe"),
        ("scenarios/FIXTURE-CONTROL-000/1/scenario.yaml", "probes", "no confirm probe"),
    ]


def test_every_label_of_a_ground_truth_is_in_the_taxonomy(catalog: Path) -> None:
    def labels(d: Any) -> None:
        d["fault"]["category"] = "config.syntax"
        d["diagnosis"]["accepted_labels"] = ["config.syntax", "config", "config.wrong_value"]

    edit_yaml(catalog, f"{ONE}/ground_truth.yaml", labels)
    edit_yaml(
        catalog,
        "taxonomy.yaml",
        lambda d: d["labels"].remove(
            {"name": "resource.disk_full", "description": "A file system has no space left."}
        ),
    )
    assert found(catalog) == [
        (
            "scenarios/FIXTURE-001/1/ground_truth.yaml",
            "labels",
            "the label config is not in the taxonomy",
        ),
        (
            "scenarios/FIXTURE-001/1/ground_truth.yaml",
            "labels",
            "the label config.syntax is not in the taxonomy",
        ),
        (
            "scenarios/FIXTURE-002/1/ground_truth.yaml",
            "labels",
            "the label resource.disk_full is not in the taxonomy",
        ),
    ]


def test_a_derived_image_has_its_overlay_and_an_overlay_its_node(catalog: Path) -> None:
    (catalog / "blueprints" / "fixture-web-app").rename(catalog / "blueprints" / "fixture-old-app")
    missing = (
        "blueprints/fixture-web.yaml",
        "overlay",
        "the derived image fixture-web-app of the node web has no overlay"
        " (blueprints/fixture-web-app/overlay)",
    )
    unused = ("blueprints/fixture-old-app/overlay", "overlay", "no node names this derived image")
    assert found(catalog) == [unused, missing]
    edit_yaml(
        catalog, WEB, lambda d: d["nodes"].append(copy.deepcopy(d["nodes"][0]) | {"alias": "web2"})
    )
    assert found(catalog) == [
        unused,
        missing,
        (missing[0], "overlay", missing[2].replace("the node web ", "the node web2 ")),
    ]
    edit_yaml(
        catalog,
        WEB,
        lambda d: [node["derived"].update(name="fixture-old-app") for node in d["nodes"]],
    )
    assert found(catalog) == []

    def plain(d: Any) -> None:
        d["nodes"][0].update(derived=None, components=[])
        d["nodes"][0]["patchable_paths"].pop()

    edit_yaml(catalog, STORE, plain)
    assert found(catalog) == [
        ("blueprints/fixture-store-app/overlay", "overlay", "no node names this derived image")
    ]


def shared(golden: str, golden_version: str, version: int, **settings: Any) -> Any:
    """A change that makes a blueprint's first node a user of the web's derived image."""

    def change(d: Any) -> None:
        web = yaml.safe_load((FIXTURE_CATALOG / WEB).read_text(encoding="utf-8"))["nodes"][0]
        node = d["nodes"][0]
        node["golden"] = {"blueprint": golden, "version": golden_version}
        node["derived"] = {"name": "fixture-web-app", "version": version}
        node["components"] = web["components"]
        node["components"][0]["settings"].update(settings)
        node["allowed_read_paths"] = web["allowed_read_paths"]
        node["patchable_paths"] = web["patchable_paths"]

    return change


SETTINGS = "another settings file or other settings of report-builder"


def differently(node: str, *differences: str) -> str:
    return (
        f"the node {node} describes the derived image fixture-web-app differently from the"
        f" node db of blueprints/fixture-store.yaml: {'; '.join(differences)}"
    )


def fewer_settings(d: Any) -> None:
    shared("web-nginx-flask", GOLDEN, 1)(d)
    del d["nodes"][0]["components"][0]["settings"]["batch_size"]


def other_file_and_settings(d: Any) -> None:
    shared("web-nginx-flask", GOLDEN, 1, workers=3)(d)
    d["nodes"][0]["components"][0]["settings_file"] = "/etc/fixture-app/other.conf"


def test_the_nodes_that_share_a_derived_image_describe_it_alike(catalog: Path) -> None:
    edit_yaml(
        catalog, WEB, lambda d: d["nodes"].append(copy.deepcopy(d["nodes"][0]) | {"alias": "web2"})
    )
    assert found(catalog) == []
    shutil.rmtree(catalog / "blueprints" / "fixture-store-app")
    edit_yaml(catalog, STORE, shared("web-nginx-flask", GOLDEN, 1))
    assert found(catalog) == []
    for change, differences in (
        (
            shared("pg-primary", GOLDEN, 1),
            [f"built on web-nginx-flask/{GOLDEN}, not pg-primary/{GOLDEN}"],
        ),
        (
            shared("web-nginx-flask", OTHER, 1),
            [f"built on web-nginx-flask/{GOLDEN}, not web-nginx-flask/{OTHER}"],
        ),
        (shared("web-nginx-flask", GOLDEN, 2), ["version 1, not 2"]),
        (shared("web-nginx-flask", GOLDEN, 1, workers=2), [SETTINGS]),
        (shared("web-nginx-flask", GOLDEN, 1, workers=1.0), [SETTINGS]),
        (shared("web-nginx-flask", GOLDEN, 1, workers=True), [SETTINGS]),
        (shared("web-nginx-flask", GOLDEN, 1, extra=1), [SETTINGS]),
        (shared("web-nginx-flask", GOLDEN, 1, workers="1"), [SETTINGS]),
        (fewer_settings, [SETTINGS]),
        (other_file_and_settings, [SETTINGS]),
        (
            shared("pg-primary", OTHER, 3, workers=4),
            [
                "version 1, not 3",
                f"built on web-nginx-flask/{GOLDEN}, not pg-primary/{OTHER}",
                SETTINGS,
            ],
        ),
    ):
        edit_yaml(catalog, STORE, change)
        assert found(catalog) == [
            ("blueprints/fixture-web.yaml", "overlay", differently(alias, *differences))
            for alias in ("web", "web2")
        ]


def test_the_order_of_settings_and_of_components_does_not_matter(catalog: Path) -> None:
    def second_component(d: Any) -> None:
        d["nodes"][0]["components"].append(
            {
                "name": "cache-warmer",
                "settings_file": "/etc/fixture-app/cache-warmer.conf",
                "settings": {"depth": 2, "eager": False},
            }
        )

    def reordered(d: Any) -> None:
        shared("web-nginx-flask", GOLDEN, 1)(d)
        second_component(d)
        d["nodes"][0]["components"].reverse()
        for component in d["nodes"][0]["components"]:
            component["settings"] = dict(reversed(component["settings"].items()))

    shutil.rmtree(catalog / "blueprints" / "fixture-store-app")
    edit_yaml(catalog, WEB, second_component)
    edit_yaml(catalog, STORE, reordered)
    store = yaml.safe_load((catalog / STORE).read_text(encoding="utf-8"))["nodes"][0]
    assert [c["name"] for c in store["components"]] == ["cache-warmer", "report-builder"]
    assert list(store["components"][1]["settings"]) == ["batch_size", "workers"]
    assert found(catalog) == []


def test_a_shared_derived_image_with_other_components_is_refused(catalog: Path) -> None:
    """The store's node on the web's image, still describing its own component."""

    def moved(d: Any) -> None:
        d["nodes"][0]["golden"] = {"blueprint": "web-nginx-flask", "version": GOLDEN}
        d["nodes"][0]["derived"] = {"name": "fixture-web-app", "version": 1}

    shutil.rmtree(catalog / "blueprints" / "fixture-store-app")
    edit_yaml(catalog, STORE, moved)
    assert found(catalog) == [
        (
            "blueprints/fixture-web.yaml",
            "overlay",
            differently(
                "web",
                "it does not describe the background component ledger-compactor, which db does",
                "it describes the background component report-builder, which db does not",
            ),
        )
    ]

    def bare(d: Any) -> None:
        d["nodes"][0]["components"] = []

    edit_yaml(catalog, STORE, bare)
    assert found(catalog) == [
        (
            "blueprints/fixture-web.yaml",
            "overlay",
            differently(
                "web", "it describes the background component report-builder, which db does not"
            ),
        )
    ]
    edit_yaml(catalog, WEB, bare)
    assert found(catalog) == []


def test_a_shared_component_with_another_settings_file_is_refused(catalog: Path) -> None:
    def elsewhere(d: Any) -> None:
        shared("web-nginx-flask", GOLDEN, 1)(d)
        d["nodes"][0]["components"][0]["settings_file"] = "/etc/fixture-app/other.conf"

    shutil.rmtree(catalog / "blueprints" / "fixture-store-app")
    edit_yaml(catalog, STORE, elsewhere)
    assert found(catalog) == [
        ("blueprints/fixture-web.yaml", "overlay", differently("web", SETTINGS))
    ]


def test_two_nodes_of_one_blueprint_and_three_descriptions_of_one_image(catalog: Path) -> None:
    def odd(d: Any) -> None:
        second = copy.deepcopy(d["nodes"][0]) | {"alias": "web2"}
        second["derived"]["version"] = 2
        third = copy.deepcopy(d["nodes"][0]) | {"alias": "web3"}
        third["golden"]["version"] = OTHER
        d["nodes"] += [second, third, copy.deepcopy(d["nodes"][0]) | {"alias": "web4"}]

    edit_yaml(catalog, WEB, odd)
    start = "describes the derived image fixture-web-app differently from the node web of"
    assert found(catalog) == [
        (
            "blueprints/fixture-web.yaml",
            "overlay",
            f"the node web2 {start} blueprints/fixture-web.yaml: version 2, not 1",
        ),
        (
            "blueprints/fixture-web.yaml",
            "overlay",
            f"the node web3 {start} blueprints/fixture-web.yaml:"
            f" built on web-nginx-flask/{OTHER}, not web-nginx-flask/{GOLDEN}",
        ),
    ]


def test_a_blueprint_with_two_derived_images(catalog: Path) -> None:
    store = yaml.safe_load((catalog / STORE).read_text(encoding="utf-8"))["nodes"][0]
    edit_yaml(catalog, WEB, lambda d: d["nodes"].append(store))
    assert found(catalog) == []
    edit_yaml(catalog, WEB, lambda d: d["nodes"][1]["derived"].update(version=5))
    assert found(catalog) == [
        (
            "blueprints/fixture-web.yaml",
            "overlay",
            "the node db describes the derived image fixture-store-app differently from the"
            " node db of blueprints/fixture-store.yaml: version 5, not 1",
        )
    ]


def test_findings_are_values_that_sort_by_path_rule_and_message() -> None:
    first = Finding("a/x.yaml", "names", "b")
    assert first == Finding("a/x.yaml", "names", "b")
    assert sorted([Finding("b", "a", "a"), Finding("a", "b", "b"), Finding("a", "b", "a")]) == [
        Finding("a", "b", "a"),
        Finding("a", "b", "b"),
        Finding("b", "a", "a"),
    ]
    assert len({first, Finding("a/x.yaml", "names", "b")}) == 1
