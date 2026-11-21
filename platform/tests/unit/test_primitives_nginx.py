"""The faults of nginx's configuration: the reading of a file, each defect's place."""

from __future__ import annotations

from typing import Any

import pytest
from potka_incus.fake import FakeIncus
from potka_primitives import library, nginx
from potka_primitives.base import Effects, Requires
from potka_primitives.context import Targets
from potka_primitives.errors import FaultFailed, Refusal, SetupRefused
from potka_primitives.nginx import NginxCorruptConf, NginxSetUpstream
from potka_testing.targets import INSTANCES, inside, run, state
from pydantic import ValidationError

PATH = "/etc/nginx/sites-available/default"
MAIN = """\
user www-data;
worker_processes auto;  # one for each processor
events {
    worker_connections 768;
    # multi_accept on;
}
http {
    log_format main '$remote_addr; "$request" {$status}';
    include /etc/nginx/mime.types;
    server_tokens off;
}
#mail {
#    server { listen localhost:110; }
#}
"""
SITE = """\
upstream backend {
    server 127.0.0.1:8000 weight=5;
    server app2.internal:8000 backup;
}
server {
    listen 80 default_server;
    location / {
        proxy_set_header Host $host;
        proxy_pass http://127.0.0.1:8000/app;
    }
    location /api {
        proxy_pass http://backend;
    }
}
"""


def lab(text: str = SITE, path: str = PATH) -> tuple[FakeIncus, Targets]:
    fake, targets = run()
    within = inside(fake, "web")
    within.mkdir("/etc/nginx/sites-available")
    within.write(path, text.encode(), uid=0, gid=0, mode=0o640)
    return fake, targets


def corrupt(defect: str, path: str = PATH, **more: Any) -> Any:
    return NginxCorruptConf.parameters({"node": "web", "path": path, "defect": defect, **more})


def refused(error: Any, reason: str) -> None:
    assert (str(error), error.alias) == (reason, "web")
    assert error.__context__ is None and error.__cause__ is None
    assert INSTANCES["web"] not in repr(error) and "/etc/nginx" not in f"{error!r} {vars(error)}"


def test_the_library_has_the_two_nginx_faults_and_they_run_no_program() -> None:
    assert {"nginx.corrupt_conf", "nginx.set_upstream"} <= set(library.PRIMITIVES)
    assert NginxCorruptConf.requires == NginxSetUpstream.requires == Requires((), "nginx")
    assert nginx.DEFECTS == (
        "missing_semicolon",
        "unclosed_block",
        "unknown_directive",
        "bad_upstream_host",
    )


def test_a_configuration_is_read_as_words_and_signs_without_its_comments() -> None:
    read = [token.text for token in nginx.tokens(MAIN)]
    assert read == [
        *("user", "www-data", ";", "worker_processes", "auto", ";"),
        *("events", "{", "worker_connections", "768", ";", "}"),
        *("http", "{", "log_format", "main", "'$remote_addr; \"$request\" {$status}'", ";"),
        *("include", "/etc/nginx/mime.types", ";", "server_tokens", "off", ";", "}"),
    ]
    assert all(MAIN[token.start : token.end] == token.text for token in nginx.tokens(MAIN))
    # A quoted word may hold an escaped quote; a variable in braces is part of its word.
    assert [t.text for t in nginx.tokens('a "x \\" ; }" ${v}b{')] == [
        "a",
        '"x \\" ; }"',
        "${v}b",
        "{",
    ]
    assert [t.sign for t in nginx.tokens("a;{}")] == [False, True, True, True]
    for text, read in {
        # What a backslash escapes is the word's, a semicolon and a brace too.
        "a\\;b c;": ["a\\;b", "c", ";"],
        "a\\{b {": ["a\\{b", "{"],
        # A closing brace and a number sign inside a word are the word's.
        "a b}c;": ["a", "b}c", ";"],
        "a b#c; # d;": ["a", "b#c", ";"],
        "a }b;": ["a", "}", "b", ";"],
        # A condition's words, a quoted one among them, and a pattern that ends in a dollar sign.
        'if ($x = "a;b") {': ["if", "($x", "=", '"a;b"', ")", "{"],
        "location ~ \\.php$ {": ["location", "~", "\\.php$", "{"],
        # Only the brace right after a dollar sign opens a name; an escaped dollar sign is none.
        "set $a ${b}c;": ["set", "$a", "${b}c", ";"],
        "a ${b c;": ["a", "${b", "c", ";"],
        "a \\${ b": ["a", "\\$", "{", "b"],
        "a b\\": ["a", "b\\"],
    }.items():
        found = nginx.tokens(text)
        assert [token.text for token in found] == read, text
        assert all(text[token.start : token.end] == token.text for token in found)
    assert nginx.tokens("") == [] and nginx.tokens("# only a comment") == []
    for unended in ('a "b;', "a 'b }", 'x "\\'):
        with pytest.raises(Refusal, match="no_place_for_defect"):
            nginx.tokens(unended)


def test_a_missing_semicolon_is_one_that_a_closing_brace_follows() -> None:
    assert nginx.missing_semicolon(MAIN) == MAIN.replace(
        "    worker_connections 768;\n", "    worker_connections 768\n"
    )
    # Not the first semicolon of the file, which another directive follows, and none
    # that stands in a comment or in a quoted word.
    assert nginx.missing_semicolon("a 1;\nb 2;\n") == "a 1;\nb 2\n"
    assert nginx.missing_semicolon("a { b 'x;' ; # c;\n}") == "a { b 'x;'  # c;\n}"
    for none in ("", "a { }", "# a;\n", "a 1; b {"):
        with pytest.raises(Refusal, match="no_place_for_defect"):
            nginx.missing_semicolon(none)


def test_an_unclosed_block_lacks_its_last_closing_brace_that_is_one() -> None:
    assert nginx.unclosed_block(MAIN) == MAIN.replace(
        "    server_tokens off;\n}\n", "    server_tokens off;\n\n"
    )
    assert nginx.unclosed_block(MAIN).endswith(
        "#mail {\n#    server { listen localhost:110; }\n#}\n"
    )
    assert nginx.unclosed_block("a { b '}'; }") == "a { b '}'; "
    for none in ("", "a 1;", "# }", "a '}';"):
        with pytest.raises(Refusal, match="no_place_for_defect"):
            nginx.unclosed_block(none)


def test_an_unknown_directive_is_the_first_simple_one_with_its_last_letter_twice() -> None:
    assert nginx.unknown_directive(MAIN) == MAIN.replace("user www-data;", "userr www-data;")
    assert nginx.unknown_directive(SITE) == SITE.replace(
        "    server 127.0.0.1:8000 weight=5;", "    serverr 127.0.0.1:8000 weight=5;"
    )
    # A block's name is no simple directive, and neither is a quoted or an odd first word.
    assert nginx.unknown_directive("events { }\n'quoted' 1;\n$odd 2;\nlisten 80;") == (
        "events { }\n'quoted' 1;\n$odd 2;\nlistenn 80;"
    )
    for none in ("", "events { }", "a 1"):
        with pytest.raises(Refusal, match="no_place_for_defect"):
            nginx.unknown_directive(none)


WEBSOCKETS = """\
map $http_upgrade $connection_upgrade {
    default upgrade;
    '' close;
}
"""


def test_an_entry_of_a_block_of_data_is_no_directive() -> None:
    # A letter more would make another entry of a map, which nginx accepts.
    site = WEBSOCKETS + SITE
    assert nginx.unknown_directive(site) == WEBSOCKETS + nginx.unknown_directive(SITE)
    for block in sorted(nginx.DATA_BLOCKS):
        text = f"http {{ {block} $a $b {{ hostnames; default x; }} sendfile on; }}"
        assert nginx.unknown_directive(text) == text.replace("sendfile on;", "sendfilee on;")
        with pytest.raises(Refusal, match="no_place_for_defect"):
            nginx.unknown_directive(f"{block} $a $b {{ default x; }}")
    lua = "location / { content_by_lua_block { ngx.say(1); } }\ngzip on;"
    assert nginx.unknown_directive(lua) == lua.replace("gzip on;", "gzipp on;")
    assert nginx.DATA_BLOCKS == {"charset_map", "geo", "map", "split_clients", "types"}
    # The same for an address: a map's entry that looks like one is no proxied address.
    mapped = "map $a $b { proxy_pass http://a.example; }\n"

    def hosted(text: str) -> str:
        return nginx.bad_upstream_host(text, "app.example")

    def passed(text: str) -> str:
        return nginx.with_upstream(text, "127.0.0.1:8099")

    for change in (hosted, passed):
        with pytest.raises(Refusal, match="no_place_for_defect"):
            change(mapped)
        assert change(mapped + SITE).startswith(mapped) and change(mapped + SITE) != mapped + SITE
    # The two defects that take a sign away are errors to nginx wherever they fall.
    assert nginx.missing_semicolon(WEBSOCKETS) == WEBSOCKETS.replace("'' close;", "'' close")
    assert nginx.unclosed_block(WEBSOCKETS) == WEBSOCKETS.replace("}\n", "\n")


def test_a_bad_upstream_host_takes_the_place_of_the_first_proxied_host() -> None:
    assert nginx.bad_upstream_host(SITE, "app.example") == SITE.replace(
        "proxy_pass http://127.0.0.1:8000/app;", "proxy_pass http://app.example:8000/app;"
    )
    only_upstream = SITE.replace("        proxy_pass http://127.0.0.1:8000/app;\n", "").replace(
        "        proxy_pass http://backend;\n", "        uwsgi_pass backend;\n"
    )
    assert nginx.bad_upstream_host(only_upstream, "app.example") == only_upstream.replace(
        "server 127.0.0.1:8000 weight=5;", "server app.example:8000 weight=5;"
    )
    # A server of no upstream block, a socket and a variable are no place for it.
    for none in (
        "server { listen 80; }",
        "location / { proxy_pass http://unix:/run/app.sock; }",
        "location / { proxy_pass $target; }",
        "upstream b { server unix:/run/app.sock; }",
        "upstream b { server unix:80; }",
        "location / { proxy_pass http://unix:80; }",
        "# proxy_pass http://a;",
    ):
        with pytest.raises(Refusal, match="no_place_for_defect"):
            nginx.bad_upstream_host(none, "app.example")


def test_an_upstream_takes_the_place_of_the_first_proxied_host_and_port() -> None:
    assert nginx.with_upstream(SITE, "127.0.0.1:8099") == SITE.replace(
        "proxy_pass http://127.0.0.1:8000/app;", "proxy_pass http://127.0.0.1:8099/app;"
    )
    assert nginx.with_upstream("proxy_pass https://app;", "h.example:1") == (
        "proxy_pass https://h.example:1;"
    )
    for none in (
        "",
        "proxy_pass $target;",
        "proxy_pass http://unix:/run/s;",
        "# proxy_pass http://a;",
    ):
        with pytest.raises(Refusal, match="no_place_for_defect"):
            nginx.with_upstream(none, "127.0.0.1:8099")


@pytest.mark.parametrize(
    ("defect", "more", "changed"),
    [
        ("missing_semicolon", {}, nginx.missing_semicolon(SITE)),
        ("unclosed_block", {}, nginx.unclosed_block(SITE)),
        ("unknown_directive", {}, nginx.unknown_directive(SITE)),
        (
            "bad_upstream_host",
            {"host": "app.example"},
            nginx.bad_upstream_host(SITE, "app.example"),
        ),
    ],
)
def test_a_defect_is_written_into_the_file_which_keeps_its_owner_and_mode(
    defect: str, more: dict[str, Any], changed: str
) -> None:
    fake, targets = lab()
    within = inside(fake, "web")
    primitive, params = NginxCorruptConf(), corrupt(defect, **more)
    held = state(fake, "web")
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert state(fake, "web") == held
    assert changed != SITE
    assert primitive.apply(targets, params) == {"bytes": len(changed.encode())}
    written = within.nodes[PATH]
    assert (written.content, written.mode) == (changed.encode(), 0o640)
    assert within.executions == [] and set(within.nodes) == set(held[0])
    assert primitive.side_effects(params) == Effects(files=(PATH,))
    # No word of the library's is in the file.
    assert b"potka" not in written.content and b"invalid" not in written.content


def test_a_file_with_no_place_for_the_defect_or_no_file_is_refused() -> None:
    fake, targets = lab("# nothing here\n")
    within = inside(fake, "web")
    primitive = NginxCorruptConf()
    for defect in ("missing_semicolon", "unclosed_block", "unknown_directive"):
        with pytest.raises(SetupRefused) as placeless:
            primitive.ready(targets, corrupt(defect))
        refused(placeless.value, "no_place_for_defect")
        with pytest.raises(FaultFailed) as late:
            primitive.apply(targets, corrupt(defect))
        refused(late.value, "no_place_for_defect")
    assert within.nodes[PATH].content == b"# nothing here\n"
    with pytest.raises(SetupRefused) as absent:
        primitive.ready(targets, corrupt("unclosed_block", "/etc/nginx/none.conf"))
    refused(absent.value, "file_not_found")
    within.link("/etc/nginx/sites-enabled", "sites-available")
    within.link("/etc/nginx/link.conf", "sites-available/default")
    with pytest.raises(SetupRefused) as link:
        primitive.ready(targets, corrupt("unclosed_block", "/etc/nginx/link.conf"))
    refused(link.value, "not_a_regular_file")
    within.write("/etc/nginx/binary.conf", b"\xff\xfe")
    with pytest.raises(SetupRefused) as binary:
        primitive.ready(targets, corrupt("unclosed_block", "/etc/nginx/binary.conf"))
    refused(binary.value, "unexpected_output")


@pytest.mark.parametrize(
    "wrong",
    [
        {"defect": "missing_brace"},
        {"defect": "bad_upstream_host"},
        {"defect": "unclosed_block", "host": "app.example"},
        {"defect": "bad_upstream_host", "host": "127.0.0.1"},
        {"defect": "bad_upstream_host", "host": "App.example"},
        {"defect": "bad_upstream_host", "host": "-app.example"},
        {"defect": "bad_upstream_host", "host": "app.example;"},
        {"defect": "bad_upstream_host", "host": "a b"},
        {"defect": "bad_upstream_host", "host": "a" * 254},
        {"defect": "bad_upstream_host", "host": "unix"},
        {"path": "/etc/nginx"},
        {"path": "/etc/nginx2/site"},
        {"path": "/etc/passwd"},
        {"path": "/srv/nginx.conf"},
    ],
)
def test_a_defect_is_one_of_four_in_a_file_of_nginx_s(wrong: dict[str, Any]) -> None:
    given = {"node": "web", "path": PATH, "defect": "unclosed_block", **wrong}
    with pytest.raises(ValidationError):
        NginxCorruptConf.parameters(given)


def upstream(value: str, path: str = PATH) -> Any:
    return NginxSetUpstream.parameters({"node": "web", "path": path, "upstream": value})


def test_a_site_s_requests_are_passed_to_another_host_and_port() -> None:
    fake, targets = lab()
    within = inside(fake, "web")
    primitive, params = NginxSetUpstream(), upstream("127.0.0.1:8099")
    held = state(fake, "web")
    primitive.check(targets, params)
    primitive.ready(targets, params)
    assert state(fake, "web") == held
    changed = SITE.replace("http://127.0.0.1:8000/app", "http://127.0.0.1:8099/app")
    assert primitive.apply(targets, params) == {"bytes": len(changed)}
    assert within.nodes[PATH].content == changed.encode() and within.executions == []
    # The same address again changes nothing: the fault is in place.
    with pytest.raises(SetupRefused) as there:
        primitive.ready(targets, params)
    refused(there.value, "already_in_that_state")
    assert primitive.side_effects(params) == Effects(files=(PATH,))
    assert "127.0.0.1:8099" in primitive.describe(params)
    assert "unclosed_block" in NginxCorruptConf().describe(corrupt("unclosed_block"))


@pytest.mark.parametrize(
    "value",
    [
        "127.0.0.1",
        "127.0.0.1:0",
        "127.0.0.1:65536",
        "127.0.0.1:80;",
        "a b:80",
        ":80",
        "-h:80",
        "h:80/path",
        "unix:/run/s",
        "unix:80",
        "h:",
        "h:8o",
        "",
    ],
)
def test_an_upstream_is_a_host_or_an_address_a_colon_and_a_port(value: str) -> None:
    with pytest.raises(ValidationError):
        upstream(value)
    for good in ("127.0.0.1:8099", "app.internal:1", "h:65535"):
        assert upstream(good).upstream == good
