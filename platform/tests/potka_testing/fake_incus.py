"""An in-memory lab for the tests: FakeIncus with the lab's profiles and one golden image.

The client and the plain connection a test gets here talk to FakeIncus
through the comparison with the Incus API extract
(``potka_testing.incus_api``): a request sent or an answer the fake gives in
a form Incus does not have fails the test.
"""

from __future__ import annotations

import httpx
from potka_incus.client import Incus
from potka_incus.fake import URL, FakeIncus

from potka_testing.incus_api import checked

FINGERPRINT = "ab" * 32
ALIAS = "web-nginx-flask/1.0.1"
TARGET = "target-container"
ROOT_SIZE = 8 * 1024**3
IMAGE_USED = 900 * 1024**2
# What Incus copies from the image's record into every instance made from it.
PROPERTIES = {"os": "debian", "description": "a golden image"}
COPIED = {f"image.{key}": value for key, value in PROPERTIES.items()}


def lab(driver: str = "lvm") -> FakeIncus:
    """A FakeIncus as infra sets a lab up: two projects, the target profile, a golden image."""
    fake = FakeIncus(("bench", "build"), driver)
    root = {"type": "disk", "path": "/", "pool": "pinka-targets", "size": "8GiB"}
    for project in fake.projects:
        fake.profile(project, "default")
        fake.profile(
            project, TARGET, {"security.nesting": "false", "limits.memory": "1GiB"}, {"root": root}
        )
    fake.image(FINGERPRINT, ALIAS, used=IMAGE_USED, properties=PROPERTIES)
    return fake


def client(fake: FakeIncus, project: str = "bench", *allowed: str) -> Incus:
    """A client of PROJECT whose every exchange is compared with the extract.

    Its certificate is restricted to PROJECT, or to ALLOWED when given.
    """
    return Incus(URL, project, transport=checked(fake.transport(*(allowed or (project,)))))


def connection(fake: FakeIncus, *allowed: str) -> httpx.Client:
    """A plain connection restricted to ALLOWED, for the requests the client never sends.

    Its exchanges are compared with the extract too.
    """
    return httpx.Client(transport=checked(fake.transport(*allowed)), base_url=URL)


def running(fake: FakeIncus, name: str = "web", project: str = "bench") -> Incus:
    """Create and start an instance from the golden image; the client that did it."""
    incus = client(fake, project)
    incus.create(name, fingerprint=FINGERPRINT, profiles=(TARGET,))
    incus.start(name)
    return incus
