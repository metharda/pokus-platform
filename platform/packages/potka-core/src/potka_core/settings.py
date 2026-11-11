"""A service's configuration and secrets.

The settings come only from the service's JSON file (its path is the
``config`` entry of ``principals.json``), validated strictly by the
service's pydantic model; nothing is read from the command line or the
environment. Every model in the settings derives from `Settings`, and
every value is a scalar, an Enum, a Literal, a tuple, a frozenset or a
nested `Settings` model (`check_model`, and `check_values` for the
values): unknown keys are refused and nothing can be changed at any
depth. Duplicate keys, NaN and the infinities are refused, and no
validation error shows an input value (the file is not secret: an error
in its encoding or its JSON shows its place and the bytes there, a
duplicate key its name). Each secret is one file in the service's
secrets directory, read into a ``SecretStr`` so it never shows in logs.
"""

from __future__ import annotations

import json
import re
import types
import typing
from datetime import date, time, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePath
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SecretBytes, SecretStr, ValidationError

SECRET_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")


class Settings(BaseModel):
    """The base of every settings model: unknown keys refused, immutable, inputs hidden."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, allow_inf_nan=False
    )


class SettingsError(ValueError):
    """The settings or a secret cannot be read."""


# The value types a setting may have (and their subclasses); anything else is refused.
SCALARS = (
    str,
    bytes,
    int,
    float,
    bool,
    Decimal,
    date,
    time,
    timedelta,
    UUID,
    PurePath,
    Enum,
    SecretStr,
    SecretBytes,
)


def _check_annotation(
    model: type[BaseModel], name: str, annotation: Any, seen: set[object]
) -> None:
    # A type alias (typing's or typing_extensions'), followed once.
    if type(annotation).__name__ == "TypeAliasType" and hasattr(annotation, "__value__"):
        if annotation not in seen:
            seen.add(annotation)
            _check_annotation(model, name, annotation.__value__, seen)
        return
    origin = typing.get_origin(annotation)
    arguments = typing.get_args(annotation)
    if annotation is None or annotation is type(None) or origin is typing.Literal:
        return
    if origin is typing.Annotated:
        _check_annotation(model, name, arguments[0], seen)
        return
    if origin in (typing.Union, types.UnionType) or (origin in (tuple, frozenset) and arguments):
        for argument in arguments:
            if argument is not Ellipsis:
                _check_annotation(model, name, argument, seen)
        return
    if origin is None and isinstance(annotation, type):
        if issubclass(annotation, Settings):
            _check(annotation, seen)
            return
        if issubclass(annotation, SCALARS):
            return
    raise TypeError(
        f"{model.__name__}.{name}: {annotation!r} is not a settings type:"
        " a scalar, an Enum, a Literal, tuple[...], frozenset[...] or a Settings model"
    )


def _check(model: type[BaseModel], seen: set[object]) -> None:
    if model in seen:
        return
    seen.add(model)
    for name, field in model.model_fields.items():
        _check_annotation(model, name, field.annotation, seen)


def check_model(model: type[BaseModel]) -> None:
    """Check that MODEL is immutable and refuses unknown keys at every depth.

    Args:
        model: A settings model.

    Raises:
        TypeError: A field's type is not a settings type: a scalar (`SCALARS`
            and their subclasses), an Enum, a Literal, ``tuple[...]``,
            ``frozenset[...]`` (of settings types) or a `Settings` model;
            type aliases are followed.
    """
    _check(model, set())


def _check_value(model: str, name: str, value: Any) -> None:
    if value is None or isinstance(value, SCALARS):
        return
    if isinstance(value, Settings):
        check_values(value)
        return
    if type(value) in (tuple, frozenset):
        for item in value:
            _check_value(model, name, item)
        return
    raise TypeError(
        f"{model}.{name}: the validated value is a {type(value).__qualname__},"
        " not a settings type (a validator or a default made it)"
    )


def check_values(settings: Settings) -> None:
    """Check that every value of SETTINGS, at any depth, is of a settings type.

    A field's annotation can be a settings type while a validator or a
    default puts another value there (a list, for example).

    Args:
        settings: Validated settings.

    Raises:
        TypeError: A value is not of a settings type.
    """
    for name in type(settings).model_fields:
        _check_value(type(settings).__name__, name, getattr(settings, name))


def _no_constant(name: str) -> Any:
    raise SettingsError(f"{name} is not a JSON number")


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise SettingsError(f"duplicate keys: {', '.join(duplicates)}")
    return dict(pairs)


def load_settings[S: Settings](model: type[S], path: Path | None) -> S:
    """The settings from PATH, or the model's defaults when the service has no file.

    Args:
        model: The service's settings model.
        path: Its JSON file (``principals.json``'s ``config``), or None.

    Returns:
        The validated settings.

    Raises:
        SettingsError: The file cannot be read or parsed, has a key twice, holds
            NaN or an infinity, or does not match the model.
        TypeError: The model, or a value a validator or a default made, is
            not immutable at every depth (`check_model`, `check_values`).
    """
    check_model(model)
    if path is None:
        try:
            settings = model()
        except ValidationError as exc:
            raise SettingsError(f"the defaults: {exc}") from exc
    else:
        try:
            raw = path.read_bytes()
            json.loads(raw, object_pairs_hook=_unique, parse_constant=_no_constant)
        except (OSError, ValueError) as exc:
            raise SettingsError(f"{path}: {exc}") from exc
        try:
            settings = model.model_validate_json(raw, strict=True)
        except ValueError as exc:
            raise SettingsError(f"{path}: {exc}") from exc
    check_values(settings)
    return settings


def read_secret(secrets_dir: Path, name: str) -> SecretStr:
    """The secret NAME from the service's secrets directory.

    Args:
        secrets_dir: The directory (``principals.json``'s ``secrets_dir``).
        name: The secret's name in the manifest.

    Returns:
        The secret, its bytes as they are but one trailing newline.

    Raises:
        SettingsError: The name is not a secret name, the file cannot be read
            or is not UTF-8, or the secret is empty. The error names neither
            the file's path nor any of its bytes, and carries no cause.
    """
    if not SECRET_NAME.fullmatch(name):
        raise SettingsError(f"not a secret name: {name!r}")
    try:
        text = (secrets_dir / name).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        reason = type(exc).__name__
        raise SettingsError(f"the secret {name} cannot be read: {reason}") from None
    secret = text.removesuffix("\n")
    if not secret:
        raise SettingsError(f"the secret {name} is empty")
    return SecretStr(secret)
