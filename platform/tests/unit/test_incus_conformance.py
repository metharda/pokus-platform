"""The client and FakeIncus together: the calls Incus describes, images that decide nothing."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable
from pathlib import Path

import pytest
from potka_incus import client as incus
from potka_testing.fake_incus import ALIAS, FINGERPRINT, TARGET, client, connection, lab, running
from potka_testing.incus_api import Api
from potka_testing.incus_measure import LINKS_AND_REFUSALS, links_and_refusals, links_tree

SOURCES = Path(__file__).resolve().parents[2] / "packages" / "potka-incus" / "src" / "potka_incus"
# Every module the package imports, the standard library's and httpx's by their first name:
# what a client of an HTTPS API needs, and none that is there to start programs. Of uuid
# the package builds an identifier from a number; what asks the machine for one is refused.
IMPORTED = {
    "__future__",
    "collections",
    "contextlib",
    "dataclasses",
    "httpx",
    "itertools",
    "json",
    "pathlib",
    "posixpath",
    "re",
    "ssl",
    "types",
    "typing",
    "uuid",
}
# Of the platform, the endpoints model and the package's own modules: the in-memory Incus
# and what is inside its instances ship with the client, for the tests of what uses it.
OURS = {
    "potka_core.endpoints",
    "potka_incus.client",
    "potka_incus.inside",
    "potka_incus.units",
}


def test_the_client_makes_every_call_of_the_extract_and_has_no_other() -> None:
    fake = lab()
    bench = client(fake)
    steps: dict[str, Callable[[], object]] = {
        "create": lambda: bench.create("web", fingerprint=FINGERPRINT, profiles=(TARGET,)),
        "start": lambda: bench.start("web"),
        "instances": bench.instances,
        "instance": lambda: bench.instance("web"),
        "state": lambda: bench.state("web"),
        "exec": lambda: bench.exec("web", ["true"]),
        "write_file": lambda: bench.write_file("web", "/a", b"x"),
        "read_file": lambda: bench.read_file("web", "/a"),
        "list_directory": lambda: bench.list_directory("web", "/"),
        "delete_file": lambda: bench.delete_file("web", "/a"),
        "stop": lambda: bench.stop("web"),
        "delete": lambda: bench.delete("web"),
    }
    for name, step in steps.items():
        if name == "exec":
            fake.instance("bench", "web").inside.on(["true"])
        step()
    api = Api()
    made = {api.call(asked.method, asked.path) for asked in fake.asked}
    assert None not in made
    assert made == set(api.calls)
    # Every request named the client's project, except the wait for an operation.
    unscoped = {asked.path.rsplit("/", 1)[-1] for asked in fake.asked if asked.project is None}
    assert unscoped == {"wait"}
    assert {asked.project for asked in fake.asked} == {"bench", None}
    # The client has no public method that was not called here.
    public = {name for name, _ in inspect.getmembers(incus.Incus, callable) if name[0] != "_"}
    assert public == {*steps, "close", "for_service"}


def test_every_creation_names_a_fingerprint_and_an_explicit_profile_list() -> None:
    fake = lab()
    bench = client(fake)
    bench.create("web", fingerprint=FINGERPRINT, profiles=(TARGET,))
    bench.create("db", fingerprint=FINGERPRINT, profiles=("default", TARGET), kind="container")
    assert [(body["source"], body["profiles"]) for body in fake.created()] == [
        ({"type": "image", "fingerprint": FINGERPRINT}, [TARGET]),
        ({"type": "image", "fingerprint": FINGERPRINT}, ["default", TARGET]),
    ]
    # A creation takes exactly these arguments: none that could name an alias, and none
    # that takes whatever it is given.
    parameters = inspect.signature(incus.Incus.create).parameters
    assert list(parameters) == [
        "self",
        "name",
        "fingerprint",
        "profiles",
        "config",
        "devices",
        "kind",
    ]
    kinds = {parameter.kind for parameter in parameters.values()}
    assert kinds == {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
    with pytest.raises(TypeError, match="unexpected keyword argument 'alias'"):
        bench.create("alias", fingerprint=FINGERPRINT, alias=ALIAS, profiles=(TARGET,))  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="missing 1 required keyword-only argument: 'profiles'"):
        bench.create("bare", fingerprint=FINGERPRINT)  # type: ignore[call-arg]
    assert len(fake.created()) == 2


def test_a_rewritten_image_record_changes_no_request_and_no_decision_of_the_client() -> None:
    plain, rewritten = lab(), lab()
    for fake in (plain, rewritten):
        fake.profile("bench", "open", {"security.nesting": "true"})
    # What a client of the project may rewrite of an image's record in Incus and that
    # reaches a creation or an instance (its properties and its profiles), and its aliases:
    # the alias of the golden image now names another image.
    image = rewritten.images[FINGERPRINT]
    image.properties.update({"requirements.nesting": "true", "os": "another", "type": "other"})
    image.profiles[:] = ["open"]
    image.aliases[:] = ["forged/1"]
    rewritten.image("cd" * 32, ALIAS).profiles[:] = ["open"]
    found = []
    for fake in (plain, rewritten):
        bench = client(fake)
        bench.create("web", fingerprint=FINGERPRINT, profiles=(TARGET,))
        found.append(bench.instance("web"))
    assert plain.asked == rewritten.asked
    # What Incus copied from the image's record differs between the two instances...
    copied = [{k: v for k, v in i.config.items() if k.startswith("image.")} for i in found]
    assert copied[0] != copied[1]
    assert copied[1]["image.requirements.nesting"] == "true"
    # ...and nothing else does: nothing that the client or its caller decides by.
    decided = [
        (
            i.name,
            i.status,
            i.kind,
            i.profiles,
            i.base_image,
            i.root_device,
            i.root_size,
            {k: v for k, v in i.expanded_config.items() if not k.startswith("image.")},
            i.devices,
            i.expanded_devices,
        )
        for i in found
    ]
    assert decided[0] == decided[1]
    assert decided[1][2:5] == ("container", (TARGET,), FINGERPRINT)
    assert found[1].expanded_config["security.nesting"] == "false"
    # The rewriting was no empty one: a creation by alias without a profile list, which the
    # client cannot ask for, follows it to another image and another profile.
    with connection(rewritten, "bench") as http:
        body = {"name": "followed", "source": {"type": "image", "alias": ALIAS}}
        assert http.post("/1.0/instances", params={"project": "bench"}, json=body).is_success
    followed = client(rewritten).instance("followed")
    assert (followed.base_image, followed.profiles) == ("cd" * 32, ("open",))
    assert followed.expanded_config["security.nesting"] == "true"
    # One thing a rewritten record can do: Incus reads a requirement of the image when an
    # instance starts, so this one does not start. The caller sees a failed operation.
    client(plain).start("web")
    with pytest.raises(incus.OperationFailed, match="requires nesting"):
        client(rewritten).start("web")
    assert client(rewritten).instance("web").status == "Stopped"


def test_the_calls_measured_on_a_real_incus_end_as_listed_on_the_in_memory_one() -> None:
    fake = lab()
    bench = running(fake)
    inside = fake.instance("bench", "web").inside
    base = "/srv/links"
    # The tree the commands of links_tree make, made here without them.
    assert [argv[:2] for argv in links_tree(base)] == [
        ["mkdir", "-p"],
        ["ln", "-s"],
        ["ln", "-s"],
        ["ln", "-s"],
        ["touch", f"{base}/directory/full/file"],
    ]
    inside.mkdir(f"{base}/directory/full")
    for _, _, target, path in links_tree(base)[1:4]:
        inside.link(path, target)
    inside.write(f"{base}/directory/full/file", b"")
    assert links_and_refusals(bench, "web", base) == LINKS_AND_REFUSALS
    assert len(LINKS_AND_REFUSALS) == 16


def test_the_client_reads_no_property_of_an_image() -> None:
    tree = ast.parse((SOURCES / "client.py").read_text(encoding="utf-8"))
    texts = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert "volatile.base_image" in texts
    # A key of an image's property begins so. (A read built another way is not seen here;
    # the test of a rewritten record compares what the client's own fields give.)
    assert not [text for text in texts if text.startswith("image.")]


def test_the_base_image_read_back_is_the_one_the_instance_was_created_from() -> None:
    fake = lab()
    fake.image("cd" * 32, "another/1.0.0")
    bench = client(fake)
    bench.create("web", fingerprint=FINGERPRINT, profiles=(TARGET,))
    bench.create("db", fingerprint="cd" * 32, profiles=(TARGET,))
    assert (bench.instance("web").base_image, bench.instance("db").base_image) == (
        FINGERPRINT,
        "cd" * 32,
    )
    # An instance whose record names another image than the one asked for is seen as such.
    fake.instance("bench", "web").config["volatile.base_image"] = "cd" * 32
    assert bench.instance("web").base_image != FINGERPRINT
    assert incus.Instance("x", "", 0, "", (), {}, {}, {}, {}).base_image is None


def test_the_package_imports_only_what_is_listed_here() -> None:
    imported: set[str] = set()
    called: set[str] = set()
    sources = sorted(SOURCES.rglob("*.py"))
    assert [source.name for source in sources] == [
        "__init__.py",
        "client.py",
        "fake.py",
        "inside.py",
        "units.py",
    ]
    for source in sources:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, source
                imported.add(node.module or "")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node, ast.Attribute):
                called.add(f".{node.attr}")
    assert {name for name in imported if name.startswith("potka")} == OURS
    assert {name.split(".")[0] for name in imported if not name.startswith("potka")} == IMPORTED
    # Nothing is imported or run by a name that only exists at run time.
    assert not called & {"__import__", "eval", "exec", "compile"}
    # Nor does anything ask the machine for an identifier, which can start a program.
    assert ".UUID" in called
    assert not called & {".uuid1", ".getnode", ".uuid4"}
