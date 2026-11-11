"""Events: the CloudEvents 1.0 envelope, in its structured JSON form.

Every externally visible action of a service is an event of type
``potka.<service>.<event>`` (the event's name may have several parts, as in
``potka.orch.training.started``), with JSON data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

from potka_core.canonical import CanonicalJSONError, canonical_json
from potka_core.ids import Id, new_id

NAME = r"[a-z][a-z0-9-]*"
TYPE_PATTERN = rf"^potka\.{NAME}(\.{NAME})+$"


class Event(BaseModel):
    """A CloudEvents 1.0 event.

    Attributes:
        specversion: The CloudEvents version, always ``1.0``.
        id: The event's ULID.
        source: The emitting service, as ``/potka/<service>``.
        type: ``potka.<service>.<event>``.
        time: When the action happened (an aware time; its JSON form is in UTC).
        datacontenttype: Always ``application/json``.
        subject: What the event is about (a run ID, for example), if any.
        data: The event's JSON data.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    specversion: Literal["1.0"] = "1.0"
    id: Id
    source: Annotated[str, Field(pattern=rf"^/potka/{NAME}$")]
    type: Annotated[str, Field(pattern=TYPE_PATTERN)]
    time: AwareDatetime
    datacontenttype: Literal["application/json"] = "application/json"
    subject: Annotated[str, Field(min_length=1)] | None = None
    data: dict[str, JsonValue]

    @model_validator(mode="after")
    def _data_is_canonical_json(self) -> Event:
        try:
            canonical_json(self.data)
        except CanonicalJSONError as exc:
            raise ValueError(f"data has no canonical JSON form: {exc}") from exc
        return self


def new_event(
    service: str, name: str, data: dict[str, JsonValue], subject: str | None = None
) -> Event:
    """A new event of SERVICE, now.

    Args:
        service: The emitting service's name in the manifest.
        name: The event's name below the service (``training.started``).
        data: The event's JSON data.
        subject: What the event is about, if anything.

    Returns:
        The event, with a new ULID and the current UTC time.
    """
    return Event(
        id=new_id(),
        source=f"/potka/{service}",
        type=f"potka.{service}.{name}",
        time=datetime.now(UTC),
        subject=subject,
        data=data,
    )


def event_json(event: Event) -> bytes:
    """EVENT's canonical JSON (RFC 8785), with the time in UTC and no null fields.

    Args:
        event: The event.

    Returns:
        The canonical bytes.
    """
    fields: dict[str, Any] = event.model_dump(mode="json", exclude_none=True)
    fields["time"] = event.time.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return canonical_json(fields)
