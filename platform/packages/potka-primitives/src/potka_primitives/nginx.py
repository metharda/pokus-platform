"""The faults of nginx's configuration.

A configuration file is read as nginx reads it: words, quoted words, the
three signs ``{``, ``}`` and ``;``, with comments passed over. A defect is
made at a place that the reading names, so that it is a defect to nginx
too: a brace in a comment or a semicolon in a quoted word is none. The
faults write no word of their own. A defect takes a sign away or doubles a
letter of the file's own; a host or an address comes from the scenario.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, model_validator

from potka_primitives.base import Effects, Facts, Params, Primitive, Requires
from potka_primitives.context import Node
from potka_primitives.errors import Refusal
from potka_primitives.files import read_text
from potka_primitives.values import AbsolutePath, below

NGINX: Final = "/etc/nginx"
SIGNS: Final = "{};"
# The blocks whose entries are no directives.
DATA_BLOCKS: Final = frozenset({"charset_map", "geo", "map", "split_clients", "types"})
_NAME: Final = re.compile(r"[a-z_]+")
# The address of a proxied server: a scheme, a host, and what follows it.
_PASSED: Final = re.compile(r"(https?://)([A-Za-z0-9.-]+)((?::[0-9]+)?(?:/[^\s]*)?)")
_PASSED_TO: Final = re.compile(r"(https?://)([A-Za-z0-9.-]+(?::[0-9]+)?)((?:/[^\s]*)?)")
_SERVER: Final = re.compile(r"([A-Za-z0-9.-]+)((?::[0-9]+)?)")
_HOST: Final = re.compile(
    r"(?=.{1,253}$)(?=.*[a-z])[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*"
)
_UPSTREAM: Final = re.compile(r"(?=.{1,259}$)[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?:([0-9]{1,5})")


@dataclass(frozen=True)
class Token:
    """A word or a sign of a configuration.

    Attributes:
        text: The word as it is written, quotes and all, or the sign.
        start: Where it begins in the file's text.
        sign: Whether it is one of ``{``, ``}`` and ``;``.
    """

    text: str
    start: int
    sign: bool

    @property
    def end(self) -> int:
        """Where it ends."""
        return self.start + len(self.text)


def tokens(text: str) -> list[Token]:
    """The words and signs of a configuration, comments left out.

    Raises:
        Refusal: ``no_place_for_defect``: a quoted word does not end, so
            the text cannot be read as a configuration.
    """
    found: list[Token] = []
    at, size = 0, len(text)
    while at < size:
        character = text[at]
        if character.isspace():
            at += 1
        elif character == "#":
            line = text.find("\n", at)
            at = size if line < 0 else line
        elif character in SIGNS:
            found.append(Token(character, at, True))
            at += 1
        elif character in "\"'":
            end = at + 1
            while end < size and text[end] != character:
                end += 2 if text[end] == "\\" else 1
            if end >= size:
                raise Refusal("no_place_for_defect")
            found.append(Token(text[at : end + 1], at, False))
            at = end + 1
        else:
            # A word ends at a blank, a semicolon or an opening brace, as for nginx: a
            # closing brace inside it is the word's, and so is what a backslash escapes. The
            # brace that opens a variable's name, right after its dollar sign, ends no word.
            end, variable = at, False
            while end < size and not text[end].isspace() and text[end] != ";":
                if text[end] == "{" and not variable:
                    break
                variable = text[end] == "$"
                end += 2 if text[end] == "\\" else 1
            end = min(end, size)
            found.append(Token(text[at:end], at, False))
            at = end
    return found


@dataclass(frozen=True)
class Statement:
    """One directive of a configuration, or what stands where one would.

    Attributes:
        words: Its words.
        sign: The sign that ends it; None at the end of the file.
        upstream: Whether an ``upstream`` block holds it.
        data: Whether a block holds it whose entries are no directives
            (the pairs of a ``map``, the types of ``types``, the code of
            a Lua block).
    """

    words: list[Token]
    sign: Token | None
    upstream: bool
    data: bool

    @property
    def simple(self) -> bool:
        """Whether it is a directive that a semicolon ends, outside every block of data."""
        return self.sign is not None and self.sign.text == ";" and not self.data


def _data(name: str) -> bool:
    return name in DATA_BLOCKS or name.endswith("_by_lua_block")


def statements(found: list[Token]) -> list[Statement]:
    """The directives of a configuration, each with the kind of block that holds it."""
    made: list[Statement] = []
    words: list[Token] = []
    blocks: list[str] = []
    for token in found:
        if not token.sign:
            words.append(token)
            continue
        if words:
            made.append(
                Statement(
                    words,
                    token,
                    bool(blocks) and blocks[-1] == "upstream",
                    any(_data(block) for block in blocks),
                )
            )
        if token.text == "{":
            blocks.append(words[0].text if words else "")
        elif token.text == "}" and blocks:
            blocks.pop()
        words = []
    if words:
        made.append(Statement(words, None, False, any(_data(block) for block in blocks)))
    return made


def _without(text: str, token: Token) -> str:
    return text[: token.start] + text[token.end :]


def _replaced(text: str, token: Token, new: str) -> str:
    return text[: token.start] + new + text[token.end :]


def missing_semicolon(text: str) -> str:
    """TEXT without the first semicolon that a closing brace or the file's end follows."""
    found = tokens(text)
    for index, token in enumerate(found):
        last = index + 1 == len(found)
        if token.text == ";" and token.sign and (last or found[index + 1].text == "}"):
            return _without(text, token)
    raise Refusal("no_place_for_defect")


def unclosed_block(text: str) -> str:
    """TEXT without its last closing brace."""
    closing = [token for token in tokens(text) if token.sign and token.text == "}"]
    if not closing:
        raise Refusal("no_place_for_defect")
    return _without(text, closing[-1])


def unknown_directive(text: str) -> str:
    """TEXT with the last letter of its first simple directive's name written twice.

    An entry of a ``map`` or of another block of data is no directive: a
    letter more would make another entry of it, and no error.
    """
    for one in statements(tokens(text)):
        name = one.words[0]
        if one.simple and _NAME.fullmatch(name.text):
            return _replaced(text, name, name.text + name.text[-1])
    raise Refusal("no_place_for_defect")


def _proxied(text: str) -> tuple[Token, re.Match[str]] | None:
    """The address of the first ``proxy_pass`` that names a host."""
    for one in statements(tokens(text)):
        if one.simple and len(one.words) == 2 and one.words[0].text == "proxy_pass":
            address = _PASSED.fullmatch(one.words[1].text)
            if address is not None and address[2] != "unix":
                return one.words[1], address
    return None


def bad_upstream_host(text: str, host: str) -> str:
    """TEXT with HOST as the host of its first proxied address, or of its first upstream server."""
    proxied = _proxied(text)
    if proxied is not None:
        token, address = proxied
        return _replaced(text, token, f"{address[1]}{host}{address[3]}")
    for one in statements(tokens(text)):
        if one.upstream and one.simple and len(one.words) >= 2:
            served = _SERVER.fullmatch(one.words[1].text)
            if one.words[0].text == "server" and served is not None and served[1] != "unix":
                return _replaced(text, one.words[1], f"{host}{served[2]}")
    raise Refusal("no_place_for_defect")


def with_upstream(text: str, upstream: str) -> str:
    """TEXT with UPSTREAM as the host and port of its first proxied address."""
    for one in statements(tokens(text)):
        if one.simple and len(one.words) == 2 and one.words[0].text == "proxy_pass":
            address = _PASSED_TO.fullmatch(one.words[1].text)
            if address is not None and not address[2].startswith("unix:"):
                return _replaced(text, one.words[1], f"{address[1]}{upstream}{address[3]}")
    raise Refusal("no_place_for_defect")


def _of_nginx(path: str) -> str:
    if not below(path, NGINX) or path == NGINX:
        raise ValueError("the file lies below /etc/nginx")
    return path


def _host(value: str) -> str:
    # "unix" before a colon is how nginx is told a socket, not a host.
    if _HOST.fullmatch(value) is None or value == "unix":
        raise ValueError("a host is a name of letters, digits, hyphens and dots, not an address")
    return value


def _upstream(value: str) -> str:
    matched = _UPSTREAM.fullmatch(value)
    if matched is None or not 1 <= int(matched[2]) <= 65535 or value.startswith("unix:"):
        raise ValueError("an upstream is a host or an address, a colon and a port")
    return value


ConfigPath = Annotated[AbsolutePath, AfterValidator(_of_nginx)]
"""A file of nginx's configuration, below ``/etc/nginx``."""
DEFECTS: Final = ("missing_semicolon", "unclosed_block", "unknown_directive", "bad_upstream_host")


class CorruptParams(Params):
    """A configuration file and the defect it gets."""

    path: ConfigPath
    """The file."""
    defect: Literal["missing_semicolon", "unclosed_block", "unknown_directive", "bad_upstream_host"]
    """The defect."""
    host: Annotated[str, AfterValidator(_host)] | None = None
    """The host that is put in, for ``bad_upstream_host`` and for no other defect."""

    @model_validator(mode="after")
    def _host_with_its_defect(self) -> CorruptParams:
        if (self.host is not None) != (self.defect == "bad_upstream_host"):
            raise ValueError("host is given with the defect bad_upstream_host and with no other")
        return self


class _Config[P: Params](Primitive[P]):
    """A fault that rewrites one file of nginx's configuration."""

    requires = Requires(service="nginx")
    reports = frozenset({"bytes"})

    def _changed(self, text: str, params: P) -> str:
        raise NotImplementedError

    def _result(self, node: Node, params: P) -> str:
        path: str = params.path  # type: ignore[attr-defined]
        before = read_text(node, path)
        result = self._changed(before, params)
        if result == before:
            raise Refusal("already_in_that_state")
        return result

    def side_effects(self, params: P) -> Effects:
        """The file."""
        return Effects(files=(params.path,))  # type: ignore[attr-defined]

    def _ready(self, node: Node, params: P) -> None:
        self._result(node, params)

    def _apply(self, node: Node, params: P) -> Facts:
        content = self._result(node, params).encode()
        node.write(params.path, content)  # type: ignore[attr-defined]
        return {"bytes": len(content)}


class NginxCorruptConf(_Config[CorruptParams]):
    """``nginx.corrupt_conf``: give a configuration file a defect nginx refuses."""

    name = "nginx.corrupt_conf"
    version = 1
    Params = CorruptParams

    def describe(self, params: CorruptParams) -> str:
        """One sentence that says what the step does with these parameters."""
        return (
            f"Gives the configuration file {params.path} on the node {params.node} the defect"
            f" {params.defect}."
        )

    def _changed(self, text: str, params: CorruptParams) -> str:
        if params.defect == "missing_semicolon":
            return missing_semicolon(text)
        if params.defect == "unclosed_block":
            return unclosed_block(text)
        if params.defect == "unknown_directive":
            return unknown_directive(text)
        return bad_upstream_host(text, params.host or "")


class UpstreamParams(Params):
    """A configuration file and where its proxied requests go."""

    path: ConfigPath
    """The file."""
    upstream: Annotated[str, AfterValidator(_upstream)]
    """The host or address and the port that requests are passed to afterwards."""


class NginxSetUpstream(_Config[UpstreamParams]):
    """``nginx.set_upstream``: pass a site's requests to another host and port."""

    name = "nginx.set_upstream"
    version = 1
    Params = UpstreamParams

    def describe(self, params: UpstreamParams) -> str:
        """One sentence that says what the step does with these parameters."""
        return (
            f"Makes {params.path} on the node {params.node} pass its requests to {params.upstream}."
        )

    def _changed(self, text: str, params: UpstreamParams) -> str:
        return with_upstream(text, params.upstream)


PRIMITIVES: Final = (NginxCorruptConf, NginxSetUpstream)
