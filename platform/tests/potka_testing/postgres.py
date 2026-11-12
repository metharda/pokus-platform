"""A PostgreSQL server for the integration tests.

In CI the ``test`` job's service container is the server:
``POTKA_TEST_POSTGRES`` holds a superuser's connection string, and
``POTKA_POSTGRES_REQUIRED`` the major version the lab runs, which the
server must have. Locally a throwaway cluster is started from the server
binaries of ``POTKA_POSTGRES_BINDIR``, else of ``platform/.postgres/env/bin``
(``make postgres``), else of the newest ``/usr/lib/postgresql/*/bin``, and
removed at the end of the session. PostgreSQL refuses to run as root, so
when the tests run as root the cluster runs as ``nobody``, in a directory
it can traverse, from binaries it must be able to reach. Roles other than
the superuser log in with a password (``scram-sha-256``), as in the lab.
The server's binaries and the environment's ``PG*`` variables do not
reach the cluster's settings, and the run's summary names the server the
tests used.
"""

from __future__ import annotations

import os
import pwd
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

PLATFORM = Path(__file__).resolve().parents[2]
LOCAL_BINDIR = PLATFORM / ".postgres" / "env" / "bin"
SYSTEM_BINDIRS = Path("/usr/lib/postgresql")
SUPERUSER = "postgres"
NO_SERVER = (
    "no PostgreSQL server: run make postgres, set POTKA_POSTGRES_BINDIR, "
    "or set POTKA_TEST_POSTGRES to a superuser's connection string"
)
HBA = "local all postgres trust\nlocal all all scram-sha-256\n"
PORT = "5432"
SETTINGS = (
    "-c listen_addresses='' -c unix_socket_directories={socket} -c port=" + PORT + " "
    "-c fsync=off -c full_page_writes=off -c synchronous_commit=off"
)
USED: list[str] = []


@dataclass(frozen=True)
class Server:
    """A running server and how to reach it as its superuser.

    Attributes:
        params: Connection parameters of the superuser (host, port, user,
            password when it needs one).
        major: The server's major version.
    """

    params: Mapping[str, Any] = field(default_factory=dict)
    major: int = 0

    def conninfo(self, dbname: str, user: str | None = None, password: str | None = None) -> str:
        """A connection string for DBNAME, as the superuser or as USER with PASSWORD.

        Args:
            dbname: The database.
            user: A role other than the superuser, or None.
            password: That role's password.

        Returns:
            The connection string.
        """
        params = dict(self.params, dbname=dbname)
        if user is not None:
            params.pop("password", None)
            params["user"] = user
            if password is not None:
                params["password"] = password
        return make_conninfo(**params)

    def connect(
        self, dbname: str = "postgres", user: str | None = None, password: str | None = None
    ) -> psycopg.Connection[tuple[Any, ...]]:
        """An autocommit connection (:meth:`conninfo`).

        Args:
            dbname: The database.
            user: A role other than the superuser, or None.
            password: That role's password.

        Returns:
            The connection.
        """
        return psycopg.connect(self.conninfo(dbname, user, password), autocommit=True)


def server_major(conninfo: str) -> int:
    """The major version of the server behind CONNINFO.

    Args:
        conninfo: A connection string.

    Returns:
        For example 18.
    """
    with psycopg.connect(conninfo) as conn:
        row = conn.execute("SHOW server_version_num").fetchone()
    assert row is not None
    return int(row[0]) // 10000


def find_bindir(environ: Mapping[str, str] | None = None) -> Path | None:
    """The server binaries for a local cluster (the module's docstring).

    Args:
        environ: The environment (default: this process's).

    Returns:
        A directory with ``initdb`` and ``pg_ctl``, or None.
    """
    env = os.environ if environ is None else environ
    if env.get("POTKA_POSTGRES_BINDIR"):
        return Path(env["POTKA_POSTGRES_BINDIR"])
    if (LOCAL_BINDIR / "initdb").is_file():
        return LOCAL_BINDIR
    system = [d / "bin" for d in SYSTEM_BINDIRS.glob("*") if d.name.isdigit()]
    system = [d for d in system if (d / "initdb").is_file() and (d / "pg_ctl").is_file()]
    return max(system, key=lambda d: int(d.parent.name)) if system else None


def check_major(server: Server, required: str | None) -> None:
    """Fail the session when REQUIRED names another major version than SERVER's.

    Args:
        server: The server.
        required: ``POTKA_POSTGRES_REQUIRED``, or None.
    """
    if required and server.major != int(required):
        pytest.fail(f"the server's major version is {server.major}, not {required}")


def reachable_by_others(path: Path) -> Path | None:
    """The first directory on PATH's way that others may not search, if any.

    Args:
        path: A directory.

    Returns:
        That directory, or None when every one on the way may be searched.
    """
    resolved = path.resolve()
    for directory in [*reversed(resolved.parents), resolved]:
        if not directory.stat().st_mode & stat.S_IXOTH:
            return directory
    return None


def _run(argv: Sequence[str], owner: pwd.struct_passwd | None, cwd: Path) -> None:
    kwargs: dict[str, Any] = {}
    if owner is not None:
        kwargs = {"user": owner.pw_uid, "group": owner.pw_gid, "extra_groups": []}
    env = {name: value for name, value in os.environ.items() if not name.startswith("PG")}
    done = subprocess.run(  # noqa: S603 (the server's own binaries, fixed arguments)
        list(argv),
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        **kwargs,
    )
    if done.returncode != 0:
        raise RuntimeError(f"{Path(argv[0]).name} failed: {done.stdout}{done.stderr}")


@contextmanager
def local_cluster(bindir: Path) -> Iterator[Server]:
    """A throwaway cluster from BINDIR, listening only on a Unix socket.

    Args:
        bindir: The server binaries.

    Yields:
        The running server; it is stopped and its directory removed after.
    """
    owner = pwd.getpwnam("nobody") if os.geteuid() == 0 else None
    if owner is not None and (blocked := reachable_by_others(bindir)) is not None:
        raise RuntimeError(f"nobody, who runs the test server, cannot search {blocked}")
    base = Path(tempfile.mkdtemp(prefix="potka-pg-"))
    try:
        base.chmod(0o711)
        data, socket = base / "data", base / "socket"
        for directory in (data, socket):
            directory.mkdir(mode=0o700)
            if owner is not None:
                os.chown(directory, owner.pw_uid, owner.pw_gid)
        initdb = [str(bindir / "initdb"), "-D", str(data), "-U", SUPERUSER, "-E", "UTF8"]
        auth = ["--auth-local=trust", "--auth-host=reject"]
        _run([*initdb, *auth, "--no-locale", "-N"], owner, base)
        hba = data / "pg_hba.conf"
        hba.write_text(HBA, encoding="utf-8")
        if owner is not None:
            os.chown(hba, owner.pw_uid, owner.pw_gid)
        pg_ctl = [str(bindir / "pg_ctl"), "-D", str(data), "-w", "-t", "60"]
        options = SETTINGS.format(socket=socket)
        log = data / "server.log"
        try:
            _run([*pg_ctl, "-l", str(log), "-o", options, "start"], owner, base)
        except RuntimeError as exc:
            text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
            raise RuntimeError(f"{exc}\nserver.log:\n{text}") from None
        try:
            params = {"host": str(socket), "port": PORT, "user": SUPERUSER}
            yield Server(params, server_major(make_conninfo(dbname="postgres", **params)))
        finally:
            _run([*pg_ctl, "-m", "fast", "stop"], owner, base)
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(scope="session")
def postgres_server() -> Iterator[Server]:
    """The tests' PostgreSQL server (the module's docstring)."""
    dsn = os.environ.get("POTKA_TEST_POSTGRES")
    required = os.environ.get("POTKA_POSTGRES_REQUIRED")
    if dsn:
        params = conninfo_to_dict(dsn)
        params.pop("dbname", None)
        server = Server(params, server_major(make_conninfo(dsn, dbname="postgres")))
        check_major(server, required)
        USED.append(f"PostgreSQL {server.major} of POTKA_TEST_POSTGRES")
        yield server
        return
    bindir = find_bindir()
    if bindir is None:
        pytest.skip(NO_SERVER)
    with local_cluster(bindir) as server:
        check_major(server, required)
        USED.append(f"PostgreSQL {server.major}, a throwaway cluster from {bindir}")
        yield server


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Name the server the tests used, also with ``-q``."""
    for line in USED:
        terminalreporter.write_line(f"database tests: {line}")
