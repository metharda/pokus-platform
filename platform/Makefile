# The Pokus Platform's development targets; CI calls the same ones.
# INFRA names a checkout of pokus-infrastructure at the commit of INFRA_PIN
# (the contract and build checks need it). BUILD_PYTHON is the Python the
# build check builds with (CI: 3.12.15).

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS += --no-builtin-rules

UV ?= uv
INFRA ?=
BUILD_PYTHON ?= 3.12
VERBOSE ?=

RUN := $(UV) run --locked --all-packages --group contract
CHECK := PYTHONPATH=$(abspath ../ci) $(RUN) python -m potka_ci
CODE := packages tests ../ci
NEED_INFRA = @test -n "$(INFRA)" || { echo "$@: INFRA=<path of the pokus-infrastructure checkout at INFRA_PIN>"; exit 2; }

.PHONY: help
help: ## List the targets
	@grep -E '^[a-z][a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

.PHONY: setup
setup: ## The locked development environment (.venv), every package and the contract group
	$(UV) sync --locked --all-packages --group contract

.PHONY: lock
lock: ## Update uv.lock
	$(UV) lock

.PHONY: lint
lint: ## ruff, ruff format, mypy (strict) and actionlint (with ShellCheck)
	$(RUN) ruff check $(CODE)
	$(RUN) ruff format --check $(CODE)
	$(RUN) mypy packages/*/src tests ../ci
	$(if $(wildcard ../.github/workflows/ci.yml),$(RUN) actionlint ../.github/workflows/ci.yml,@echo "lint: no workflow yet, actionlint not run")

.PHONY: test
test: ## The tests of the packages and of the repository checks
	$(RUN) pytest -q -p no:cacheprovider

.PHONY: contract
contract: ## The release against pokus-infrastructure's contract at INFRA_PIN (INFRA=<path>)
	$(NEED_INFRA)
	$(CHECK) contract --infra "$(INFRA)"

.PHONY: build
build: ## pokus-infrastructure's build of the release, and the built release (INFRA=<path>)
	$(NEED_INFRA)
	$(CHECK) build --infra "$(INFRA)" --uv "$(UV)" --python "$(BUILD_PYTHON)"

.PHONY: privacy
privacy: ## No personal or installation-specific data (denylist: POTKA_PRIVACY_DENYLIST)
	$(CHECK) privacy

.PHONY: references
references: ## No reference outside the product's own terms
	$(CHECK) references

.PHONY: commits
commits: ## Every commit of the range is the maintainer's
	$(CHECK) commits

.PHONY: verify-todos
verify-todos: ## List the open verification markers; fails while any remain (the gate warns)
	$(CHECK) verify-todos

.PHONY: gate
gate: ## Every check once, with one summary line (INFRA=<path>, VERBOSE=1)
	@$(CHECK) gate $(if $(INFRA),--infra "$(INFRA)") --uv "$(UV)" --python "$(BUILD_PYTHON)" $(if $(VERBOSE),--verbose)
