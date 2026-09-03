# Metrics

Every quantity the papers report, defined before it is computed.
`ACADEMIC_WRITING_GUIDE.md` §1.5 requires the Experiments section to
give metrics, then their definitions, then implementation, then data —
in that order. This file is the first two, so the paper can state them
without re-deriving them and so a reader can check a number against its
definition rather than against a prose gloss.

Notation: for a project *p*, a version-change event *e*, and a date *t*.

---

## 1. Lag

**L1 · Version lag (releases).** The number of first-of-minor stable
releases of the newer engine line that shipped strictly after the
version *p* declares and at or before *t*.

```
lag_releases(p, t) = |{ r ∈ releases : r.channel = stable
                                     ∧ r.is_first_of_minor
                                     ∧ r.line = newer_line
                                     ∧ origin(p) < r.released_on ≤ t }|
```

Counted in releases, not days, because release cadence is irregular:
4.5→4.6 took 133 days and 4.2→4.3 took 259, so a day count silently
weights the same gap differently.

**L2 · Time at risk.** `duration_days = t_end − origin(p)`, where
`origin(p)` is the later of the date *p* first declared the older major
version and the date the newer major became available, and `t_end` is
the migration date or the censoring date.

**Two origins are recorded, not one.** Under `origin_rule = 4.0-stable`
the clock starts 2023-03-01; under `4.0-beta1` it starts 2022-09-15.
The choice is consequential — 4.0 betas wrote `config_version=5` from
September 2022, so tooling projects migrated months before the stable
release and a stable-only origin gives them negative time at risk. Both
rules are emitted; the paper must say which it reports.

**L3 · Supported lag.** `still_supported(p, t)` is true when a stable
release of the line *p* declares shipped at or after *t*.

This is not decoration. `3.6.3-stable` shipped 2026-08-22, three years
and five months after `4.0-stable`. Lag measured against the newest
release counts a project on Godot 3.6 as three major-line versions
behind; lag measured against the newest *supported* release counts it as
current. Papers that conflate the two are measuring obsolescence and
calling it lag.

---

## 2. Migration

**M1 · Major migration.** An event where `config_version` increases
across a commit and its first parent. Godot: 4→5 is 3.x→4.x, 3→4 is
2.x→3.x.

**M2 · Time to migration.** `duration_days` at the migration event.
Estimated with **Kaplan–Meier**, not by averaging over migrators.
Averaging conditions on the event having occurred and is guaranteed to
understate the wait, because a project that has waited four years and
counting contributes nothing to the mean while every fast migrator
contributes fully. Never-migrated projects are right-censored at their
last observation, and they are the substantive population for Paper B.

**M3 · Message-miss rate.** The fraction of major migrations whose
commit subject names neither the engine nor a version.

```
miss_rate = |{ e : e.type = major_migration
                 ∧ ¬e.subject_names_engine
                 ∧ ¬e.subject_names_version }| / |{ e : e.type = major_migration }|
```

An estimate of how much of the phenomenon a commit-message study cannot
see. It is a **lower bound on the miss rate** of a full-message search,
because only the subject line is examined; a body mentioning the engine
is not counted.

---

## 3. Principal — what repayment cost

**P1 · Boundary churn.** Insertions plus deletions in the commit where
the declared version changed. Cheap, exact, and an underestimate: on
Thrive the boundary commit touched scenes and configuration only.

**P2 · Window churn.** Insertions plus deletions across the migration
window (§`SCHEMA.md` 6). Closer to the true cost and noisier. Windows
marked `saturated` hit a bound rather than finding a natural edge and
must be excluded or reported separately, never pooled with clean ones.

**P3 · Churn by extension.** `.gd`/`.cs` churn is separated from
`.tscn`/`.tres` churn throughout. Godot's converter rewrites scene and
resource files wholesale, so pooling them lets mechanical rewriting
masquerade as hand-written effort — which would corrupt the B-RQ2
regression outright rather than merely add noise.

**P4 · Size control.** `pre_files`, `pre_bytes`, `pre_gd_files`,
`pre_scene_files` at the commit before the window. Any claim that churn
grows with lag must control for size, because larger projects both lag
longer and churn more, and the uncontrolled correlation is nearly
uninformative.

---

## 4. Interest — what waiting cost

**I1 · Parallel-branch lifetime.** `last_on − first_on` for a branch
whose name names the engine or an explicit move to the new major.

**I2 · Duplicated work.** `duplicated_commits`: branch commits whose
patch already has an equivalent on the default branch, by `git cherry`
patch-id equivalence.

This is the interest payment made concrete. A fix applied to both the
maintenance branch and the migration branch is work done twice, and the
count is a direct measure rather than an inference from how long a
branch stayed open. **A project with no such branch is paying no
interest**, and that is the observation Paper B's negative thesis turns
on.

**I3 · Branch survival bias.** Branches deleted after merge leave no
trace, so I1 and I2 are lower bounds. Restate wherever cited.

---

## 5. Debt incurred by repayment

**D1 · SATD delta.** `post − pre` across the migration window for TODO,
FIXME, HACK, XXX in `.gd` and `.cs`.

**D2 · Normalised SATD.** `satd_total / loc_total`, per side. The raw
delta confounds with project growth: Thrive gained 8,197 lines across its
window, so a TODO increase of 41 is a different claim before and after
normalisation. Both are emitted; the paper must report the normalised
one and may report the raw one beside it.

**D3 · Test attrition.** `test_files`, `test_cases`, `skipped_tests`,
each side. A migration that ships by disabling tests has moved debt
rather than repaid it.

**D4 · Commented-out code.** Comment lines whose content parses as a
statement. A deliberately conservative heuristic — a doc comment
containing `=` must not inflate it — and it must be described as a
heuristic every time it is reported.

---

## 6. Blocked demand

**B1 · Demand signal.** An issue or pull request asking for the newer
version, naming a blocker, or a live migration branch.

**B2 · Counter-demand signal.** A maintainer stating no intent to
migrate, or that the current version suffices.

**B3 · In-debt fraction.** Of lagging projects, the fraction carrying at
least one demand signal.

```
in_debt = |{ p : lagging(p) ∧ ∃ demand signal }| / |{ p : lagging(p) }|
```

**This is Paper B's headline number and the schema is built so it can
come out either way.** Lag without demand is a supported steady state,
not debt; a collector that recorded only demand would make the negative
thesis untestable by construction, which is why B2 exists as a
first-class signal rather than as the absence of B1.

B1–B3 are **rule-labelled** and require two-coder adjudication with a
reported κ before publication. Issue-tracker evidence also
over-represents organised teams: a solo developer who decided either way
in silence leaves no record at all (caveat C7).

---

## 7. Instrument agreement

**A1 · Agreement rate.** Of repositories carrying at least one
independent version signal, the fraction where it matches the declared
version.

```
agreement = |agree| / (|agree| + |disagree|)
```

Only **machine-readable** signals count: CI pins, container tags, export
presets. A README mention is recorded but excluded, because the study
measured its agreement at 54% against 79% for CI pins — a README carries
compatibility tables and changelogs, not the engine in use. Those cases
get the verdict `prose-only`.

Repositories with **no** independent signal are excluded from the
denominator and reported separately. Counting them as agreeing would
inflate the rate with every project that simply has no CI — which is
most small ones, and the single easiest way to publish a reassuring and
meaningless validation number.

**A3 · Recovered 3.x minor.** For a Godot 3 project, the minor version
read from an independent signal. `project.godot` cannot express it;
a CI pin can. Available only for the subset that has CI, so it
supplements the instrument for a biased subsample rather than replacing
it — corpus-wide 3.x minor lag remains unavailable.

**A2 · Disagreement kind.** `declared-newer`, `declared-older`, or
`different-line`. The direction matters: a project whose CI pins a newer
engine than its `project.godot` declares is a measurement artefact,
while one pinning an older engine may be genuinely running behind its
own declaration.

---

## Intervals and tests

Report a confidence interval beside every point estimate and name the
method. Defaults for these data:

- **Proportions** (miss rate, agreement, in-debt fraction): Wilson score
  interval. Normal approximation misbehaves near 0 and 1, and several of
  these proportions are expected to sit there.
- **Survival** (time to migration): Kaplan–Meier with Greenwood
  standard errors; log-rank to compare strata.
- **Churn against lag**: churn is heavily right-skewed and bounded below
  at zero, so fit on a log scale or use a rank correlation, and report
  the size control's coefficient rather than only the lag coefficient.
- **Paired pre/post** (SATD, tests): Wilcoxon signed-rank, with the
  normalised metric D2 as the primary.
