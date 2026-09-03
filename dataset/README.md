# GEM — Game Engine Migration dataset

When open-source game projects moved between engine versions, what the
move cost, and what evidence exists that anything was blocked while they
waited.

Godot is the instrument. The phenomenon is API evolution and
breaking-change migration; every table carries an `engine` column and the
version-extraction rule sits behind an adapter interface
(`../miner/engines.py`) with Unity and Unreal sketched alongside Godot.

```
SCHEMA.md      16 tables, every column, and why it is shaped that way
DATASHEET.md   Gebru-style datasheet: motivation, composition, ethics, misuse
v0.1/*.jsonl   the tables
v0.1/summary.json   generated counts -- the single place any number lives
trees/         pre/post tree manifests at each migration boundary
```

## Reading it

Tables are JSONL, one record per line, joined on `repo_id`, then
`(repo_id, path)` for project-file-level tables and
`(repo_id, boundary_sha)` for migration-cost tables. Start with
`SCHEMA.md`'s join graph.

```bash
# projects still declaring Godot 3, most-starred first
jq -r 'select(.config_version==4) | .repo_id' v0.1/snapshots.jsonl | sort -u

# every Godot 3->4 boundary commit whose subject hides what it did
jq -r 'select(.event_type=="major_migration" and .from_config_version==4)
       | select(.subject_names_engine==false and .subject_names_version==false)
       | "\(.authored_on)  \(.repo_id)  \(.subject)"' v0.1/version_events.jsonl
```

## Rebuilding it

```bash
cd ../miner
make dataset                      # the whole chain
make dataset SNAPSHOT_BUDGET=44000 HISTORY_BUDGET=500   # bigger tiers
make selftest                     # adapters + reproduction check, no network
```

or one stage at a time:

```bash
make releases    # engine release timeline -- the clock everything else is measured against
make frame       # corpus frame, bisected under GitHub's 1,000-result cap, then patched
make snapshot    # trees, project files, declared versions, strata, dependencies
make validate    # the agreement study -- run before trusting any version string
make history     # version events, windows, churn, branches, SATD
make demand      # issue-tracker demand and counter-demand
make derive      # lag observations, survival input, summary.json
make derive OBSERVED_ON=2026-08-26   # reproduce a published release exactly
make check       # provenance and join integrity -- must pass before any hand-off
make protocol    # regenerate QUERY_LOG.md from the ledger
make csv         # flatten to dataset/v0.1/csv/
```

Every stage routes its fetches through the evidence ledger
(`../evidence/`), so each row cites the content-addressed artifact and
the exact command that produced it. `make check` fails on a row without
provenance, and batch caches are keyed on both the input and the
miner's source digest, so a code change re-runs rather than serving a
stale measurement.

**Cost.** The frame is metadata only and cheap. The snapshot tier is one
core-API call per repository, which GitHub caps at 5,000 per hour — the
full frame is an overnight run, not a coffee break. The history tier is
one clone per repository and is the tier to keep small.

## Three things to know before using it

**Godot 3.x carries no minor version.** `config_version=4` and nothing
finer. That is the file format, not missing data. Minor-granularity lag
is measurable only for the 4.x era; the 3→4 transition is binary.

**Behind is not unsupported.** `3.6.3-stable` shipped 2026-08-22 — three
years and five months after `4.0-stable`, two months after `4.7-stable`.
A project on Godot 3.6 is on a line upstream is still patching.
`lag_observations.still_supported` keeps that distinction available;
treating lag as decay throws it away.

**Commit messages do not find migrations.** Thrive's 3→4 boundary commit
is titled *"Project file upgrades"*. `godot_heightmap_plugin`'s two major
migrations are *"Update to use global class names"* and *"New
project.godot format"*. The miner reads file content at every commit;
`version_events` carries `subject_names_engine`, `subject_names_version`
and `subject_names_migration` so the miss rate of a message-based study
is a measurement in this dataset rather than an anecdote about it.
