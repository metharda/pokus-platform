"""The repository's own checks: privacy, references, commits, contract, build, gate.

They run in CI and in ``make gate``. Each check returns an :class:`Outcome`;
the command line (``python -m potka_ci <check>``) prints it and exits 0 when
the check passed (with or without warnings), 1 when it failed and 2 when it
could not run because an input is missing.
"""
