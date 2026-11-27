"""The platform's YAML: a strict subset whose data is exactly JSON's.

One document of mappings with string keys (no key twice), sequences and
scalars; no alias, no tag. A plain scalar is ``null`` or ``~`` (null),
``true`` or ``false``, or a number as JSON writes it; everything else is a
string, so ``yes``, ``on``, ``0x10``, ``1_000`` and a date are strings.

The files of a catalog and of the tool registry are read with it. It knows
neither: an error names the place in a document and never its text.
"""

from __future__ import annotations

import json
import re
from typing import Any

import yaml
from pydantic import JsonValue


class _Loader(yaml.SafeLoader):
    """YAML's safe loader, narrowed to the subset of the module's docstring."""

    # Only these plain scalars are not strings.
    yaml_implicit_resolvers = {}  # noqa: RUF012 (PyYAML's own class attribute, replaced)

    def compose_node(self, parent: Any, index: Any) -> Any:
        event = self.peek_event()  # type: ignore[no-untyped-call]
        if isinstance(event, yaml.AliasEvent):
            raise _refused("an alias", event)
        if getattr(event, "tag", None) is not None:
            raise _refused("a tag", event)
        return super().compose_node(parent, index)

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if not isinstance(key, str):
                raise _refused("a key that is not a string", key_node)
            if key in seen:
                raise _refused("a key given twice", key_node)
            seen.add(key)
        return super().construct_mapping(node, deep)


class _Refused(yaml.MarkedYAMLError):
    """YAML outside the subset, refused here: the problem's text is this module's own."""


def _refused(problem: str, where: Any) -> _Refused:
    return _Refused(problem=problem, problem_mark=where.start_mark)


_NUMBER = r"-?(?:0|[1-9][0-9]*)"
_Loader.add_implicit_resolver("tag:yaml.org,2002:null", re.compile(r"^(?:null|~|)$"), None)
_Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), None)
_Loader.add_implicit_resolver("tag:yaml.org,2002:int", re.compile(rf"^{_NUMBER}$"), None)
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(rf"^{_NUMBER}(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?$"),
    None,
)


def parse_yaml(text: str) -> JsonValue:
    """The JSON data of the YAML document TEXT (the module's docstring has the subset).

    Args:
        text: One YAML document.

    Returns:
        Its data: dictionaries with string keys, lists, strings, numbers,
        booleans and None.

    Raises:
        ValueError: TEXT is not YAML of the subset, is nested too deeply or
            holds a number too large for a float. The message gives the
            reason in this module's own words, with the line and column
            where YAML names them, and never the text.
    """
    try:
        data: JsonValue = yaml.load(text, Loader=_Loader)  # noqa: S506 (a safe loader, narrowed)
        json.dumps(data, allow_nan=False)
    except yaml.MarkedYAMLError as exc:
        # PyYAML's own messages can quote the document, so only this module's are shown.
        if isinstance(exc, _Refused):
            problem = str(exc.problem)
        elif exc.context == "expected a single document in the stream":
            problem = "more than one document"
        else:
            problem = "a syntax error"
        mark = exc.problem_mark
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        raise ValueError(f"not YAML of the platform's subset: {problem}{where}") from None
    except yaml.YAMLError:
        raise ValueError("not YAML of the platform's subset: a syntax error") from None
    except RecursionError:
        raise ValueError("not YAML of the platform's subset: nested too deeply") from None
    except ValueError:
        raise ValueError("not YAML of the platform's subset: a number too large") from None
    return data
