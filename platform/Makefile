# The Pokus Platform's development targets; CI calls the same ones.
# INFRA names a checkout of pokus-infrastructure at the commit of INFRA_PIN
# (the contract, build and generated checks and make golden-images need it).
# CATALOG names a checkout of a catalog, which the catalog-* targets lint and
# check by this repository's rules; a relative path is taken from this
# directory. ROOT_COMMIT is that repository's root commit: its author is the
# author and, a merge made on github.com aside, the committer that every
# commit of the catalog must have. privacy, references and commits check
# this repository only: given either variable they refuse to run, so that a
# catalog- target written without its prefix cannot pass in a catalog's place.
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
CATALOG ?=
ROOT_COMMIT ?=
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
NEED_CATALOG = @test -n "$(CATALOG)" || { echo "$@: CATALOG=<path of the catalog checkout>"; exit 2; }
# $(call OWN_CHECK,<check>): a check of this repository, or the refusal when a
# catalog is given. One recipe line, so that nothing runs after a refusal
# (make -i included); make only tests the variables and never writes them
# into the recipe.
OWN_CHECK = $(if $(CATALOG)$(ROOT_COMMIT),@echo "$@: checks this repository and takes no CATALOG or ROOT_COMMIT; catalog-$@ checks a catalog"; exit 2,$(CHECK) $(1))

.PHONY: help
help: ## List the targets
	@grep -E '^[a-z][a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-19s %s\n", $$1, $$2}'

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
test: ## The tests of the packages and of the repository checks (INFRA=<path> for the database tests)
	$(if $(INFRA),POTKA_INFRA="$(abspath $(INFRA))") $(RUN) pytest -q -p no:cacheprovider

.PHONY: schemas
schemas: ## Write the JSON Schemas of the catalog's files (schemas/catalog/) from their models
	$(CHECK) generated --write schemas

.PHONY: golden-images
golden-images: ## Write infra-data/golden-images.json from pokus-infrastructure's blueprints at INFRA_PIN (INFRA=<path>)
	$(NEED_INFRA)
	$(CHECK) generated --write golden-images --infra "$(INFRA)"

.PHONY: generated
generated: ## The committed generated files match a fresh generation (INFRA=<path>)
	$(NEED_INFRA)
	$(CHECK) generated --infra "$(INFRA)"

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
	$(call OWN_CHECK,privacy)

.PHONY: references
references: ## No reference outside the product's own terms
	$(call OWN_CHECK,references)

.PHONY: commits
commits: ## Every commit of the range is the maintainer's
	$(call OWN_CHECK,commits)

.PHONY: catalog-lint
catalog-lint: ## potka catalog lint on a catalog checkout (CATALOG=<path>)
	$(NEED_CATALOG)
	$(RUN) potka catalog lint "$(CATALOG)"

.PHONY: catalog-privacy
catalog-privacy: ## The privacy scan of a catalog checkout (CATALOG=<path>)
	$(NEED_CATALOG)
	$(CHECK) privacy --root "$(CATALOG)"

.PHONY: catalog-references
catalog-references: ## The references check of a catalog checkout (CATALOG=<path>)
	$(NEED_CATALOG)
	$(CHECK) references --root "$(CATALOG)"

.PHONY: catalog-commits
catalog-commits: ## The commit check of a catalog checkout (CATALOG=<path> ROOT_COMMIT=<its root commit>)
	$(NEED_CATALOG)
	@test -n "$(ROOT_COMMIT)" || { echo "$@: ROOT_COMMIT=<the root commit of the catalog's repository>"; exit 2; }
	$(CHECK) commits --root "$(CATALOG)" --root-commit "$(ROOT_COMMIT)"

.PHONY: verify-todos
verify-todos: ## List the open verification markers; fails while any remain (the gate warns)
	$(CHECK) verify-todos

.PHONY: gate
gate: ## Every check once, with one summary line (INFRA=<path>, VERBOSE=1)
	@$(CHECK) gate $(if $(INFRA),--infra "$(INFRA)") --uv "$(UV)" --python "$(BUILD_PYTHON)" $(if $(VERBOSE),--verbose)
