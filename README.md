# GEM — Game Engine Migration

When open-source game projects moved between engine versions, what the
move cost, and what evidence exists that anything was blocked while they
waited.

Godot is the instrument, not the subject. The phenomenon is API
evolution and breaking-change migration: every table carries an
`engine` column and the version-extraction rule sits behind an adapter
interface (`miner/engines.py`), with Unity and Unreal declared alongside
Godot. Only the Godot adapter is instantiated in v0.1.

This repository holds the **miner** and the **v0.1 dataset**. See
`dataset/SCHEMA.md` for the 16 tables and how they join,
`dataset/METRICS.md` for every reported quantity and its definition, and
`dataset/DATASHEET.md` for composition, collection, uses and known
limitations.

## What is in v0.1

| | |
|---|---|
| Frame | 39,553 non-fork GitHub repositories, created before `4.0-stable` |
| Probed for a declared version | 5,988, yielding 4,869 project files |
| Mined commit by commit | 300 repositories |
| Version events | 1,134, of which 131 are major migrations |
| Migration windows | 135, with churn separated by file extension |
| Survival records | 405, under two reference versions rather than one |
| Pre/post trees | 103 repositories, as path + blob manifests |
| Instrument agreement | 200 repositories checked against independent signals |

## Quick start

Nothing here needs credentials or a network to inspect:

```bash
cd miner
make selftest          # adapter fixtures + reproduction check
make check             # provenance and join integrity
```

```bash
# projects still declaring Godot 3, most-starred first
jq -r 'select(.config_version==4) | .repo_id' dataset/v0.1/snapshots.jsonl | sort -u

# 3->4 boundary commits whose subject hides what they did
jq -r 'select(.event_type=="major_migration" and .from_config_version==4)
       | select(.subject_names_engine==false and .subject_names_version==false)
       | "\(.authored_on)  \(.repo_id)  \(.subject)"' dataset/v0.1/version_events.jsonl
```

Tables are JSONL, one record per line, mirrored as CSV under
`dataset/v0.1/csv/`. They join on `repo_id`, then `(repo_id, path)` for
project-file-level tables and `(repo_id, boundary_sha)` for the
migration-cost tables.

## Rebuilding

Requires `python >= 3.11`, `git >= 2.36`, and `gh` (run `gh auth
login`). No pip dependencies — see `miner/requirements.txt`.

```bash
cd miner
make dataset                                    # the whole chain
make dataset SNAPSHOT_BUDGET=44000 HISTORY_BUDGET=500
make help                                       # individual stages
```

Stages run in order: `releases` (the clock lag is measured against),
`frame`, `snapshot`, `tiers`, `validate`, `history`, `demand`, `derive`,
`check`.

**Reproducing a published release exactly.** The derived tables are pure
functions of the measured tables plus one parameter: the observation
date at which unmigrated projects are right-censored. Without it pinned,
the 314 right-censored rows move every day.

```bash
make derive OBSERVED_ON=2026-08-26      # the generated_on of v0.1
```

`make selftest` checks this against whatever `generated_on` the release
in the tree carries.

## Provenance

Every row cites the content-addressed artifact it was computed from, in
`ev_id` or `ev_ids`. `miner/check.py` fails the build on a row without
one. `evidence/ledger.jsonl` carries the URL, SHA-256, byte count and
collection date of all 1,047 artifacts, and `evidence/claims.jsonl` every
claim bound to them.

**The stored bodies are not in this repository.** Thirty-three of them
are copies of published papers that we have no right to redistribute, so
`evidence/raw/` is excluded. The hashes are what matter: any artifact can
be re-fetched from its recorded URL and verified against its recorded
digest.

## Caveats that travel with the data

- **GitHub search counts are approximate**, include forks, and drift
  between runs. Where the frame was built from them, restate that.
- **A declared version is a claim about what last wrote the file**, not a
  measurement of the engine in use. `dataset/v0.1/validation.jsonl`
  reports the agreement rate against independent signals; discount
  accordingly.
- **Strata and demand signals are rule-assigned and single-coded.** Each
  row carries the rule that fired, so labels are auditable, but they are
  not adjudicated ground truth.
- **The 300-repository history tier is purposive**, chosen to maximise
  observable events, with the Godot 3 cohort taken in star order.
  Proportions over that tier estimate nothing about the frame.

## Licence and citation

The miner is Apache-2.0 (`miner/LICENSE`). The dataset is CC-BY-4.0
(`dataset/LICENSE`). File content is redistributed only where the source
repository's licence permits; rows with `license: null` are excluded from
any content redistribution.

Two DOIs, because a Zenodo record carries one type and one licence.
Cite the **version** DOIs below rather than the concept DOI: they are
stable, and each resolves to an artifact with the right type and licence.

- **Dataset** (Dataset, CC-BY-4.0):
  [10.5281/zenodo.22264777](https://doi.org/10.5281/zenodo.22264777)
- **Miner** (Software, Apache-2.0), release `v0.1.1`:
  [10.5281/zenodo.22264638](https://doi.org/10.5281/zenodo.22264638)

Both records share the concept DOI `10.5281/zenodo.22264307`, which
therefore resolves to whichever was deposited most recently rather than
to one of the two consistently. Do not cite it.

See `CITATION.cff` for structured metadata.

> **Note on the `v0.1` release.** Its Zenodo archive
> (`10.5281/zenodo.22264308`) is incomplete: the two largest tables were
> tracked in Git LFS at the time, and the GitHub source zip Zenodo
> archives does not resolve LFS objects, so the 39,553-row corpus frame
> was deposited as a 133-byte pointer. Use `v0.1.1` or later, where every
> table is a plain git object. The dataset itself is unchanged between
> the two: same 16 tables, same `generated_on` of 2026-08-26.
