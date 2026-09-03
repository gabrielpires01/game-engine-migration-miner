# GEM — Game Engine Migration dataset · schema v0.1

16 tables plus `trees/`. Every table is JSONL, one record per line.

**GEM** records how open-source game projects move between engine
versions: when a project declared a new engine version, what the move
cost in the same commit window, what parallel work it ran, and what
evidence exists that anything was blocked while it waited.

Godot is the instrument. Every table carries an `engine` column and the
version-extraction rule lives behind an adapter interface
(`miner/engines.py`), so the schema admits Unity
(`ProjectSettings/ProjectVersion.txt`) and Unreal (`.uproject`
`EngineAssociation`) without change. Only the Godot adapter is
instantiated in v0.1.

---

## 0. Design commitments

**Version strings are claims, not measurements.** A `project.godot`
records what last *wrote* the file. The `validation` table reports the
agreement rate against independent signals (CI pins, export presets,
README, Dockerfiles) so consumers can discount accordingly (caveat C9).

**Migration is a window, not a commit.** The boundary commit is where
the declared version flips; the port work spans commits either side.
`version_events` records the point, `migration_windows` records the
span, and cost measures attach to the window.

**Every table is append-only JSONL, one record per line**, with a
`schema` field naming the table and `v` naming the schema version.
Re-running the miner rewrites a table wholesale from its inputs; no
in-place mutation.

**Provenance is mandatory.** Every row carries `ev_id`, the id of the
stored artifact in `evidence/ledger.jsonl` it was computed from, or
`ev_ids[]` where several. A row with no `ev_id` is a bug, and
`miner/check.py` fails the build on one.

**Derived tables never re-measure.** `lag_observations`, `strata`, and
`migration_windows` are pure functions of the measured tables, of
`releases`, and of one parameter: the observation date at which
unmigrated projects are right-censored. Deleting them and re-deriving
*at the same date* must be a no-op, which `miner/selftest.py` checks
against this release's `generated_on`. Without the date pinned the 314
right-censored rows move every day, because `censor_on`,
`duration_days`, `dormant_days` and `days_since_old_line_release` are
all measured to it: use `make derive OBSERVED_ON=<generated_on>` to
reproduce a published release.

---

## 1. `releases.jsonl` — the engine release timeline

The clock against which lag is measured. Without it, "lag" has no
denominator.

| field | type | notes |
|---|---|---|
| `engine` | str | `godot` |
| `version` | str | normalised, e.g. `4.3`, `4.2.1`, `3.6.3` |
| `tag` | str | upstream tag, e.g. `4.3-stable` |
| `line` | str | `1.x` `2.x` `3.x` `4.x` — the maintenance branch |
| `minor_series` | str | `4.3` for `4.3.1`; the granularity `config/features` records |
| `channel` | str | `stable` `rc` `beta` `alpha` `dev` |
| `released_on` | date | see the dating rule below |
| `date_field` | str | which field supplied `released_on` |
| `date_source` | str | `godotengine/godot` or `godotengine/godot-builds` |
| `is_first_of_minor` | bool | true for `4.3-stable`, false for `4.3.1-stable` |
| `ev_id` | str | |

**Dating rule.** Stable releases take `published_at` from
`godotengine/godot`, which has no backfill. Prereleases take
`created_at` from `godotengine/godot-builds`, whose `published_at` is a
2023-09-12 backfill timestamp for everything older (caveat C12). The
field actually used is recorded per row in `date_field` rather than
left to the reader to infer.

**Why prereleases are in scope.** Godot 4.0 betas shipped
`config_version=5` from 2022 onward, so projects declare Godot 4 more
than six months before `4.0-stable`. A timeline of stables alone dates
those adoptions to the future and silently produces negative lag.

---

## 2. `repos.jsonl` — the corpus frame

One row per candidate repository, **including the excluded ones**. A
frame that keeps only survivors cannot be audited, and reviewers ask how
many were dropped and why.

| field | type | notes |
|---|---|---|
| `repo_id` | str | `owner/name`, the join key everywhere |
| `host` / `url` / `default_branch` | str | |
| `created_at` / `pushed_at` / `updated_at` | date | |
| `stars` / `forks` / `open_issues` / `size_kb` | int | |
| `language` | str | GitHub's primary-language guess |
| `topics` | str[] | |
| `license` | str\|null | SPDX id; null blocks redistribution of file content |
| `is_fork` / `archived` / `disabled` | bool | |
| `description` / `homepage` | str | |
| `discovered_via` | str[] | every query string that returned it |
| `discovered_on` | date | |
| `included` | bool | passed the frame filter |
| `exclusion_reason` | str\|null | `fork` `engine-itself` `no-project-file` `tree-unavailable` |
| `tier` | str | `frame` `snapshot` `history` — how far this repository was actually probed |
| `ev_ids` | str[] | |

**Frame definition.** Non-fork repositories matching
`topic:godot`, `topic:godot-engine`, or `language:GDScript`, created
before `4.0-stable` (2023-03-01) so that a 3→4 decision was available to
them, with at least one `project.godot` anywhere in the default-branch
tree. The created-before cut is what makes the never-migrated cohort
meaningful: a project born in 2024 never faced the choice.

`included` is a frame-level property and is only **refined** for
repositories that were actually probed: `no-project-file` can be
assigned to a repository at the `snapshot` tier or above, never to one
that was left at `frame`. Reading `included=true` as "confirmed to be a
Godot project" is therefore wrong for the frame tier, and `tier` is the
column that tells the two apart.

`discovered_via` is a list because the three queries overlap heavily and
the overlap itself is reportable.

Slices are paged with `sort=created&order=asc`. Creation dates are
immutable, so pages do not shift under the reader; paging on
`sort=updated` lets a repository pushed between two requests cross a page
boundary and vanish from the enumeration (caveat C13). The v0.1 frame was
collected before this was corrected and may under-enumerate slightly for
that reason — noted rather than silently carried.

---

## 3. `project_files.jsonl` — where the version lives

One row per `project.godot` (or engine equivalent) per repository. A
repository is not a project: monorepos of demos carry dozens, and a
root-only probe silently drops them. In the 2026-08-19 pilot, 174 of
373 candidates had no root `project.godot` and were recorded as
unknown — most were multi-project or nested-path repositories, not
non-Godot ones.

| field | type | notes |
|---|---|---|
| `repo_id` | str | |
| `path` | str | repo-relative, e.g. `project.godot`, `demos/2d/platformer/project.godot` |
| `is_root` | bool | |
| `depth` | int | path components |
| `blob_sha` | str | at the snapshot commit |
| `size_bytes` | int | |
| `is_primary` | bool | the repository's main project — root if present, else shallowest, ties broken by largest |
| `ev_id` | str | |

---

## 4. `snapshots.jsonl` — declared version at a point in time

One row per `(project_file, snapshot_date)`. Re-running the miner
appends a new snapshot rather than overwriting, so the corpus becomes a
longitudinal panel rather than a photograph.

| field | type | notes |
|---|---|---|
| `repo_id` / `path` | str | |
| `snapshot_on` | date | |
| `commit_sha` | str | default-branch HEAD at snapshot |
| `config_version` | int\|null | `4`=Godot 3.x, `5`=Godot 4.x |
| `features_raw` | str\|null | the `config/features` value verbatim |
| `declared_minor` | str\|null | `4.3`; null for all of Godot 3.x |
| `renderer` | str\|null | `Forward Plus` `Mobile` `GL Compatibility` |
| `uses_csharp` | bool | `C#` in features, or a `.csproj` in the tree |
| `engine_major` | str | `3` or `4`, derived from `config_version` |
| `parse_status` | str | `ok` `no-version-key` `unparseable` `fetch-failed` |
| `ev_id` | str | |

`declared_minor` being null for the whole 3.x era is the instrument's
hard limit, not missing data. Minor-granularity lag is a 4.x-era
measure; the 3→4 transition is binary. Consumers must not impute.

---

## 4b. `repo_signals.jsonl` — what the tree says about a repository

One row per repository probed at the snapshot tier. These are the inputs
the `strata` rule is adjudicated against and the source of the workflow
paths the agreement study reads, so they are persisted rather than
consumed and discarded.

| field | type | notes |
|---|---|---|
| `repo_id` | str | |
| `tree_sha` / `branch` | str | |
| `branch_fallback` | bool | the frame's `default_branch` 404'd and `HEAD` was used |
| `blob_count` | int | files on the default branch |
| `truncated` | bool | the API truncated the tree; all counts below are partial |
| `project_file_count` | int | |
| `signals` | obj | counts by kind: `gd`, `gd_in_addons`, `scene`, `scene_in_addons`, `resource`, `shader`, `plugin_cfg`, `addons_dir`, `csproj`, `export_presets`, `workflow`, `dockerfile`, `readme`, `test_file` |
| `workflow_paths` | str[] | up to 12 |
| `ev_id` | str | |

`gd_in_addons` beside `gd` is what separates an addon repository from a
game that vendors three plugins. `plugin_cfg` alone cannot: nearly every
game has one.

## 5. `version_events.jsonl` — the core table

One row per observed change in the declared version of one project file.
This is what the miner exists to produce.

| field | type | notes |
|---|---|---|
| `repo_id` / `path` | str | |
| `commit_sha` / `parent_sha` | str | |
| `authored_on` / `committed_on` | date | both, because rebases move one and not the other |
| `from_config_version` / `to_config_version` | int\|null | null on `initial` |
| `from_minor` / `to_minor` | str\|null | |
| `event_type` | str | `initial` `major_migration` `major_rollback` `minor_upgrade` `minor_downgrade` |
| `minor_distance` | int\|null | first-of-minor stable releases advanced, counted against `releases`, e.g. 4.1→4.6 is 5 |
| `minors_skipped` | int\|null | `minor_distance - 1`; a jump straight from 4.1 to 4.6 skips four |
| `to_minor_released_on` | date\|null | when the adopted version shipped |
| `detection` | str | `content-at-commit` |
| `on_default_branch` | bool | reachable from the default branch |
| `subject` | str | first line of the commit message, verbatim |
| `subject_names_engine` | bool | matches `godot\|engine` case-insensitively |
| `subject_names_version` | bool | matches a version-like token |
| `subject_names_migration` | bool | matches `migrat\|upgrad\|port\|convert\|bump` |
| `ev_id` | str | |

**The three `subject_names_*` flags are a result, not bookkeeping.** The
project's motivating anecdote is Thrive's 3→4 boundary commit
`217ef43e`, titled *"Project file upgrades"* — invisible to a
commit-message search. These flags turn that anecdote into a measured
rate: the fraction of real migration events a message-based study would
miss. That rate is the empirical case for the miner's existence, and it
is the one number a Data & Tool Showcase reviewer most needs to see.

There is no separate `minor_skip` type: a jump is an ordinary
`minor_upgrade` carrying `minors_skipped > 0`. A distinct type would
fragment the upgrade counts and force every consumer to remember to add
two categories together.

`major_rollback` and `minor_downgrade` are not error states. Projects do
revert. Recording them separately keeps them out of the migration
counts without discarding them.

**Detection reads content, never messages.** For each commit touching a
project file, the file is read at that commit and at its first parent
and the two declared versions compared. Reading beats diffing: it does
not depend on rename detection, and it cannot be fooled by a pickaxe
regex matching a context line.

---

## 6. `migration_windows.jsonl` — the unit cost attaches to

One row per `major_migration` event, bounding the port work around it.

| field | type | notes |
|---|---|---|
| `repo_id` | str | |
| `boundary_sha` | str | joins `version_events` |
| `window_rule` | str | `branch-merge` `commit-burst` `single-commit` |
| `start_sha` / `start_on` | str/date | |
| `end_sha` / `end_on` | str/date | |
| `commits` | int | in the window |
| `authors` | int | distinct |
| `span_days` | int | |
| `saturated` | str\|null | `span` or `commits` if the burst hit a bound instead of finding an edge |
| `ev_id` | str | |

**Rule precedence.** If the boundary commit is a merge whose second
parent's branch name matches a migration pattern, the window is that
branch (`branch-merge`). Otherwise the window is the maximal run of
commits around the boundary with no gap longer than 14 days
(`commit-burst`). Otherwise the boundary commit alone
(`single-commit`). `window_rule` is stored so a consumer can restrict
to one rule; the three are not equally reliable and must not be pooled
silently.

**A burst must stop on more than a gap.** On a repository with
continuous daily activity there is never a 14-day gap, so a
gap-only rule walks until it runs out of commits and sweeps in months of
unrelated work — on Thrive it produced a 396-commit, 241-day "window"
whose largest churn component was vendored `.hpp` files. The burst
therefore also stops at 60 days and 150 commits, and `saturated` records
which bound it hit so those windows can be excluded rather than pooled.

---

## 7. `churn.jsonl` — the principal

One row per migration window. Cost measured, not assumed.

| field | type | notes |
|---|---|---|
| `repo_id` / `boundary_sha` | str | |
| `scope` | str | `window` or `boundary-commit` — both are emitted |
| `files_changed` / `insertions` / `deletions` | int | |
| `by_ext` | obj | `{".gd": {...}, ".tscn": {...}, ".cs": {...}, ".gdshader": {...}}` |
| `renames` | int | |
| `pre_files` / `pre_bytes` | int | project size before the measured span — the control variable |
| `pre_gd_files` / `pre_scene_files` | int | |
| `ev_id` | str | |

`pre_*` is measured at the parent of whatever the row's `scope` covers:
the boundary commit's parent for `boundary-commit`, the window start's
parent for `window`. On a 150-commit window those are different trees,
and using the boundary's for both would mis-scale exactly the rows where
the control matters most.

`.tscn` and `.tres` churn is separated from `.gd` churn because Godot's
converter rewrites scene and resource files wholesale. Pooling them
would let mechanical resource rewriting masquerade as hand-written
migration effort, which would corrupt B-RQ2's regression outright.

---

## 8. `branches.jsonl` — the interest

One row per branch whose name matches a migration pattern.

| field | type | notes |
|---|---|---|
| `repo_id` / `branch` | str | |
| `pattern` | str | the substring that matched |
| `line` | str | the engine line the name refers to, `3.x` or `4.x` |
| `first_on` / `last_on` | date | |
| `commits` | int | not on the default branch at fork point |
| `lifetime_days` | int | |
| `merged` | bool | reachable from default branch HEAD |
| `merge_sha` | str\|null | |
| `unique_commits` | int\|null | branch commits with no equivalent patch on the default branch (`git cherry` `+`) |
| `duplicated_commits` | int\|null | branch commits whose patch already exists on the default branch (`git cherry` `-`) |
| `behind` | int | commits on the default branch not on this one |
| `ev_id` | str | |

**Both lines are captured.** A repository carrying a `godot_3`
maintenance branch beside a `godot_4` branch is paying to keep two
engine versions alive, and that is exactly the interest B-RQ3 measures.
A pattern that recognised only the new line would see half of it.

Cost of carrying two versions in parallel. `duplicated_commits` is the
direct measure of interest: a fix applied to both the maintenance branch
and the migration branch is work done twice, and `git cherry` identifies
it by patch equivalence rather than by inferring it from branch
lifetime.

`duplicated_commits + unique_commits` does **not** equal `commits`:
`git cherry` skips merge commits while `rev-list --count` counts them.
On `bitwes/Gut`'s `godot_4_7` branch that is 1 + 38 against 46. Report
the pair or the total, never a difference between them.

Caveat: branches deleted after merge leave no trace, so counts are lower
bounds — restate at every point of use.

---

## 9. `satd.jsonl` — debt incurred by repayment

One row per `(boundary_sha, scope, side)`, `side ∈ {pre, post}`. Raw
counts per side rather than a delta, so consumers can normalise by LOC
themselves.

| field | type | notes |
|---|---|---|
| `repo_id` / `boundary_sha` / `side` | str | |
| `scope` | str | `window` where one exists, else `boundary-commit` |
| `tree_sha` | str | |
| `todo` / `fixme` / `hack` / `xxx` | int | in `.gd` and `.cs` |
| `commented_code_lines` | int | comment lines that parse as code |
| `test_files` / `test_cases` | int | |
| `skipped_tests` | int | |
| `loc_gd` / `loc_cs` / `loc_total` | int | the normaliser |
| `files_gd` / `files_cs` / `files_scanned` | int | |
| `files_truncated` | bool | more than 8,000 source files; counts are partial |
| `ev_id` | str | |

**Measured across the window, not the boundary commit.** The commit that
flips `project.godot` is often not the commit that ports the code. On
Thrive, boundary-commit SATD is identical either side — 1,058 TODOs
before and after — because that commit touched scenes and configuration
only. Across the window the same repository shows 1,017 → 1,058, a real
delta of 41. A boundary-only measure would have reported "migration
changes nothing", which is an artefact of the unit, not a finding.

`commented_code_lines` counts comment lines whose content parses as a
statement, deliberately conservatively: a doc comment containing an `=`
should not inflate it. It is a heuristic and must be described as one.

---

## 10. `blocked_demand.jsonl` — lag × demand

The table Paper B's thesis turns on. Lag alone is a version number;
lag with documented blocked demand is debt.

| field | type | notes |
|---|---|---|
| `repo_id` | str | |
| `signal` | str | see the coverage table below |
| `polarity` | str | `demand` or `counter-demand` |
| `url` | str | permalink |
| `observed_on` | date | |
| `snippet` | str | verbatim, ≤300 chars |
| `author_handle` | str | **ledger only — stripped from the released dataset** |
| `coder` | str | `rule` or a coder id |
| `confidence` | str | `high` `medium` `low` |
| `ev_id` | str | |

**Signal coverage in v0.1** — declared and populated is not the same as
declared:

| signal | polarity | v0.1 |
|---|---|---|
| `issue-requests-new-version` | demand | populated |
| `contributor-names-blocker` | demand | populated |
| `migration-branch-exists` | demand | populated, from `branches` |
| `default-branch-is-migration` | demand | populated, from `repo_history` — the migration branch became the mainline, so no branch remains to find |
| `maintainer-declines-migration` | counter-demand | populated |
| `contributor-notes-no-need` | counter-demand | populated |
| `dependency-dropped-support` | demand | **not populated** — needs an addon's own support declaration, which Godot does not record anywhere machine-readable |
| `addon-pins-old-version` | demand | **not populated** — needs the name match in `dependencies` to be adjudicated first |

The two unpopulated signals stay in the enum so that adding them later
does not change the schema, and so a consumer counting signals knows
which absences are real and which are unmeasured.

`maintainer-declines-migration` carries `polarity=counter-demand` and is
as important as the demand signals. Under Paper B's negative thesis, a
maintainer stating that 3.x meets their needs is not a null result —
it is the finding. A schema that could only record demand would make
the thesis untestable by construction.

**Human-subject handling.** Handles and permalinks stay in
`evidence/ledger.jsonl`. The released dataset carries the URL and the
snippet but not the handle, and quoting any individual requires UFRJ CEP
clearance. Aggregate counts do not.

---

## 10b. `dependencies.jsonl` — what a project vendors

One row per vendored addon directory per repository.

| field | type | notes |
|---|---|---|
| `repo_id` | str | |
| `addon_name` | str | the `<name>` in `addons/<name>/` |
| `source` | str | `vendored-tree` |
| `truncated` | bool | more than 40 addons; the list is partial |
| `ev_id` | str | |

The inference-discipline table in `research_questions.md` names "blocked
by its dependencies" as a rival explanation for lag, and killing it
requires a project's lag to be comparable against its addons' lag. Godot
records a dependency only as a directory name under `addons/`, never as a
repository or a version constraint, so matching an addon to a corpus
repository is a **name match** and every claim built on it must say so.
The corpus-level form of the same question — do addon repositories
migrate before game repositories — needs no matching at all and is
answered from `strata` and `lag_observations` directly.

## 11. `strata.jsonl` — what kind of project this is

| field | type | notes |
|---|---|---|
| `repo_id` | str | |
| `stratum` | str | `game` `addon` `tool` `template` `demo` `library` `learning-material` `unknown` |
| `signals` | obj | the rule inputs that fired |
| `method` | str | `rule` `manual` `adjudicated` |
| `coder` | str | |
| `confidence` | float | |
| `ev_id` | str | |

Tooling migrated onto 4.0 betas in 2022; games migrated in 2024. Pooling
them produces a bimodal lag distribution whose mean describes nothing.
The stratum is a first-class variable, and the games-vs-tooling gap is a
finding in its own right rather than a nuisance to control away.

A manually coded subsample with two coders and a reported κ is required
before the rule labels are used in any published figure.

---

## 11b. `repo_history.jsonl` — repository-level facts from the clone

One row per repository mined at the history tier.

| field | type | notes |
|---|---|---|
| `repo_id` | str | |
| `default_branch` / `head_sha` | str | as cloned, which can differ from the frame's value |
| `default_branch_line` | str\|null | `4.x` if the default branch name itself names a version |
| `default_branch_names_version` | str\|null | the matched substring |
| `commits_total` / `commits_default` | int | all refs, and the default branch |
| `authors_total` | int | distinct author emails |
| `first_commit_on` / `last_commit_on` | date | |
| `project_paths` | str[] | every path that was ever a `project.godot` |
| `ev_id` | str | |

`default_branch_line` exists because the migration branch sometimes wins
outright: `godot-aseprite-wizard` renamed its default branch to `godot_4`
and left `master` on the old line. A branch inventory that skips the
default branch records that project as having no migration branch at all.

## 12. `validation.jsonl` — is the declared version the real one?

The agreement study. Run **before** the full mining pass; caveat C9
depends on it.

| field | type | notes |
|---|---|---|
| `repo_id` | str | |
| `declared` | str | from `project.godot` |
| `ci_pin` | str\|null | from workflow YAML |
| `export_preset_hint` | str\|null | |
| `readme_hint` | str\|null | |
| `container_hint` | str\|null | Dockerfile / devcontainer |
| `secondary_count` | int | independent signals found |
| `best_secondary` | str\|null | the version the ranked signals agree on |
| `recovered_minor` | str\|null | **for Godot 3.x only**: the minor version `project.godot` cannot record, recovered from a CI pin or container tag |
| `agreement` | str | `agree` `disagree` `prose-only` `no-secondary-signal` |
| `disagreement_kind` | str\|null | `declared-newer` `declared-older` `different-line` |
| `notes` | str | |
| `ev_ids` | str[] | |

`no-secondary-signal` is reported separately from `agree`. Folding the
two would inflate the agreement rate with projects that simply carry no
CI, which is most small ones.

**A README mention is not an instrument, and this table is what showed
it.** Measured over 200 repositories: the declared version agrees with a
machine-readable CI pin 79% of the time and with a README mention 54%.
The README disagreements are stale prose — `beehave` declares 4.7 and
its README mentions 3.5; `godotengine/tps-demo` declares 4.5 and mentions
3.4 — because a README carries compatibility tables and changelogs, not a
statement about the engine in use. Pooling the two gave 62%, a number
that describes neither population. README hits are therefore recorded as
`readme_hint` and given the verdict `prose-only`, and are excluded from
the agreement denominator.

**This table partially lifts the 3.x limitation.** A Godot 3
`project.godot` records no minor version, but a CI pin does —
`git-learning-game/oh-my-git` declares `config_version=4` and pins
`3.2.3`. `recovered_minor` carries that where it exists. It is available
only for projects with CI, so it is a supplement for a biased subset,
never a substitute: minor-granularity 3.x lag still cannot be computed
corpus-wide.

---

## 13. `lag_observations.jsonl` — derived, the survival input

One row per `(repo, observation)`. Pure function of `snapshots`,
`version_events`, and `releases`.

| field | type | notes |
|---|---|---|
| `repo_id` | str | |
| `stratum` | str | denormalised for convenience |
| `origin_on` | date | when the risk period starts — `4.0-beta1` or repo creation, whichever is later |
| `event_on` | date\|null | the `major_migration` date; null if never |
| `censor_on` | date\|null | the observation date, for the never-migrated |
| `last_activity_on` | date\|null | last commit, carried as a covariate |
| `dormant_days` | int\|null | observation date minus last activity |
| `event` | int | 1 migrated, 0 right-censored |
| `duration_days` | int | the survival time |
| `latest_stable_at_event` | str | what the newest release was then |
| `lag_releases` | int | stable releases behind at event or censoring |
| `still_supported` | bool | was the declared line still being maintained at that moment — a release at or after it, or its last release within 365 days |
| `days_since_old_line_release` | int | age of the most recent stable release of the line the project is on |
| `ev_ids` | str[] | |

**`still_supported` exists because of one measured fact:** `3.6.3-stable`
shipped 2026-08-22, 3 years 5 months after `4.0-stable`. In the v0.1
history tier, **all 157 projects still declaring Godot 3 are on a line
whose most recent stable release was four days before observation.** Not
one is on an abandoned engine. Any lag measure that treats that as decay
is measuring the wrong thing, and this column is what separates
lag-behind-latest from lag-behind-supported.

The support test is windowed, not literal. "Did a release ship at or
after this date" reports every observation in the four days after 3.6.3
as unsupported, inverting the fact the column exists to record; a line
patched last month is maintained. `days_since_old_line_release` carries
the underlying number so a consumer can pick a different window.

**Right-censoring is not optional.** Averaging time-to-migration over
migrators only conditions on the event having happened and reports a
number that is guaranteed too small. The never-migrated cohort is the
substantive population for Paper B, so it is carried, not dropped.

**Censoring is at the observation date, not the last commit.** We
observed each repository today and it had not migrated; stopping its
clock at its last commit would discard every year since and understate
the wait. Abandonment is a competing risk, not a censoring time, so it
is carried as `last_activity_on` / `dormant_days` for an analyst to
filter on or model — a choice the dataset makes visible rather than
baking in.

---

## 14. `trees/` — pre/post captures

Not a table. For every `major_migration` in the migrator subset:

```
trees/<owner>__<name>/<boundary_sha>/pre.manifest.jsonl   path, blob_sha, size, mode
trees/<owner>__<name>/<boundary_sha>/post.manifest.jsonl
trees/<owner>__<name>/<boundary_sha>/pre.tar.zst          full tree (subset only)
trees/<owner>__<name>/<boundary_sha>/post.tar.zst
```

Manifests for every migration; tarballs only where the license permits
redistribution and the tree is under the size cap. The recorded
`(repo_id, sha)` pair makes any tree reconstructible from a
blob-filtered clone, but reconstruction fails for force-pushed or
deleted repositories, which is why the subset is archived.

Paper C consumes `pre.tar.zst` directly: it is the input to
`godot --validate-conversion-3to4`, and `post` is the human ground
truth to diff the converter's output against. Capturing trees during
the history pass costs one checkout on an already-cloned repository;
re-cloning thousands later is the expensive failure mode this layout
exists to avoid.

---

## Join graph

```
releases ─────────────────┐
                          ├──> lag_observations
repos ──> project_files ──┼──> snapshots
              │           └──> version_events ──> migration_windows ──> churn
              │                       │                                  satd
              │                       └────────────────────────────────> trees/
repos ──> strata
repos ──> branches
repos ──> blocked_demand
repos ──> validation
```

`repo_id` joins everything. `(repo_id, path)` joins project-file-level
tables. `(repo_id, boundary_sha)` joins the migration-cost tables.

---

## Table → research question

| Table | Paper A | Paper B | Paper C |
|---|---|---|---|
| `releases` | timeline figure | lag denominator | converter target versions |
| `repos` | corpus description | cohort definition | subset frame |
| `project_files` | multi-project finding | — | conversion units |
| `snapshots` | version distribution | current-state cohort | — |
| `version_events` | **core contribution** + message-miss rate | event dates | boundary shas |
| `migration_windows` | — | B-RQ2 unit | — |
| `churn` | — | **B-RQ2** cost vs. lag | residual denominator |
| `branches` | — | **B-RQ3** interest | — |
| `satd` | — | **B-RQ4** repayment debt | — |
| `blocked_demand` | — | **B-RQ1** the thesis | — |
| `strata` | games vs. tooling finding | control variable | subset selection |
| `validation` | agreement rate (C9) | — | — |
| `lag_observations` | survival curves | never-migrated cohort | — |
| `trees/` | artifact | — | **the ground truth** |

---

## Caveats bound to this schema

C1–C5 and C9–C12 from the evidence-mining caveat registry apply. Each
must be restated at its point of use in prose, not gathered once into a
threats section. Schema-specific additions:

- **`declared_minor` is unavailable for all of Godot 3.x.** Not missing
  data; the file does not carry it. Do not impute.
- **Branch counts are lower bounds.** Deleted-after-merge branches leave
  no trace.
- **`by_ext` churn conflates converter output with human edits** for
  `.tscn`/`.tres`. That is why it is split out rather than pooled.
- **Prerelease adoption predates `4.0-stable` by months.** Lag computed
  against stables alone goes negative for early adopters.
