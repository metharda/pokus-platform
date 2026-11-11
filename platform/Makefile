# The Pokus Platform's development targets; CI calls the same ones.
# INFRA names a checkout of pokus-infrastructure at the commit of INFRA_PIN
# (the contract and build checks need it).
#
# The file BUILD_PYTHON at the repository's root holds the one Python
# version of the development environment, the build check, the gate and CI.
# make setup installs it with uv into .python/ (git-ignored); every uv
# command here uses only that installation and never downloads one, so a
# missing installation fails instead of falling back to another Python.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
MAKEFLAGS += --no-builtin-rules

UV ?= uv
INFRA ?=
VERBOSE ?=

ifneq ($(origin BUILD_PYTHON),undefined)
$(error BUILD_PYTHON is read from the file ../BUILD_PYTHON only, not from the $(origin BUILD_PYTHON))
endif
BUILD_PYTHON := $(file < ../BUILD_PYTHON)
ifeq ($(strip $(BUILD_PYTHON)),)
$(error ../BUILD_PYTHON is missing or empty)
endif
export UV_PYTHON_INSTALL_DIR := $(CURDIR)/.python
export UV_MANAGED_PYTHON := 1
export UV_PYTHON_DOWNLOADS := manual

RUN := $(UV) run --locked --all-packages --group contract
CHECK := PYTHONPATH=$(abspath ../ci) $(RUN) python -m potka_ci
CODE := packages tests ../ci
NEED_INFRA = @test -n "$(INFRA)" || { echo "$@: INFRA=<path of the pokus-infrastructure checkout at INFRA_PIN>"; exit 2; }

.PHONY: help
help: ## List the targets
	@grep -E '^[a-z][a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

.PHONY: setup
setup: ## Python (../BUILD_PYTHON) in .python/, then the locked development environment (.venv)
	$(UV) python install --no-bin "$(BUILD_PYTHON)"
	$(UV) sync --locked --all-packages --group contract --python "$(BUILD_PYTHON)"

.PHONY: postgres
postgres: ## PostgreSQL 18.6 (the lab's version) from conda-forge, each file checked by sha256, in .postgres/
	tests/postgres/install.sh

.PHONY: lock
lock: ## Update uv.lock
	$(UV) lock

.PHONY: lint
lint: ## ruff, ruff format, mypy (strict) and actionlint (with ShellCheck)
	$(RUN) ruff check $(CODE)
	$(RUN) ruff format --check $(CODE)
	$(RUN) mypy packages/*/src tests ../ci
	$(RUN) shellcheck $(sort $(wildcard tests/*/*.sh))
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
