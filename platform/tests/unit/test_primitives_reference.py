"""The reference page of the library: written from the classes, and saying what they say."""

from __future__ import annotations

import inspect
import types
from collections.abc import Callable, Iterator
from typing import Annotated, Any, Literal, get_args, get_origin, get_type_hints

import pytest
from potka_primitives import library, reference
from potka_primitives.base import Params, Probe, Program, Requires, Step, Strict
from pydantic import AfterValidator, BaseModel, BeforeValidator, Field, field_validator
from pydantic import model_validator as together

STEPS: dict[str, type[Step[Any]]] = {**library.PRIMITIVES, **library.PROBES}
NODE = (
    "| `node` | string | yes | matches `^[a-z][a-z0-9]{0,14}$` |"
    " The alias of the node the step acts on. |"
)
PATH = (
    "at most 4096 characters; an absolute path in its normal form (of printable characters"
    " without a blank, not `/` itself, and not in `/dev`, `/proc` or `/sys`)"
)
UNIT = (
    "a unit's name that ends in `.service`, `.socket` or `.timer` (of letters, digits and"
    " `@._:-`; a template's unit has its instance)"
)
NOT_PROTECTED = "no unit of systemd's own or of the lab's channel into a target"


def section(page: str, name: str) -> str:
    """The lines of the page from one step's heading to the next heading."""
    start = page.index(f"### `{name}`\n")
    end = page.find("\n#", start + 1)
    return page[start:] if end == -1 else page[start : end + 1]


def rows(text: str) -> list[str]:
    """The rows of the parameter tables in TEXT."""
    return [line for line in text.splitlines() if line.startswith("| `")]


def test_the_page_lists_every_fault_primitive_and_then_every_probe_once_by_name() -> None:
    page = reference.documentation()
    assert page == reference.documentation()
    assert page.startswith(
        "---\n"
        "title: Fault primitives and probes\n"
        "type: reference\n"
        "audience: [researcher, scenario-author, developer, reader]\n"
        'applies_to: {platform: ">=0.0"}\n'
        "last_verified: {date: null, rc: null, by: null}\n"
        "---\n\n"
        "<!-- Written by `make primitives` from the library. Do not edit it by hand. -->\n\n"
        "# Fault primitives and probes\n\n"
        f"The library has {len(library.PRIMITIVES)} fault primitives and {len(library.PROBES)}"
        " probes. A scenario's steps name one and give its parameters: a fault primitive in"
        " `setup` and `inject`, a probe in `preconditions`, `confirm`, a ground truth's"
        " `verification` and `collateral`, and a blueprint's `health`. Every step acts on one"
        " node of the scenario's blueprint, named by `node`.\n\n"
        # What the lint holds a step to is the model, of which a table shows every rule.
        "`potka catalog lint` holds each step's parameters to the step's model, in every"
        " instance of the scenario. A table below has a row for each parameter: its type, what"
        " must hold of its value (Rule) and what it is (Description). What must hold of several"
        " parameters together stands below the table. The lint's finding names the parameter"
        " and says what is wrong with it, and shows no value.\n\n"
        # Only what the library itself holds is said of a fault and of a probe.
        "A fault primitive refuses a target it cannot be made on before it changes anything"
        " there. A probe changes nothing in a target: it passes, fails, or reports an error"
        " when it could not measure. A step runs only the programs that its section lists,"
        " each as a list of arguments and never through a shell.\n\n"
        "## Fault primitives\n\n"
    )
    assert page.endswith("|\n") and "\n\n\n" not in page
    assert all(line == line.rstrip() for line in page.splitlines())
    headings = [line for line in page.splitlines() if line.startswith("#")]
    assert headings == [
        "# Fault primitives and probes",
        "## Fault primitives",
        *(f"### `{name}`" for name in sorted(library.PRIMITIVES)),
        "## Probes",
        *(f"### `{name}`" for name in sorted(library.PROBES)),
    ]
    assert len(headings) == 3 + len(STEPS) and not set(library.PRIMITIVES) & set(library.PROBES)
    # Nothing in the library's words needed to be made plain: no markup stands in its prose.
    assert "\\`" not in page and "\\*" not in page and "\\_" not in page and "&lt;" not in page


def test_every_parameter_of_every_step_is_a_row_with_words_beside_it() -> None:
    page = reference.documentation()
    for name, step in STEPS.items():
        table = rows(section(page, name))
        # A row for each field of the model, in the model's order, and no other row.
        assert [row.split(" | ")[0] for row in table] == [
            f"| `{field}`" for field in step.Params.model_fields
        ], name
        assert table[0] == NODE, name
        for row in table:
            cells = row.replace("\\|", "").split(" | ")
            assert len(cells) == 5 and row.endswith(" |"), row
            # No parameter stands without a description.
            assert cells[4].removesuffix(" |").strip(), row
    assert len(rows(page)) == sum(len(step.Params.model_fields) for step in STEPS.values())


def test_a_row_says_what_the_model_s_schema_says_and_what_its_validators_ask() -> None:
    page = reference.documentation()
    for name, row in (
        (
            "disk.fill",
            "| `percent` | integer | yes | from 1 to 100 | The share of the root disk's"
            " configured size that is in use afterwards. |",
        ),
        (
            "disk.fill",
            f"| `path` | string | yes | {PATH} | The file the fault makes; its directory is"
            " there and on the root disk. |",
        ),
        (
            "file.write",
            f"| `path` | string | yes | {PATH}; no file in a place whose files are run, or name"
            " commands that are run (the units of systemd, cron's tables, what a login or a"
            " boot reads) | The file; its directory is there. |",
        ),
        (
            "file.write",
            "| `mode` | string | no | matches `^[0-7]{3}$`; no execute bit | Its permission"
            " bits, none of them an execute bit; a new file's are 644 unless given. |",
        ),
        (
            "file.write",
            "| `uid` | integer | no | from 0 to 2147483647 | Its owner; a new file's is 0"
            " unless given. |",
        ),
        (
            "nginx.corrupt_conf",
            f"| `path` | string | yes | {PATH}; a file below `/etc/nginx` | The configuration"
            " file. |",
        ),
        (
            "redis.stop",
            f"| `unit` | string | no; default `redis-server.service` | {UNIT}; {NOT_PROTECTED};"
            " the unit `redis-server.service` and no other | Redis's unit. |",
        ),
        (
            "systemd.action",
            f"| `unit` | string | yes | {UNIT}; {NOT_PROTECTED} | The unit. |",
        ),
        (
            "systemd.break_unit",
            "| `exec_start` | string | yes | at most 1000 characters; one line of words of"
            " letters, digits and `/._=:@,+-`, one blank between them (the first word is a"
            " program's absolute path in its normal form, and no word is a shell) | The command"
            " line: a program that is not in the target, and its arguments. |",
        ),
        (
            "pg.break_hba",
            "| `defect` | string | yes | one of `reject`, `no_entry` | `reject`: every"
            " connection over the network is rejected; `no_entry`: none has a rule. |",
        ),
        (
            "pg.stop",
            f"| `unit` | string | yes | {UNIT}; {NOT_PROTECTED}; PostgreSQL's unit:"
            " `postgresql.service`, or one cluster's (a cluster's unit is"
            " `postgresql@<version>-<cluster>.service`) | `postgresql.service` or a cluster's"
            " `postgresql@<version>-<cluster>.service`. |",
        ),
        (
            "pg_ready",
            "| `ready` | boolean | no; default `true` |  | Whether it must accept connections"
            " (true) or must not (false). |",
        ),
        (
            "inode_usage_below",
            f"| `path` | string | no; default `/` | {PATH}; or `/` | A path on the file system"
            " that is asked about. |",
        ),
        (
            "unit_state",
            "| `state` | string | yes | one of `active`, `reloading`, `inactive`, `failed`,"
            " `activating`, `deactivating`, `maintenance` | The state systemd must say the unit"
            " is in. |",
        ),
        (
            "unit_state",
            # A probe's unit is any unit: the rule of the units a fault may not act on is not its.
            f"| `unit` | string | yes | {UNIT} | The unit, by the form of its name alone: the"
            " probe only reads, so the units a fault may not act on, systemd's own and the"
            " lab's channel into a target, may be asked about. |",
        ),
        (
            "pg_query",
            "| `query` | string | yes | at most 1000 characters; one `SELECT` statement on one"
            " line, of printable characters, without a semicolon | One `SELECT` statement. |",
        ),
    ):
        assert row in rows(section(page, name)), name


def test_a_list_s_entries_and_what_holds_of_several_parameters_stand_below_the_table() -> None:
    page = reference.documentation()
    line = "string; at most 4096 characters; one line of text, without a line's end"
    equals = "The line as it is, without its end; exactly one line of the file equals it."
    assert section(page, "file.mutate").endswith(
        "| `edits` | list | yes | 1 to 16 entries | The edits, each made on what the one before"
        " it left. |\n"
        "\n"
        "An entry of `edits` is a mapping; its `edit` says which kind it is:\n"
        "\n"
        "- `edit`: `set_key`. Give the one line `key = value` of a file another value.\n"
        "  - `key` (string; matches `^[A-Za-z0-9_.-]{1,128}$`): The key: what stands before the"
        " line's first `=`, without blanks.\n"
        f"  - `value` ({line}): What stands after the `=` afterwards.\n"
        "- `edit`: `replace_line`. Put another line in place of the one line that equals a"
        " text.\n"
        f"  - `line` ({line}): {equals}\n"
        f"  - `replacement` ({line}): The line that takes its place.\n"
        "- `edit`: `insert_after`. Put a new line after the one line that equals a text.\n"
        f"  - `line` ({line}): {equals}\n"
        f"  - `text` ({line}): The new line that follows it.\n"
        "- `edit`: `delete_line`. Take away the one line that equals a text.\n"
        f"  - `line` ({line}): {equals}\n"
        "\n"
    )
    assert page.count("\nAn entry of `") == 1
    # What a model asks of several parameters is the last paragraph of the step's section.
    for name, said in {
        "net.netem": "One of `delay_ms` and `loss_percent` is given.",
        "permissions.break": "One of `mode`, `uid` and `gid` is given.",
        "nginx.corrupt_conf": (
            "`host` is given with the defect `bad_upstream_host` and with no other."
        ),
        "pg.break_hba": "The unit is the cluster's whose file it is, or `postgresql.service`.",
    }.items():
        assert section(page, name).endswith(f" |\n\n{said}\n\n"), name
    whole = [name for name, step in STEPS.items() if _whole(step.Params)]
    assert sorted(whole) == ["net.netem", "nginx.corrupt_conf", "permissions.break", "pg.break_hba"]


def _whole(model: type[BaseModel]) -> list[Callable[..., Any]]:
    """The validators of a whole model."""
    return [one.func for one in model.__pydantic_decorators__.model_validators.values()]


def _of_fields(hint: Any) -> Iterator[Callable[..., Any]]:
    """Every validator among a type's marks, of its alternatives, its entries and their fields."""
    if get_origin(hint) is Annotated:
        yield from (mark.func for mark in hint.__metadata__ if isinstance(mark, AfterValidator))
        yield from _of_fields(hint.__origin__)
    elif isinstance(hint, type) and issubclass(hint, BaseModel):
        for inner in get_type_hints(hint, include_extras=True).values():
            yield from _of_fields(inner)
    else:
        for inner in get_args(hint):
            if not isinstance(inner, str | int | types.EllipsisType):
                yield from _of_fields(inner)


def test_every_validator_of_every_model_has_its_words_in_the_step_s_section() -> None:
    page = reference.documentation()
    found: set[Callable[..., Any]] = set()
    for name, step in STEPS.items():
        said = section(page, name)
        asking = [
            *_whole(step.Params),
            *_of_fields(step.Params),
        ]
        for validator in asking:
            words = inspect.getdoc(validator)
            assert words, (name, validator.__name__)
            for part in words.split("\n\n"):
                # Its words, whatever the case of their first letter and without the full stop.
                shown = " ".join(part.split()).replace("``", "`").removesuffix(".")
                assert shown[1:] in said, (name, validator.__name__)
        found.update(asking)
    # The walk above finds them: seventeen validators of fields, four of whole models.
    assert len(found) == 21 and len({one for s in STEPS.values() for one in _whole(s.Params)}) == 4


def test_a_section_says_the_step_s_words_version_programs_service_and_facts() -> None:
    page = reference.documentation()
    assert section(page, "pg_ready").startswith(
        "### `pg_ready`\n"
        "\n"
        "Whether PostgreSQL on the node accepts connections on its local socket.\n"
        "\n"
        "The server is asked as the operating system user `postgres`, a role it knows: asked"
        " as any other, it would write the attempt in its log at every poll. It still writes a"
        " line for an attempt that it refuses before a session begins: while it starts or"
        " stops, and when the role `postgres`, the database of that name or the rule for local"
        " connections has been taken away. The cluster asked is the one on PostgreSQL's"
        " default socket and port.\n"
        "\n"
        "- Version: 1.\n"
        "- Runs in the node: `id` (coreutils), `pg_isready` (postgresql).\n"
        "- Acts on the service of the package `postgresql`, which the golden image of its node"
        " must carry.\n"
        "- Reports: `status`.\n"
        "\n"
        "| Parameter | Type | Required | Rule | Description |\n"
        "| --- | --- | --- | --- | --- |\n"
    )
    assert section(page, "net.netem").startswith(
        "### `net.netem`\n"
        "\n"
        "Delay or drop what a node sends on its network interface.\n"
        "\n"
        "The interface is `eth0`.\n"
        "\n"
        "- Version: 1.\n"
        "- Runs in the node: `tc` (iproute2).\n"
        "\n"
        "| Parameter | "
    )
    for name, step in STEPS.items():
        said = section(page, name)
        lines = said.splitlines()
        # The step's own words: the first paragraph of its class, without its name before it.
        own = step.__dict__.get("__doc__") or ""
        assert own.startswith(f"``{name}``: "), name
        first = " ".join(own.partition("\n\n")[0].split()).removeprefix(f"``{name}``: ")
        assert lines[:3] == [f"### `{name}`", "", first[0].upper() + first[1:].replace("``", "`")]
        assert lines.count(f"- Version: {step.version}.") == 1, name
        programs = [f"`{one.name}` ({one.package})" for one in step.requires.programs]
        runs = [line for line in lines if line.startswith("- Runs ")]
        assert runs == [
            f"- Runs in the node: {', '.join(programs)}."
            if programs
            else "- Runs no program in the node."
        ], name
        acts = [line for line in lines if line.startswith("- Acts on ")]
        assert acts == (
            []
            if step.requires.service is None
            else [
                f"- Acts on the service of the package `{step.requires.service}`, which the"
                " golden image of its node must carry."
            ]
        ), name
        reports = [line for line in lines if line.startswith("- Reports: ")]
        assert reports == (
            [f"- Reports: {', '.join(f'`{fact}`' for fact in sorted(step.reports))}."]
            if step.reports
            else []
        ), name


def _even(value: int) -> int:
    """An even number.

    Not ``0``, and at most ``a|b`` of them.
    """
    return value


class AwkwardParams(Params):
    """Parameters whose words and rules hold what a table and a code span are made of."""

    word: Annotated[str, Field(pattern="^(a|b)`c$")]
    """One | of   two
    words, with ``code``, a <tag>, a *star*, a snake_case word and a ` alone."""
    kind: Literal["a  b", "c``d`e"]
    """The kind."""
    flag: bool = False
    """Whether."""
    count: Annotated[int, Field(ge=0), AfterValidator(_even)] | None = None
    """How many, when given."""
    share: float = 0.5
    """A share."""

    @together(mode="after")
    def _both(self) -> AwkwardParams:
        """Both of ``flag`` and ``count``, or neither | of them."""
        return self


class Awkward(Probe[AwkwardParams]):
    """``awkward_text``: whether the page's   tables
    hold.

    More | words, with ``code`` and <no> markup.
    """

    name = "awkward_text"
    version = 3
    Params = AwkwardParams
    requires = Requires(programs=(Program("a-b", "its-package"),))
    reports = frozenset({"b_fact", "a_fact"})


def test_text_of_a_step_cannot_break_the_page_s_tables() -> None:
    assert reference.entry(Awkward) == [
        "### `awkward_text`",
        "",
        "Whether the page's tables hold.",
        "",
        # Outside a table a bar is no markup; what would be is made plain.
        "More | words, with `code` and &lt;no> markup.",
        "",
        "- Version: 3.",
        "- Runs in the node: `a-b` (its-package).",
        "- Reports: `a_fact`, `b_fact`.",
        "",
        "| Parameter | Type | Required | Rule | Description |",
        "| --- | --- | --- | --- | --- |",
        NODE,
        # A bar is escaped wherever it stands in a row; a span that holds a backtick has a
        # longer fence, and what it holds stands there as it is; nothing in prose is markup.
        "| `word` | string | yes | matches `` ^(a\\|b)`c$ `` | One \\| of two words, with `code`,"
        " a &lt;tag>, a \\*star\\*, a snake\\_case word and a \\` alone. |",
        "| `kind` | string | yes | one of `a  b`, ``` c``d`e ``` | The kind. |",
        # A default that is false is a default; one that is nothing is none.
        "| `flag` | boolean | no; default `false` |  | Whether. |",
        # A validator's rule follows the schema's, its further words in brackets.
        "| `count` | integer | no | from 0 to any; an even number (not `0`, and at most"
        " `a\\|b` of them) | How many, when given. |",
        "| `share` | number | no; default `0.5` |  | A share. |",
        "",
        "Both of `flag` and `count`, or neither | of them.",
        "",
    ]
    # The made-up step is no part of the library or of its page.
    assert "awkward_text" not in STEPS and "awkward_text" not in reference.documentation()


def _silent(value: str) -> str:
    return value


class Inner(Strict):
    """An entry."""

    size: int
    """Its size."""


class NoWords(Params):
    size: int


class SilentValidator(Params):
    size: Annotated[str, AfterValidator(_silent)]
    """A size."""


class OtherMark(Params):
    size: Annotated[str, BeforeValidator(_silent)]
    """A size."""


class OtherBound(Params):
    size: Annotated[int, Field(gt=0)]
    """A size."""


class OneModel(Params):
    inner: Inner
    """An entry."""


class PlainList(Params):
    sizes: tuple[int, ...]
    """Sizes."""


class NotToldApart(Params):
    entries: tuple[Inner, ...]
    """Entries."""


class LineEnd(Params):
    kind: Literal["a\nb"]
    """A kind."""


class OpenSpan(Params):
    size: int
    """A size with an ``open span."""


class OfOneField(Params):
    size: int
    """A size."""

    @field_validator("size")
    @classmethod
    def _check(cls, value: int) -> int:
        """A size that is checked."""
        return value


class SilentTogether(Params):
    size: int
    """A size."""

    @together(mode="after")
    def _check(self) -> SilentTogether:
        return self


def made(model: type[Params], words: str | None = "``made_up``: a step.") -> type[Step[Any]]:
    """A probe of the name ``made_up`` with MODEL as its parameters and WORDS as its own."""
    body: dict[str, Any] = {"name": "made_up", "version": 1, "Params": model}
    if words is not None:
        body["__doc__"] = words
    step: type[Step[Any]] = types.new_class(
        "MadeUp", (Probe,), exec_body=lambda ns: ns.update(body)
    )
    return step


@pytest.mark.parametrize(
    ("model", "said"),
    [
        (NoWords, "made_up: size: a field without words"),
        (SilentValidator, "made_up: size: a validator without words (_silent)"),
        (OtherMark, "made_up: size: a mark the page cannot show (BeforeValidator)"),
        (OtherBound, "made_up: size: a form the page cannot show (exclusiveMinimum)"),
        (OneModel, "made_up: inner: a form the page cannot show ($ref)"),
        (PlainList, "made_up: sizes: entries the page cannot show"),
        (NotToldApart, "made_up: entries: entries the page cannot show"),
        (LineEnd, "a value with a line's end cannot stand in a table"),
        (OpenSpan, "words with a code span that does not end"),
        (OfOneField, "made_up: a validator the page cannot show"),
        (SilentTogether, "made_up: a validator without words (_check)"),
    ],
)
def test_what_the_page_cannot_show_ends_its_generation(model: type[Params], said: str) -> None:
    with pytest.raises(
        ValueError,
        match="^" + said.replace("(", r"\(").replace(")", r"\)").replace("$", r"\$") + "$",
    ):
        reference.entry(made(model))


def test_a_step_has_words_of_its_own_that_begin_with_its_name() -> None:
    assert reference.entry(made(Params))[:3] == ["### `made_up`", "", "A step."]
    said = "^MadeUp: no words of its own that begin with its name$"
    for words in (None, "", "A step.", "``made_up``: ", "``other``: a step."):
        with pytest.raises(ValueError, match=said):
            reference.entry(made(Params, words))
    # A step that has none does not speak with the words of the class it is made from.
    # Under the same name, so that the words it would take over begin as its own would.
    own = {"name": Awkward.name, "version": 1, "Params": AwkwardParams}
    inherits = types.new_class("MadeUp", (Awkward,), exec_body=lambda ns: ns.update(own))
    assert inspect.getdoc(inherits) == inspect.getdoc(Awkward)
    assert (inspect.getdoc(inherits) or "").startswith(f"``{Awkward.name}``: ")
    with pytest.raises(ValueError, match=said):
        reference.entry(inherits)
