# Contributing

The platform is at an early stage and does not accept outside
contributions yet: CI runs for commits pushed to this repository's
branches, not for pull requests from forks, and its commit check accepts
the maintainer's commits only. Bug reports and questions are welcome as
issues.

## The gate

Every change passes the gate before it is committed:

```bash
make -C platform gate INFRA=<path to the pokus-infrastructure checkout>
```

`<path to the pokus-infrastructure checkout>`: the absolute path of a clone
of pokus-infrastructure with the commit of `INFRA_PIN` checked out.

The gate runs the linters (ruff, ruff format, mypy in strict mode,
actionlint), the tests, the contract and build checks against
pokus-infrastructure at `INFRA_PIN`, the privacy scan, the references check
and the commit check, lists the open verification markers, and prints one
summary line. A check is never weakened to make it pass; a skipped step, a
skipped test and a warning are always shown.

## Privacy

Never commit data that identifies a person or a particular installation:
real names, user names, e-mail addresses, host names, domains, IP or MAC
addresses, serial numbers, keys or tokens. Use placeholders: `researcher1`,
`researcher2`; `example-org`; `example.com`, `example.org`, `example.net`;
the documentation address ranges `192.0.2.0/24`, `198.51.100.0/24`,
`203.0.113.0/24` and `2001:db8::/32`; MAC addresses from `00:00:5e:00:53:00`
to `00:00:5e:00:53:ff` (RFC 7042); zeroed serial numbers and UUIDs. Public services,
vendors and projects may be named. The privacy scan in CI checks every file
and every commit message.

## Commits

- One change per commit, with a message that says what the change does.
- Commit messages carry no trailers: no co-author, sign-off, tool or
  session lines (the commit check refuses these).
- Commits are not signed.
- The commit check in CI accepts a commit only when its author and
  committer are the repository's maintainer, the identity of the
  repository's first commit; a merge made on GitHub is accepted with
  GitHub as its committer.
