"""Stage 5 -- derived tables. Pure functions of the measured ones and of
one parameter: the observation date at which unmigrated projects are
right-censored.

Deleting everything this writes and re-running with the same
--observed-on must be a no-op. Nothing here fetches; if a value cannot
be computed from a measured table it is left null rather than imputed.

The observation date defaults to today (UTC), which is what a fresh
collection wants, and is written into summary.json so a published
release can be re-derived exactly: pass the generated_on of the release
you are reproducing.
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import argparse, json, os, sys
from collections import Counter
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import write_table, read_table, ev_claim, ENGINE, today, DATASET

OLD_LINE = "3.x"          # the line the 3->4 risk set is leaving
BETA1 = "2022-09-15"      # 4.0-beta1: the first build that wrote config_version=5
STABLE40 = "2023-03-01"


def d(s):
    return date.fromisoformat(s) if s else None


def days(a, b):
    return (d(b) - d(a)).days if a and b else None


def vkey(v):
    """Numeric version ordering. String ordering puts 4.10 below 4.7."""
    out = []
    for part in (v or "").split("."):
        out.append(int(part) if part.isdigit() else 0)
    return tuple(out + [0] * (4 - len(out)))


def main(observed_on=None):
    observed_on = observed_on or today()
    rel = read_table("releases")
    stable = sorted([r for r in rel if r["channel"] == "stable"], key=lambda r: r["released_on"])
    firsts = [r for r in stable if r["is_first_of_minor"]]
    repos = {r["repo_id"]: r for r in read_table("repos")}
    strata = {s["repo_id"]: s["stratum"] for s in read_table("strata")}
    hist = {h["repo_id"]: h for h in read_table("repo_history")}
    events = read_table("version_events")
    snaps = read_table("snapshots")

    by_repo = {}
    for e in events:
        by_repo.setdefault(e["repo_id"], []).append(e)

    def releases_between(a, b, line=None):
        return [r for r in firsts if a < r["released_on"] <= b and (line is None or r["line"] == line)]

    SUPPORT_WINDOW_DAYS = 365

    def line_supported_at(line, when):
        """Was this engine line still being maintained at `when`?

        True if a stable release of the line shipped at or after `when`, or
        if its most recent release was within a year before it. The literal
        test alone -- "did a release ship on or after this date" -- reports
        every observation made in the four days after 3.6.3 (2026-08-22) as
        unsupported, which inverts the fact it exists to record. A line
        patched last month is maintained; the window says so.
        """
        rel = [r["released_on"] for r in stable if r["line"] == line]
        if not rel:
            return False
        if max(rel) >= when:
            return True
        prior = [d for d in rel if d < when]
        return bool(prior) and days(max(prior), when) <= SUPPORT_WINDOW_DAYS

    rows = []
    for rid, h in hist.items():
        evs = sorted([e for e in by_repo.get(rid, []) if e.get("on_default_branch")],
                     key=lambda e: e["authored_on"])
        mig = next((e for e in evs
                    if e["event_type"] == "major_migration"
                    and e["from_config_version"] == 4 and e["to_config_version"] == 5), None)
        # was the project on Godot 3 at all, and from when?
        on3_from = next((e["authored_on"] for e in evs
                         if (e["to_config_version"] == 4)
                         or (e["event_type"] == "initial" and e["to_config_version"] == 4)), None)
        if on3_from is None and not mig:
            continue                      # never on Godot 3: not at risk of a 3->4 migration
        # Censor at the observation date, not at the last commit. We looked
        # at this repository today and it had not migrated; stopping the
        # clock at its last commit would discard the years since and
        # understate every wait. Abandonment is a competing risk, carried as
        # a covariate rather than folded into the censoring time.
        last_activity = (h.get("last_commit_on")
                         or repos.get(rid, {}).get("pushed_at"))

        for rule, origin_base in (("4.0-stable", STABLE40), ("4.0-beta1", BETA1)):
            # At risk from whichever came later: the project adopting Godot 3,
            # or Godot 4 becoming available under this rule.
            origin = max(on3_from or origin_base, origin_base)
            event_on = mig["authored_on"] if mig else None
            if event_on and event_on < origin:
                continue          # migrated before it was at risk under this rule
            end = event_on or observed_on
            if end < origin:
                continue
            behind = releases_between(origin, end, line="4.x")
            rows.append(dict(
                repo_id=rid, engine=ENGINE, stratum=strata.get(rid, "unknown"),
                origin_rule=rule, origin_on=origin,
                event_on=event_on, censor_on=None if mig else observed_on,
                last_activity_on=last_activity,
                dormant_days=None if mig else days(last_activity, observed_on),
                event=1 if mig else 0, duration_days=days(origin, end),
                boundary_sha=mig["commit_sha"] if mig else None,
                latest_stable_at_end=(max((r["version"] for r in stable
                                           if r["released_on"] <= end),
                                          key=vkey, default=None)),
                lag_releases=len(behind),
                # The line the project is ON, which for a 3->4 risk set is 3.x.
                # 3.6.3 shipped 2026-08-22, so this is routinely true for
                # projects the naive lag measure calls three versions behind.
                still_supported=line_supported_at(OLD_LINE, end),
                days_since_old_line_release=days(
                    max([r["released_on"] for r in stable
                         if r["line"] == OLD_LINE and r["released_on"] <= end] or [None]) or end, end),
                commits_total=h.get("commits_total"), authors_total=h.get("authors_total"),
                ev_ids=[h["ev_id"]] + ([mig["ev_id"]] if mig else [])))
    write_table("lag_observations", rows)

    prim = [s for s in snaps if s.get("parse_status") == "ok"]
    # config_version -> engine line, so the Godot 2.x cohort is labelled
    # rather than bucketed under "?". 430 files in the v0.1 snapshot still
    # declare config_version=3, which is Godot 2.x -- two engine majors
    # behind, and worth naming.
    CV_LINE = {3: "2.x", 4: "3.x", 5: "4.x"}
    cur = Counter()
    for s in prim:
        cur[s.get("declared_minor") or CV_LINE.get(s.get("config_version"), "unparsed")] += 1

    a = [r for r in rows if r["origin_rule"] == "4.0-stable"]
    migrated = [r for r in a if r["event"] == 1]
    censored = [r for r in a if r["event"] == 0]
    summary = dict(
        generated_on=observed_on,
        releases=len(rel), releases_stable=len(stable),
        repos_frame=len(repos), repos_included=sum(1 for r in repos.values() if r.get("included")),
        repos_mined=len(hist), version_events=len(events),
        major_migrations=sum(1 for e in events if e["event_type"] == "major_migration"),
        minor_upgrades=sum(1 for e in events if e["event_type"] == "minor_upgrade"),
        minor_downgrades=sum(1 for e in events if e["event_type"] == "minor_downgrade"),
        major_rollbacks=sum(1 for e in events if e["event_type"] == "major_rollback"),
        at_risk_3to4=len(a), migrated=len(migrated), right_censored=len(censored),
        declared_version_distribution=dict(cur.most_common()),
        strata=dict(Counter(strata.values()).most_common()),
    )
    # Tooling and games migrated years apart, so a pooled mean over both
    # describes neither. The cross-tab is generated here rather than left
    # for a consumer to assemble.
    majors = [e for e in events
              if e["event_type"] == "major_migration"
              and e["from_config_version"] == 4 and e["to_config_version"] == 5]
    xtab = {}
    for e in majors:
        st = strata.get(e["repo_id"], "unknown")
        xtab.setdefault(st, Counter())[e["authored_on"][:4]] += 1
    summary["migration_year_by_stratum"] = {k: dict(sorted(v.items()))
                                            for k, v in sorted(xtab.items())}
    if majors:
        named = [e for e in majors
                 if e.get("subject_names_engine") or e.get("subject_names_version")]
        summary["message_miss"] = dict(
            major_3to4=len(majors), subject_names_engine_or_version=len(named),
            missed_by_message_search=len(majors) - len(named),
            miss_rate=round(1.0 - len(named) / len(majors), 4))
    # Which minor did migrators land on? A project that waits does not
    # arrive at 4.0; it arrives at whatever is current when it moves.
    summary["landed_on_minor"] = dict(
        Counter(e["to_minor"] for e in majors if e.get("to_minor")).most_common())

    if migrated:
        ds = sorted(r["duration_days"] for r in migrated if r["duration_days"] is not None)
        summary["migration_days_median"] = ds[len(ds) // 2] if ds else None
        summary["migration_days_min"] = ds[0] if ds else None
        summary["migration_days_max"] = ds[-1] if ds else None
    (DATASET / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))

    if a:
        ev_claim("Among %d mined repositories that were on Godot 3 when 4.0-stable shipped, "
                 "%d had migrated to Godot 4 by the observation date and %d had not and are "
                 "right-censored." % (len(a), len(migrated), len(censored)),
                 "DERIVED", sorted({i for r in a for i in r["ev_ids"]})[:40],
                 caveat="Time-to-migration must be estimated with right-censoring. Averaging over "
                        "migrators alone conditions on the event having occurred and is "
                        "guaranteed to understate the wait.")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--observed-on", metavar="YYYY-MM-DD",
                    help="observation date at which unmigrated projects are "
                         "right-censored; defaults to today (UTC). Pass the "
                         "generated_on of a published release to reproduce it.")
    a = ap.parse_args()
    if a.observed_on:
        date.fromisoformat(a.observed_on)      # fail loudly on a malformed date
    main(a.observed_on)
