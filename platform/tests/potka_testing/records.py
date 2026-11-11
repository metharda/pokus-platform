"""Rows for the database tests: a campaign with its references, runs and their assignments."""

from __future__ import annotations

from typing import Any

import psycopg

RUN = "01HZY00000000000000000RN01"
CAMPAIGN = "01HZY000000000000000CAMPGN"
ARTIFACT = "a" * 64

Conn = psycopg.Connection[tuple[Any, ...]]


def seed(conn: Conn) -> None:
    """An artifact, a catalog version, a scenario, a deployment, an agent config, a campaign.

    Args:
        conn: A connection of the migrator.
    """
    conn.execute(
        "INSERT INTO artifacts (sha256, size, media_type, compressed, created_at, created_by)"
        " VALUES (%s, 1, 'text/plain', false, now(), 'orch')",
        (ARTIFACT,),
    )
    conn.execute(
        "INSERT INTO catalog_versions (ref, commit, imported_at, content_hash)"
        " VALUES ('main', %s, now(), 'h')",
        ("c" * 40,),
    )
    for scenario in ("FIXTURE-1", "FIXTURE-2"):
        conn.execute(
            "INSERT INTO scenario_versions (catalog_version_id, scenario_id, version, title,"
            " risk_level, isolation, blueprint, split, tags, public_meta)"
            " VALUES (1, %s, '1', 't', 'low', 'container', 'b', 'test', '{}', '{}')",
            (scenario,),
        )
    conn.execute(
        "INSERT INTO models (hf_repo, revision, files, registered_at)"
        " VALUES ('example/model', 'r', '{}', now())"
    )
    conn.execute(
        "INSERT INTO deployments (model_id, adapter_ids, profile, serving_args,"
        " vllm_image_digest, spec_hash) VALUES (1, '{}', 'p', '{}', %s, 's')",
        ("sha256:" + "d" * 64,),
    )
    conn.execute(
        "INSERT INTO agent_configs (name, version, prompt_template_hash, prompt_artifact, mode,"
        " budgets, sampling, tool_registry_version, tool_subset, evidence_profile,"
        " policy_bundle_hash, hash) VALUES ('a', '1', 'p', %s, 'm', '{}', '{}', '1', '{}',"
        " '{}', 'b', 'h')",
        (ARTIFACT,),
    )
    conn.execute(
        "INSERT INTO campaigns (id, name, submitter, spec, spec_hash, official,"
        " official_reasons, state, created_at, platform_version, catalog_version_id,"
        " deployment_id, agent_config_id, approval_mode, run_mode, concurrency, seed)"
        " VALUES (%s, 'c', 'researcher1', '{}', 'h', false, '[]', 'created', now(), '0.0.0',"
        " 1, 1, 1, 'approve_all', 'measurement', 1, 7)",
        (CAMPAIGN,),
    )


def insert_run(conn: Conn, **values: object) -> None:
    """A queued run of the campaign; VALUES replace or add columns.

    Args:
        conn: A connection.
        **values: Columns of ``runs``.
    """
    row: dict[str, object] = {
        "id": RUN,
        "campaign_id": CAMPAIGN,
        "repetition": 0,
        "seed_sampling": 1,
        "phase": "queued",
        "official": False,
    }
    row.update(values)
    names = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    conn.execute(f"INSERT INTO runs ({names}) VALUES ({marks})", list(row.values()))  # noqa: S608


def assign(conn: Conn, run_id: str = RUN, scenario_version_id: int = 1, instance: int = 3) -> None:
    """The run's assignment in ``restricted``.

    Args:
        conn: A connection of the migrator or the controller.
        run_id: The run.
        scenario_version_id: Its scenario version.
        instance: Its instance index.
    """
    conn.execute(
        "INSERT INTO restricted.run_assignments (run_id, scenario_version_id, instance_index,"
        " seed_scenario, parameters, gt_hash) VALUES (%s, %s, %s, 11, '{}', 'g')",
        (run_id, scenario_version_id, instance),
    )
