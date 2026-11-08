# pokus-platform

> **Status: skeleton.** No version has been released and nothing runs yet.
> The repository holds the platform's manifest with its services and
> sockets, service entry points that only report that they are not
> implemented, hooks that report the release as not healthy, and the checks
> of its CI. Do not deploy it.

The Pokus Platform (`potka`) is the research service layer of the Pokus
project. A researcher submits a campaign: a model and an agent
configuration run against many reproducible fault scenarios. The platform
provisions an isolated target for each run, injects the fault, lets the
agent investigate and act only through policy-checked tools, verifies the
outcome independently, and seals a signed, immutable record of the run.
Ground truth is joined only afterwards, when the runs are evaluated.

It runs on a lab host that pokus-infrastructure provisions, and it reaches
the host only through that project's platform contract (contract v2).

## Repository layout

```text
pokus-platform/
├── INFRA_PIN                 # the pokus-infrastructure commit whose contract this targets
├── ci/                       # the repository's own checks (not part of the release)
├── .github/workflows/        # CI
└── platform/                 # the release: what a lab host deploys
    ├── platform-manifest.yaml
    ├── pyproject.toml        # the uv workspace
    ├── uv.lock
    ├── Makefile
    ├── packages/             # one package per service
    └── tests/
```

## The infrastructure contract

`INFRA_PIN` names one commit of pokus-infrastructure. The platform targets
the contract published there: the manifest schema, the privilege profiles,
the validator and the build. CI checks that the commit is on that
project's `main`, that `platform/platform-manifest.yaml` is valid against
the schema there, that the project's own validator accepts the release, and
that its two-phase build (locked wheels first, then an offline build) builds
the release with the flags of that commit.

## Development

Requirements: Linux, git, GNU make, uv 0.12.23 or a later 0.12 release, and
Python 3.12 (uv installs it if it is missing).

```bash
make -C platform setup
make -C platform gate
```

`make -C platform gate` runs every check once and prints one line with the
counts of passed, failed, skipped and warnings, and the complete output of
every failing step (`VERBOSE=1` prints all of it). The contract and build
checks need a checkout of pokus-infrastructure at `INFRA_PIN`:

```bash
make -C platform gate INFRA=<path to the pokus-infrastructure checkout>
```

`<path to the pokus-infrastructure checkout>`: the absolute path of a clone
of pokus-infrastructure with the commit of `INFRA_PIN` checked out (`make -C`
runs in `platform/`, so a relative path is taken from there). Without
`INFRA`, those checks are reported as skipped.

## Privacy

Nothing in this repository identifies a person or a particular
installation: no real names, accounts, e-mail addresses, host names,
addresses or keys, in code, configuration, examples, tests or
documentation. Use placeholders such as `researcher1`, `example-org`,
`example.com` and the documentation address ranges. A privacy scan in CI
enforces it.

## Contributing and security

How to work on the repository: [CONTRIBUTING.md](CONTRIBUTING.md). Report a
vulnerability privately, as [SECURITY.md](SECURITY.md) describes.

## License

Code: Apache License 2.0 (`LICENSE`). Documentation: Creative Commons
Attribution 4.0 International (`LICENSE-docs`). Copyright notice:
[NOTICE](NOTICE).
