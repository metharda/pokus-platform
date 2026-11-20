"""The command path check: no shell between a tool call and a target.

A command that reaches a target is a list of arguments that Incus executes
as it is. The packages that make, check and carry such commands
(:data:`PACKAGES`) therefore hold nothing that runs a command through a
shell or starts a program on the machine the service runs on:

* no import of a module that starts programs or reads a line as a shell
  does (:data:`MODULES`, asyncio's own among them), and no use of
  ``os.system``, ``os.popen``, the ``os.exec*``, ``os.spawn*`` and
  ``os.posix_spawn*`` families or asyncio's calls that start a program
  (:data:`ASYNC_CALLS`), on the module or on an event loop;
* no call with a ``shell`` keyword;
* no list or tuple that names a shell (:data:`SHELLS`) at any place: a
  shell behind ``timeout`` or ``env`` is a shell. Names of shells kept as
  data are written as a set;
* no string that is made where it stands (a literal, an f-string, a
  ``format`` or a ``%`` of one) and split into words: that is a command
  written as a line.

Every package of the release is in :data:`PACKAGES` or in :data:`OTHERS`;
one that is in neither fails the check, so that a new package is put where
it belongs. Only ``*.py`` files are read.

The check reads the sources' syntax; it runs and imports nothing of them.
It guards against a shell that slips in by accident, not against one that
is hidden on purpose: a name bound to a shell's path, a command put
together piece by piece and a module imported under another name are not
found.
"""

from __future__ import annotations

import ast
from pathlib import Path

from potka_ci.outcome import Outcome, from_findings

PACKAGES = (
    "potka-executor",
    "potka-incus",
    "potka-injector",
    "potka-primitives",
    "potka-tools",
    "potka-verifier",
)
# The packages that neither make nor check nor carry a command.
OTHERS = (
    "potka-agent",
    "potka-cli",
    "potka-core",
    "potka-db",
    "potka-evaluator",
    "potka-gateway",
    "potka-orchestrator",
)
MODULES = ("subprocess", "shlex", "pty", "pexpect", "commands", "asyncio.subprocess")
OS_CALLS = ("system", "popen", "startfile")
OS_FAMILIES = ("exec", "spawn", "posix_spawn")
ASYNC_CALLS = (
    "create_subprocess_exec",
    "create_subprocess_shell",
    "subprocess_exec",
    "subprocess_shell",
)
SHELLS = (
    "ash",
    "bash",
    "busybox",
    "csh",
    "dash",
    "fish",
    "hush",
    "ksh",
    "lksh",
    "mksh",
    "pdksh",
    "posh",
    "rbash",
    "sash",
    "sh",
    "tcsh",
    "yash",
    "zsh",
)


def _imported(node: ast.AST) -> list[str]:
    """The modules an import statement names, each with the modules above it."""
    if isinstance(node, ast.Import):
        named = [alias.name for alias in node.names]
    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        named = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
    else:
        return []
    return [
        ".".join(name.split(".")[:end]) for name in named for end in range(1, name.count(".") + 2)
    ]


def _of_os(name: str) -> bool:
    return name in OS_CALLS or name.startswith(OS_FAMILIES)


def _made_in_place(node: ast.expr) -> bool:
    """Whether NODE is a string made where it stands: a literal or a formatting of one."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _made_in_place(node.left)
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and _made_in_place(node.func.value)
    )


def _attribute(node: ast.Attribute) -> str | None:
    """What the attribute NODE uses that the command path may not, or None."""
    on_module = isinstance(node.value, ast.Name)
    if on_module and node.value.id == "os" and _of_os(node.attr):  # type: ignore[attr-defined]
        return f"uses os.{node.attr}"
    if on_module and node.value.id == "asyncio" and node.attr == "subprocess":  # type: ignore[attr-defined]
        return "uses asyncio.subprocess"
    return f"uses {node.attr}" if node.attr in ASYNC_CALLS else None


def source_findings(source: str) -> list[tuple[int, str]]:
    """What SOURCE holds that the command path may not, each with its line.

    Raises:
        SyntaxError: SOURCE is not Python.
    """
    found: set[tuple[int, str]] = set()
    for node in ast.walk(ast.parse(source)):
        for module in _imported(node):
            if (
                module in MODULES
                or (module.startswith("os.") and _of_os(module[3:]))
                or (module.startswith("asyncio.") and module[8:] in ASYNC_CALLS)
            ):
                found.add((node.lineno, f"imports {module}"))  # type: ignore[attr-defined]
        if isinstance(node, ast.Attribute):
            used = _attribute(node)
            if used is not None:
                found.add((node.lineno, used))
        if isinstance(node, ast.Call):
            if any(keyword.arg == "shell" for keyword in node.keywords):
                found.add((node.lineno, "a call with shell="))
            split = isinstance(node.func, ast.Attribute) and node.func.attr in ("split", "rsplit")
            if split and _made_in_place(node.func.value):  # type: ignore[attr-defined]
                found.add((node.lineno, "a string split into a command"))
        if isinstance(node, ast.List | ast.Tuple):
            for element in node.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    program = element.value.rsplit("/", 1)[-1]
                    if program in SHELLS:
                        found.add((node.lineno, f"a list that names a shell ({program})"))
    return sorted(found)


def run(root: Path) -> Outcome:
    """Check the sources of the command path's packages.

    Args:
        root: The working tree.

    Returns:
        The outcome: failed with one line for each finding
        (``<file>:<line>: <what>``), for a ``.py`` file that is not Python
        or cannot be read, and for a package that is in neither list;
        passed with the number of files read. A package of
        :data:`PACKAGES` that is not there yet is named in the summary.
    """
    packages = root / "platform" / "packages"
    entries = packages.iterdir() if packages.is_dir() else ()
    there = sorted(entry.name for entry in entries if entry.is_dir())
    findings = [
        f"platform/packages/{name}: in neither list of the command path check"
        " (PACKAGES: it makes, checks or carries a command; OTHERS: it does not)"
        for name in there
        if name not in PACKAGES and name not in OTHERS
    ]
    read = 0
    absent = [name for name in PACKAGES if not (packages / name / "src").is_dir()]
    for name in PACKAGES:
        for path in sorted((packages / name / "src").rglob("*.py")):
            shown = path.relative_to(root).as_posix()
            read += 1
            try:
                found = source_findings(path.read_text(encoding="utf-8"))
            except OSError as exc:
                findings.append(f"{shown}: cannot be read ({type(exc).__name__})")
                continue
            except (SyntaxError, UnicodeDecodeError, ValueError):
                findings.append(f"{shown}: not Python the check can read")
                continue
            findings += [f"{shown}:{line}: {what}" for line, what in found]
    summary = (
        f"{read} file(s) of {len(PACKAGES) - len(absent)} package(s) on the command path: no shell"
    )
    if absent:
        summary += f" (not there: {', '.join(absent)})"
    if not read:
        findings.append("no source of the command path's packages was found")
    return from_findings(findings, [], summary)
