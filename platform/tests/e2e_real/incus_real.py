"""The Incus client against a real Incus, on a lab host. Not run by the gate or by CI.

The file's name keeps it out of every collected run: pytest reads it only
when it is named, as ``make e2e-real`` does.

A person runs it: ``make e2e-real`` in ``platform/``, as a user who can
read a service's certificate directory, with these variables:

* ``POTKA_E2E_INCUS_URL``: the address of the API (``[incus] url`` of the
  lab's ``endpoints.toml``);
* ``POTKA_E2E_INCUS_CERTS``: a directory with ``client.crt``, ``client.key``
  and ``server.crt`` (a service's, below ``[incus] cert_dir``);
* ``POTKA_E2E_INCUS_PROJECT``: the project (``bench``);
* ``POTKA_E2E_INCUS_INSTANCE``: the name of a running instance of that
  project that may be written to;
* ``POTKA_E2E_INCUS_FINGERPRINT`` and ``POTKA_E2E_INCUS_PROFILE``, both or
  neither: an image and a profile of the project. With them the last check
  creates, starts, stops and deletes an instance named ``potka-e2e-made``.

In the instance the checks run commands as root. They make the directory
``/var/tmp/potka-e2e``, which must not exist, write only below it, and
remove it at the end. The client removes the records Incus keeps of a
command's output; those of a command that was still running when a check
gave up stay on the Incus host. If the last check fails while Incus is
still creating its instance, the instance can appear after the check's
clean-up has ended: look for ``potka-e2e-made`` after a failed run.

A check that measures prints what it measured before it asserts anything
(the make target runs with ``-s``): the forms that only a real Incus can
show. What is printed names the instance's image, profiles and storage
pool, can name the address's host, and quotes Incus's refusals and the
differences found; it is for the person who runs the checks. Run the check
of the root disk twice, with an instance on a btrfs pool and with one on an
LVM thin pool. The last two checks compare every request and answer of
their calls with the Incus API extract the other tests rest on, and go on
after a difference to show them all.
"""

from __future__ import annotations

import os
import socket
import ssl
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from potka_incus import client as incus
from potka_testing.incus_api import checked
from potka_testing.incus_measure import (
    LINKS_AND_REFUSALS,
    links_and_refusals,
    links_tree,
    measured,
)

SCRATCH = "/var/tmp/potka-e2e"  # noqa: S108 (a directory inside the instance)
MADE = "potka-e2e-made"
VARIABLES = ("URL", "CERTS", "PROJECT", "INSTANCE")
MEBIBYTE = 1024**2
ALLOCATED = 64 * MEBIBYTE
MARGIN = 4 * MEBIBYTE
WAIT_SECONDS = 60


def variable(name: str) -> str:
    return os.environ.get(f"POTKA_E2E_INCUS_{name}", "")


@pytest.fixture(scope="module")
def target() -> Iterator[tuple[incus.Incus, str]]:
    """The client and the instance's name, from the environment; the scratch directory made."""
    missing = [f"POTKA_E2E_INCUS_{name}" for name in VARIABLES if not variable(name)]
    if missing:
        pytest.fail(f"set {', '.join(missing)} (see this file's first lines)")
    certificate = incus.Certificate.of(Path(variable("CERTS")))
    with incus.Incus(variable("URL"), variable("PROJECT"), certificate=certificate) as client:
        name = variable("INSTANCE")
        made = client.exec(name, ["mkdir", SCRATCH])
        assert made.exit_code == 0, f"{SCRATCH} must not exist in the instance: {made.stderr!r}"
        yield client, name
        removed = client.exec(name, ["rm", "-rf", SCRATCH])
        assert removed.exit_code == 0, f"{SCRATCH} was not removed: {removed.stderr!r}"


@contextmanager
def compared() -> Iterator[incus.Incus]:
    """A client whose every request and answer is compared with the Incus API extract."""
    certificate = incus.Certificate.of(Path(variable("CERTS")))
    real = httpx.HTTPTransport(verify=certificate.context(), trust_env=False)
    with real, incus.Incus(variable("URL"), variable("PROJECT"), transport=checked(real)) as client:
        yield client


def attempted(what: str, call: Callable[[], object], differences: list[str]) -> object:
    """CALL's result, or None with a line added to DIFFERENCES.

    A difference from the API extract and a refusal are both kept, so that
    the calls after it are still made.
    """
    try:
        return call()
    except AssertionError as difference:
        differences.append(f"{what}: {difference}")
    except incus.IncusError as exc:
        differences.append(f"{what}: {type(exc).__name__}: {exc}")
    return None


def test_the_instance_is_reached_with_the_pinned_certificate(
    target: tuple[incus.Incus, str],
) -> None:
    client, name = target
    found = client.instance(name)
    device = found.root_device
    pool = found.expanded_devices[device].get("pool") if device else None
    print(f"\nprofiles {found.profiles}; base image {found.base_image}; kind {found.kind}")
    print(f"root device {device!r} of pool {pool!r}; configured size {found.root_size}")
    address = urlsplit(variable("URL"))
    # The client's own settings, with the one difference that the host name is compared.
    named = incus.Certificate.of(Path(variable("CERTS"))).context()
    named.check_hostname = True
    try:
        with socket.create_connection((address.hostname, address.port or 443), timeout=10) as raw:
            named.wrap_socket(raw, server_hostname=address.hostname).close()
        names = "also verifies"
    except ssl.SSLCertVerificationError as exc:
        names = f"does not verify ({exc.verify_message})"
    except OSError as exc:
        names = f"was not measured ({type(exc).__name__}: {exc})"
    print(f"the server certificate {names} with the address's host name compared")
    status, listed = found.status, name in client.instances()
    assert (status, listed) == ("Running", True)


def test_a_command_s_arguments_status_and_streams(target: tuple[incus.Incus, str]) -> None:
    client, name = target
    odd = "a b;c $(d) 'e' \"f\""
    assert client.exec(name, ["printf", "%s", odd]) == incus.Result(0, odd.encode(), b"")
    failed = client.exec(name, ["ls", "/no-such-directory-of-potka"])
    assert failed.exit_code != 0
    assert failed.stdout == b""
    assert failed.stderr
    assert client.exec(name, ["pwd"], cwd="/var").stdout == b"/var\n"
    assert (
        client.exec(name, ["printenv", "POTKA_E2E"], environment={"POTKA_E2E": "x"}).stdout
        == b"x\n"
    )
    assert client.exec(name, ["id", "-u"], user=65534, group=65534).stdout == b"65534\n"


def test_a_program_that_does_not_exist_or_cannot_be_run(target: tuple[incus.Incus, str]) -> None:
    client, name = target
    print()
    missing = measured(
        "a program that does not exist", lambda: client.exec(name, ["no-such-program-of-potka"])
    )
    # A file of the check's own that is no program: its mode has no bit to execute.
    plain = f"{SCRATCH}/plain"
    client.write_file(name, plain, b"no program\n", mode=0o644)
    unfit = measured("a program that cannot be run", lambda: client.exec(name, [plain]))
    client.delete_file(name, plain)
    assert missing.startswith("gave Result(exit_code=127,")
    assert unfit.startswith("gave Result(exit_code=126,")


def test_a_file_s_round_trip_with_owner_and_mode(target: tuple[incus.Incus, str]) -> None:
    client, name = target
    path = f"{SCRATCH}/file"
    client.write_file(name, path, b"\x00one\n", uid=65534, gid=65534, mode=0o640)
    client.write_file(name, path, b"two\n", append=True)
    appended = client.read_file(name, path)
    client.write_file(name, path, b"three\n", uid=0, gid=0, mode=0o600)
    replaced = client.read_file(name, path)
    listed = client.list_directory(name, SCRATCH)
    print(f"\nwritten, then appended to: {appended}")
    print(f"then written whole with another owner and mode: {replaced}; listed: {listed}")
    client.delete_file(name, path)
    assert appended == incus.File(b"\x00one\ntwo\n", 65534, 65534, 0o640)
    # A file that was there keeps its owner and its mode.
    assert replaced == incus.File(b"three\n", 65534, 65534, 0o640)
    assert listed == ("file",)
    with pytest.raises(incus.NotFound):
        client.read_file(name, path)


def test_what_incus_does_with_links_and_what_it_refuses(target: tuple[incus.Incus, str]) -> None:
    client, name = target
    base = f"{SCRATCH}/links"
    for argv in links_tree(base):
        made = client.exec(name, argv)
        assert made.exit_code == 0, made.stderr
    print()
    # The calls print each end themselves. The list is how they end on the in-memory Incus
    # of the other tests (a link read or listed is the client's own refusal of what Incus
    # answered: it has no status).
    assert links_and_refusals(client, name, base) == LINKS_AND_REFUSALS


def test_the_root_disk_s_configured_size_and_its_use(target: tuple[incus.Incus, str]) -> None:
    client, name = target
    found = client.instance(name)
    device, size = found.root_device, found.root_size
    before = client.state(name).disk
    kind = (
        client.exec(name, ["findmnt", "--noheadings", "--output", "FSTYPE", "/"])
        .stdout.decode()
        .strip()
    )
    pool = found.expanded_devices[device].get("pool") if device else None
    print(f"\nroot file system {kind}; root device {device!r} of pool {pool!r}")
    print(f"configured size {size}; state before: {before}")
    assert device is not None, "the instance has no root disk device"
    assert size is not None, "the instance's root device has no size"
    assert device in before, "the state has no disk under the root device's name"
    path = f"{SCRATCH}/allocated"
    made = client.exec(name, ["fallocate", "--length", str(ALLOCATED), path])
    assert made.exit_code == 0, made.stderr

    def follows(wanted: int, more: bool) -> float:
        started = time.monotonic()
        while time.monotonic() - started < WAIT_SECONDS:
            usage = client.state(name).disk[device].usage
            if (usage >= wanted) == more:
                return time.monotonic() - started
            time.sleep(1)
        aim = "reach" if more else "fall below"
        pytest.fail(
            f"the state's usage did not {aim} {wanted} within {WAIT_SECONDS} s (last: {usage}); "
            "something else may be writing to the instance's disk"
        )

    grown = follows(before[device].usage + ALLOCATED - MARGIN, True)
    during = client.state(name).disk[device]
    print(f"after allocating {ALLOCATED} bytes: {during}, seen after {grown:.1f} s")
    print(
        f"usage as a part of the configured size: {during.usage / size:.4f}; total {during.total}"
    )
    client.delete_file(name, path)
    fallen = follows(before[device].usage + MARGIN, False)
    print(f"after deleting them: {client.state(name).disk[device]}, seen after {fallen:.1f} s")


def test_a_real_incus_answers_in_the_form_of_its_description(
    target: tuple[incus.Incus, str],
) -> None:
    _, name = target
    path = f"{SCRATCH}/compared"
    differences: list[str] = []
    with compared() as client:
        # Every call is made, whatever the one before it showed; what was found is printed
        # together.
        for what, call in (
            ("instances", client.instances),
            ("instance", lambda: client.instance(name)),
            ("state", lambda: client.state(name)),
            ("exec", lambda: client.exec(name, ["true"])),
            ("write_file", lambda: client.write_file(name, path, b"x")),
            ("read_file", lambda: client.read_file(name, path)),
            ("list_directory", lambda: client.list_directory(name, SCRATCH)),
            ("delete_file", lambda: client.delete_file(name, path)),
        ):
            attempted(what, call, differences)
        try:
            client.instance("no-such-instance-of-potka")
            differences.append("a refusal: an instance that does not exist was found")
        except incus.NotFound:
            pass
        except AssertionError as difference:
            differences.append(f"a refusal: {difference}")
    print(f"\ndifferences from the API extract in a round of calls: {differences or 'none'}")
    assert not differences


def test_an_instance_s_life_in_the_form_of_incus_s_description(
    target: tuple[incus.Incus, str],
) -> None:
    plain, _ = target
    fingerprint, profile = variable("FINGERPRINT"), variable("PROFILE")
    if not (fingerprint and profile):
        pytest.skip("no image and profile to create an instance from (see the first lines)")
    assert MADE not in plain.instances(), f"an instance named {MADE} exists"
    print()
    differences: list[str] = []
    try:
        with compared() as client:
            # Each step is made whatever the one before it showed.
            attempted(
                "create",
                lambda: client.create(MADE, fingerprint=fingerprint, profiles=(profile,)),
                differences,
            )
            made = attempted("instance", lambda: client.instance(MADE), differences)
            if isinstance(made, incus.Instance):
                copied = sorted(key for key in made.config if key.startswith("image."))
                print(f"created: {made.status}; profiles {made.profiles}; kind {made.kind}")
                print(f"its base image is the one asked for: {made.base_image == fingerprint}")
                print(f"keys copied from the image's record: {copied}")
            attempted("start", lambda: client.start(MADE), differences)
            print(f"started: {attempted('state', lambda: client.state(MADE), differences)}")
            measured("starting it again", lambda: client.start(MADE))
            measured("deleting it while it runs", lambda: client.delete(MADE))
            attempted("stop", lambda: client.stop(MADE, force=True), differences)
            print(f"stopped: {attempted('state', lambda: client.state(MADE), differences)}")
            measured("stopping it again", lambda: client.stop(MADE, force=True))
            attempted("delete", lambda: client.delete(MADE), differences)
    finally:
        # Whatever happened above, nothing made here stays: Incus may still be at work on
        # the instance, so the removal is tried until it has been gone for some seconds.
        quiet, until = 0, time.monotonic() + WAIT_SECONDS
        while quiet < 10 and time.monotonic() < until:
            if MADE in plain.instances():
                quiet = 0
                measured(
                    "cleaning up: stopping what was made", lambda: plain.stop(MADE, force=True)
                )
                measured("cleaning up: deleting what was made", lambda: plain.delete(MADE))
            else:
                quiet += 1
            time.sleep(1)
    print(f"differences from the API extract in an instance's life: {differences or 'none'}")
    assert MADE not in plain.instances(), f"{MADE} is still there: remove it by hand"
    assert not differences
