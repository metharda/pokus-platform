"""Settings come from the service's JSON file only; secrets from their files."""

from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime
from enum import Flag, StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple

import pydantic.dataclasses
import pytest
from potka_core.settings import Settings, SettingsError, check_model, load_settings, read_secret
from pydantic import AfterValidator, BaseModel, Field, JsonValue, SecretStr

HIDDEN_VALUE = "hunter2-not-a-real-one"


class Limits(Settings):
    cpus: int = 1
    hosts: tuple[str, ...] = ()


class Example(Settings):
    max_concurrent_runs: int = 1
    note: str = "default"
    since: datetime | None = None
    ratio: float = 0.5
    limits: Limits = Limits()


def test_the_file_is_read_and_checked(tmp_path: Path) -> None:
    path = tmp_path / "orch.json"
    path.write_text(json.dumps({"max_concurrent_runs": 4}))
    assert load_settings(Example, path) == Example(max_concurrent_runs=4)
    path.write_text(json.dumps({"since": "2026-01-02T03:04:05Z"}))
    assert load_settings(Example, path).since == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert load_settings(Example, None) == Example()


def test_the_environment_and_arguments_are_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("MAX_CONCURRENT_RUNS", "max_concurrent_runs", "POTKA_MAX_CONCURRENT_RUNS"):
        monkeypatch.setenv(name, "9")
    monkeypatch.setattr("sys.argv", ["potka-orch", "--max-concurrent-runs", "9"])
    path = tmp_path / "orch.json"
    path.write_text("{}")
    assert load_settings(Example, path).max_concurrent_runs == 1


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('{"unknown": 1}', "unknown"),
        ('{"max_concurrent_runs": "4"}', "max_concurrent_runs"),
        (
            '{"max_concurrent_runs": 1, "max_concurrent_runs": 2}',
            "duplicate keys: max_concurrent_runs",
        ),
        ("{not json", "orch.json: Expecting property name"),
        ('{"limits": {"cpu": 2}}', "limits.cpu"),
        ('{"limits": {"cpus": 1, "cpus": 2}}', "duplicate keys: cpus"),
        ('{"ratio": NaN}', "NaN is not a JSON number"),
        ('{"ratio": -Infinity}', "-Infinity is not a JSON number"),
        ('{"ratio": 1e999}', "ratio"),
    ],
)
def test_a_bad_file_is_refused(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "orch.json"
    path.write_text(text)
    with pytest.raises(SettingsError, match=message):
        load_settings(Example, path)


@pytest.mark.parametrize(
    "text",
    [
        f'{{"db_password": "{HIDDEN_VALUE}"}}',
        f'{{"note": ["{HIDDEN_VALUE}"]}}',
        f'{{"limits": {{"hosts": "{HIDDEN_VALUE}"}}}}',
    ],
)
def test_an_error_never_shows_an_input_value(tmp_path: Path, text: str) -> None:
    path = tmp_path / "orch.json"
    path.write_text(text)
    with pytest.raises(SettingsError) as raised:
        load_settings(Example, path)
    assert HIDDEN_VALUE not in str(raised.value)
    assert HIDDEN_VALUE not in str(raised.value.__cause__)


def test_a_model_that_can_change_or_take_unknown_keys_is_refused() -> None:
    class Plain(BaseModel):
        x: int = 0

    class Nested(Settings):
        plain: Plain = Plain()

    class Listed(Settings):
        names: list[str]

    class Deep(Settings):
        pairs: tuple[dict[str, int], ...] = ()

    for model, field in ((Nested, "plain"), (Listed, "names"), (Deep, "pairs")):
        message = rf"^{model.__name__}\.{field}: .* is not a settings type"
        with pytest.raises(TypeError, match=message):
            load_settings(model, None)
    check_model(Example)


class Mode(StrEnum):
    FAST = "fast"


type Names = tuple[str, ...]
type Hosts = list[str]
type Tree = int | tuple[Tree, ...]
type Positive = Annotated[int, Field(gt=0)]


class Bits(Flag):
    A = 1
    B = 2


class Point(NamedTuple):
    x: int


@pydantic.dataclasses.dataclass
class PlainData:
    host: str


@pytest.mark.parametrize(
    "annotation",
    [Any, JsonValue, object, Hosts, PlainData, Point, tuple, dict[str, int], set[int]],
)
def test_a_value_that_can_change_or_hold_anything_is_refused(annotation: Any) -> None:
    model = type("Loose", (Settings,), {"__annotations__": {"value": annotation}})
    with pytest.raises(TypeError, match=r"^Loose\.value: .* is not a settings type"):
        check_model(model)


def test_immutable_types_and_their_subclasses_are_settings_types() -> None:
    class Strict(Settings):
        mode: Mode = Mode.FAST
        names: Names = ()
        level: Literal["a", "b"] = "a"
        count: Annotated[int, Field(ge=0)] = 0
        pair: tuple[str, int] = ("a", 1)
        limits: Limits | None = None
        bits: Bits = Bits.A
        tree: Tree = 0
        sizes: tuple[Annotated[int, Field(gt=0)], ...] = ()
        positive: Positive = 1

    check_model(Strict)
    assert load_settings(Strict, None).mode is Mode.FAST


def test_a_validator_or_a_default_cannot_make_a_value_that_can_change(tmp_path: Path) -> None:
    class Sorted(Settings):
        hosts: Annotated[tuple[str, ...], AfterValidator(sorted)] = ()

    class Defaulted(Settings):
        hosts: tuple[str, ...] = Field(default_factory=list)  # type: ignore[arg-type]

    path = tmp_path / "orch.json"
    path.write_text('{"hosts": ["b", "a"]}')
    message = r"\.hosts: the validated value is a list, not a settings type"
    with pytest.raises(TypeError, match=rf"^Sorted{message}"):
        load_settings(Sorted, path)
    with pytest.raises(TypeError, match=rf"^Defaulted{message}"):
        load_settings(Defaulted, None)


def test_defaults_that_do_not_validate_are_a_settings_error() -> None:
    class Required(Settings):
        x: int

    with pytest.raises(SettingsError, match=r"^the defaults: "):
        load_settings(Required, None)


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match=r"missing\.json"):
        load_settings(Example, tmp_path / "missing.json")


def test_settings_are_immutable_at_every_depth() -> None:
    settings = Example()
    with pytest.raises(ValueError):
        settings.note = "changed"
    with pytest.raises(ValueError):
        settings.limits.cpus = 4


def test_a_secret_is_read_from_its_file(tmp_path: Path) -> None:
    (tmp_path / "db-password").write_text("s3cr3t-value\n")
    secret = read_secret(tmp_path, "db-password")
    assert isinstance(secret, SecretStr)
    assert secret.get_secret_value() == "s3cr3t-value"
    assert "s3cr3t-value" not in repr(secret)
    (tmp_path / "token").write_bytes(b"ab\rcd\r\n\n")
    assert read_secret(tmp_path, "token").get_secret_value() == "ab\rcd\r\n"


@pytest.mark.parametrize("name", ["../etc/passwd", "Upper", "a/b", "", "x" * 65])
def test_a_secret_name_outside_the_manifest_s_form_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(SettingsError, match="not a secret name"):
        read_secret(tmp_path, name)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "the secret token cannot be read: FileNotFoundError"),
        (b"pa\xe9ss", "the secret token cannot be read: UnicodeDecodeError"),
        (b"\n", "the secret token is empty"),
    ],
)
def test_a_secret_error_shows_no_path_no_byte_and_no_cause(
    tmp_path: Path, content: bytes | None, message: str
) -> None:
    if content is not None:
        (tmp_path / "token").write_bytes(content)
    with pytest.raises(SettingsError) as raised:
        read_secret(tmp_path, "token")
    assert str(raised.value) == message
    assert raised.value.__cause__ is None
    printed = "".join(traceback.format_exception(raised.value))
    assert str(tmp_path) not in printed
    assert "0xe9" not in printed
