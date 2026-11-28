"""What the policy bundle is asked about a call, and what its answer is.

Every call is decided by the bundle, whether its tool exists, is offered
or is forbidden, and whether its arguments fit or not. The bundle is
asked with one JSON object, the policy input, made here from facts the
executor holds: who called (the socket the call came on), the run's
policy conditions and its latest diagnosis (what the orchestrator
registered), the run's counters, and the call as it was judged.

Nothing a model wrote reaches the input unchecked: a judged call holds a
tool's name only in the registry's form, and a reason only as a code of a
closed list. A call whose arguments were checked carries their checked
form (defaults filled in, paths normal), so that two spellings of one path
are one action; a text, such as a patch, enters as its SHA-256 and its
length and never as its content. A call whose arguments were not checked
or did not fit carries no argument at all, only the code of the reason,
and of its tool only the name.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal, get_args

from potka_core.canonical import canonical_json
from potka_tools.arguments import Call, ErrorCode, Meta
from potka_tools.registry import FORBIDDEN, Label, Name
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

Caller = Literal["agent", "orchestrator"]
Mode = Literal["diagnose_only", "recommend", "autonomous_low_risk", "human_in_loop"]
Effect = Literal["allow", "deny", "require_approval"]

UNKNOWN_TOOL: Final = "unknown_tool"
NOT_OFFERED: Final = "not_offered"
UNCHECKED: Final = (UNKNOWN_TOOL, NOT_OFFERED, FORBIDDEN)
"""Why a call's arguments were not checked: its tool is unknown, not offered, or forbidden."""
CODES: Final = frozenset({UNKNOWN_TOOL, NOT_OFFERED, *get_args(ErrorCode)})
"""Every reason a call can have for not being a checked one."""
_NAME: Final = TypeAdapter(Name)

Count = Annotated[int, Field(ge=0, le=1_000_000)]
Share = Annotated[float, Field(ge=0, le=1)]


class Model(BaseModel):
    """A part of the policy input: no key it does not know, no value of another type."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Budgets(Model):
    """A run's budgets for actions, from its agent config."""

    max_mutating_calls: Count
    max_same_action_repeats: Count


class Thresholds(Model):
    """The confidences a run's agent config asks for a low-risk action."""

    min_action_confidence: Share
    min_diagnosis_confidence: Share


class Counters(Model):
    """What a run has done before this call."""

    steps: Count
    """This caller's calls of the run before this one."""
    mutating_calls: Count
    """The run's mutating calls that were carried out."""
    same_action_repeats: Count
    """Its calls that were carried out with this tool and these checked arguments."""


class Diagnosis(Model):
    """A run's latest diagnosis, as the orchestrator gave it; or that there is none yet."""

    present: bool
    label: Label | None = None
    confidence: Share | None = None

    @model_validator(mode="after")
    def _whole(self) -> Diagnosis:
        if self.present != (self.label is not None and self.confidence is not None):
            raise ValueError("a diagnosis has a label and a confidence, and no diagnosis neither")
        if not self.present and (self.label is not None or self.confidence is not None):
            raise ValueError("a diagnosis has a label and a confidence, and no diagnosis neither")
        return self


NO_DIAGNOSIS: Final = Diagnosis(present=False)


@dataclass(frozen=True)
class Judged:
    """A call as it was judged before the bundle is asked.

    Attributes:
        name: The tool's name as the call gave it when it has the form of
            a registry's name; empty otherwise. A name of another form
            is refused here, so no text of a model's is ever a name.
        known: Whether the registry has the tool.
        offered: Whether the tool is offered to this caller: the run's
            tool subset for the agent, the evidence profile's tools for
            the orchestrator.
        call: The checked call; None when the arguments were not checked
            or did not fit.
        error: Why there is no checked call: one of `UNCHECKED`, or the
            code of the argument check that failed (`CODES`). None with
            a call.
        meta: What a checked call of a mutating tool says of itself,
            checked; None for every other call.

    Whether a tool that is said to be forbidden is so is not held here: the
    bundle decides that from its own data on the tool.
    """

    name: str
    known: bool
    offered: bool
    call: Call | None = None
    error: str | None = None
    meta: Meta | None = None

    def __post_init__(self) -> None:
        """Refuse a judgment that is not whole or that contradicts itself."""
        if type(self.known) is not bool or type(self.offered) is not bool:
            raise ValueError("whether a tool is known, and offered, is true or false")
        if not isinstance(self.name, str) or (self.name != "" and not _is_name(self.name)):
            raise ValueError("a tool's name has the registry's form or is empty")
        if self.name == "" and self.known:
            raise ValueError("a name that has not the registry's form is no tool's")
        if (self.call is None) == (self.error is None):
            raise ValueError("a judged call has a checked call or the code of why not")
        if self.error is not None and self.error not in CODES:
            raise ValueError("why a call is not a checked one is a code of the closed list")
        if not self.known and (self.offered or self.error != UNKNOWN_TOOL):
            raise ValueError("an unknown tool is offered to no one and is nothing but unknown")
        if self.error == UNKNOWN_TOOL and self.known:
            raise ValueError("a known tool is not unknown")
        if self.error == NOT_OFFERED and self.offered:
            raise ValueError("an offered tool is not unoffered")
        if self.known and not self.offered and self.error not in (NOT_OFFERED, FORBIDDEN):
            raise ValueError("the arguments of a tool that is not offered are never checked")
        if self.call is None:
            if self.meta is not None:
                raise ValueError("only a checked call has a checked statement of itself")
            return
        if self.call.tool.name != self.name:
            raise ValueError("the checked call is of another tool")
        if self.call.tool.risk == FORBIDDEN:
            raise ValueError("a forbidden tool's arguments are never checked")
        if (self.meta is not None) != self.call.tool.mutating:
            raise ValueError("a mutating call says how sure it is, and no other call does")
        if self.meta is not None:
            confidence = self.meta.action_confidence
            # A NaN is in no range: every comparison with it is false.
            if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
                raise ValueError("a confidence is a number from 0 to 1")


def _is_name(name: str) -> bool:
    try:
        _NAME.validate_python(name, strict=True)
    except ValidationError:
        return False
    return True


def text_digest(text: str) -> dict[str, str | int]:
    """What the bundle is given of a text: the SHA-256 of its UTF-8 bytes, and its length.

    The length is in characters, as the registry's ``max_length`` counts.
    """
    return {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "length": len(text)}


def policy_args(call: Call) -> dict[str, Any]:
    """CALL's arguments as the bundle is given them: the checked form, a text as its digest."""
    return {
        name: text_digest(str(value)) if call.tool.args[name].type == "text" else value
        for name, value in call.args.items()
    }


def policy_input(
    *,
    caller: Caller,
    mode: Mode,
    budgets: Budgets,
    thresholds: Thresholds,
    diagnosis: Diagnosis,
    counters: Counters,
    judged: Judged,
) -> dict[str, Any]:
    """The policy input of one call.

    Args:
        caller: Who called: the socket's word, never the request's.
        mode: The run's mode.
        budgets: The run's budgets.
        thresholds: The run's thresholds.
        diagnosis: The run's latest diagnosis, or `NO_DIAGNOSIS`.
        counters: What the run has done before this call.
        judged: The call as it was judged.

    Returns:
        The input, JSON data. The tool's version, risk and categories are
        there for a checked call only; the bundle takes them from its own
        data in every case.

    Raises:
        ValueError: The caller or the mode is none the bundle knows.
    """
    if caller not in get_args(Caller) or mode not in get_args(Mode):
        raise ValueError("not a caller or not a mode of the policy input")
    tool: dict[str, Any] = {"name": judged.name, "known": judged.known, "offered": judged.offered}
    if judged.call is not None:
        entry = judged.call.tool
        tool |= {
            "version": entry.version,
            "risk": entry.risk,
            "mutating": entry.mutating,
            "applicable_categories": list(entry.applicable_categories),
        }
    meta = {} if judged.meta is None else {"action_confidence": judged.meta.action_confidence}
    return {
        "caller": caller,
        "mode": mode,
        "tool": tool,
        "call": {"valid": judged.call is not None, "error": judged.error},
        "args": {} if judged.call is None else policy_args(judged.call),
        "meta": meta,
        # Before the first diagnosis there is no label and no confidence to state.
        "diagnosis": diagnosis.model_dump(mode="json", exclude_none=True),
        "counters": counters.model_dump(mode="json"),
        "budgets": budgets.model_dump(mode="json"),
        "thresholds": thresholds.model_dump(mode="json"),
    }


def input_hash(value: Mapping[str, Any]) -> str:
    """The SHA-256, in hexadecimal, of a policy input's canonical JSON.

    Raises:
        ValueError: VALUE has no canonical JSON form (potka-core's
            ``CanonicalJSONError``): it holds NaN or an infinity, an integer
            a JSON number cannot hold exactly, a lone surrogate, a key that
            is not a string, or a type JSON does not have. The message
            never shows the value.
    """
    return hashlib.sha256(canonical_json(value)).hexdigest()


Word = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*(:[a-z][a-z0-9_]*)?$", max_length=128)]
"""A rule's ID or a reason: lowercase words, a reason at most with a code after a colon."""
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Decision(Model):
    """What the bundle decided about a call, and which bundle it was."""

    effect: Effect
    rule_id: Word
    reason: Word
    bundle_hash: Sha256
