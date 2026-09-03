# GEM miner -- dataset build pipeline.
#
# Stages run in order and each is a strict dependency of the next; run
# them individually while developing, or `make dataset` for the chain.
# Every stage routes its fetches through the evidence ledger, so each row
# it writes cites the content-addressed artifact it came from.
#
# Prerequisites: python >= 3.11, git >= 2.36, gh (run `gh auth login`).
# See requirements.txt.

PY := python3
HERE := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

# Tier budgets. The published v0.1 used the defaults below; raise them
# for a larger release.
SNAPSHOT_BUDGET ?= 6000
HISTORY_BUDGET  ?= 300

# Observation date at which unmigrated projects are right-censored.
# Leave empty for today, or pin it to reproduce a published release
# exactly: make derive OBSERVED_ON=2026-08-26
OBSERVED_ON ?=
DERIVE_ARGS := $(if $(OBSERVED_ON),--observed-on $(OBSERVED_ON),)

.PHONY: dataset releases frame snapshot tiers validate history demand \
        derive check csv selftest clean-derived help

help:
	@echo "make dataset      the whole chain, releases -> check"
	@echo "make selftest     adapter fixtures + reproduction check (no network)"
	@echo "make check        provenance and join integrity"
	@echo
	@echo "stages: releases frame snapshot tiers validate history demand derive csv"
	@echo "vars:   SNAPSHOT_BUDGET=$(SNAPSHOT_BUDGET) HISTORY_BUDGET=$(HISTORY_BUDGET)"
	@echo "        OBSERVED_ON=$(if $(OBSERVED_ON),$(OBSERVED_ON),<today>)"

dataset: releases frame snapshot tiers validate history demand derive check

releases:
	$(PY) $(HERE)build_releases.py

frame:
	$(PY) $(HERE)build_frame.py
	$(PY) $(HERE)patch_frame.py

snapshot:
	$(PY) $(HERE)build_snapshot.py --budget $(SNAPSHOT_BUDGET)

tiers:
	$(PY) $(HERE)select_tiers.py --snapshot-budget $(SNAPSHOT_BUDGET) \
	                             --history-budget $(HISTORY_BUDGET)

validate:
	$(PY) $(HERE)fetch_validation.py
	$(PY) $(HERE)build_validation.py

history:
	$(PY) $(HERE)build_history.py

demand:
	$(PY) $(HERE)fetch_demand.py
	$(PY) $(HERE)build_demand.py

derive:
	$(PY) $(HERE)derive.py $(DERIVE_ARGS)

check:
	$(PY) $(HERE)check.py

csv:
	$(PY) $(HERE)export_csv.py

selftest:
	$(PY) $(HERE)selftest.py

# Derived tables only. Safe: they are pure functions of the measured
# tables plus OBSERVED_ON, so `make clean-derived derive` restores them.
clean-derived:
	rm -f ../dataset/v0.1/lag_observations.jsonl \
	      ../dataset/v0.1/migration_windows.jsonl \
	      ../dataset/v0.1/strata.jsonl \
	      ../dataset/v0.1/summary.json
