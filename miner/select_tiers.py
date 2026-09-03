"""Tier selection, written down rather than left implicit.

Cost per repository rises by three orders of magnitude across the tiers,
so which repositories get the expensive treatment is a sampling decision
and has to be auditable. This writes the decision into the repos table
as `tier` and emits the work lists.
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import json, os, random, sys
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import WORK, write_table, read_table, today

SEED = 20260825          # fixed: the sample must be reproducible


SNAPSHOT_TOPICS = {"godot", "godot-engine", "godot-game", "godotengine"}


def snapshot_tier(repos, budget, min_stars=10):
    """Every repository whose owner tagged it as Godot, plus every other
    repository above a star floor, capped at the budget.

    A topic is the owner asserting this is a Godot project. GitHub's
    language guess is not: it fires on a repository containing a single
    .gd file. The star floor then reaches projects whose owners never
    added a topic, which is most of them.
    """
    tagged, other = [], []
    for r in repos:
        if not r.get("included"):
            continue
        if {t.lower() for t in (r.get("topics") or [])} & SNAPSHOT_TOPICS:
            tagged.append(r)
        elif r.get("stars", 0) >= min_stars:
            other.append(r)
    other.sort(key=lambda r: -r.get("stars", 0))
    room = max(0, budget - len(tagged))
    kept = other[:room]
    if len(other) > room:
        print("  !! budget cut %d repositories above the %d-star floor -- raise "
              "--snapshot-budget to include them" % (len(other) - room, min_stars))
    return tagged + kept, len(tagged), len(kept)


def history_tier(snaps, repos, strata, budget, all_godot3=False):
    """Both cohorts, or the comparison is not available.

    Half the budget goes to projects still declaring Godot 3, taken in
    star order, or to the whole cohort with --all-godot3. The other half
    samples migrators stratified by project kind and star band, because
    tooling and games migrated years apart and a top-N-by-stars sample of
    migrators would be almost entirely tooling.

    Star ordering within the Godot 3 cohort is a known bias, and it is
    not neutral: popular projects carry more issue-tracker evidence, which
    is the same evidence the demand table records. Prefer --all-godot3
    where the clone budget allows it.
    """
    prim = {}
    for s in snaps:
        if s.get("parse_status") == "ok":
            prim.setdefault(s["repo_id"], s)
    byid = {r["repo_id"]: r for r in repos}
    strat = {s["repo_id"]: s["stratum"] for s in strata}

    # A size-filtered clone still pulls the whole text history; a 2.5 GB
    # repository (BDCC, measured) blows the time budget for one row.
    MAX_KB = 1_500_000
    still3, on4, oversize = [], [], []
    for rid, s in prim.items():
        r = byid.get(rid)
        if not r:
            continue
        if (r.get("size_kb") or 0) > MAX_KB:
            oversize.append(rid); continue
        (still3 if s.get("config_version") == 4 else on4).append(rid)
    if oversize:
        print("  %d repos over %d KB excluded from the history tier: %s"
              % (len(oversize), MAX_KB, ", ".join(sorted(oversize)[:4])))

    rng = random.Random(SEED)
    still3.sort(key=lambda rid: -byid[rid].get("stars", 0))
    take3 = still3 if all_godot3 else still3[:budget // 2]

    def band(rid):
        st = byid[rid].get("stars", 0)
        return ">=100" if st >= 100 else (">=10" if st >= 10 else "<10")
    cells = defaultdict(list)
    for rid in on4:
        cells[(strat.get(rid, "unknown"), band(rid))].append(rid)
    room = max(0, budget - len(take3))
    take4, cellnames = [], sorted(cells)
    for c in cellnames:
        rng.shuffle(cells[c])
    i = 0
    while len(take4) < room and any(cells[c] for c in cellnames):
        c = cellnames[i % len(cellnames)]
        if cells[c]:
            take4.append(cells[c].pop())
        i += 1
    return take3, take4, dict(Counter(strat.get(r, "unknown") for r in take4))


def main(snapshot_budget=6000, history_budget=200, min_stars=10, all_godot3=False):
    repos = read_table("repos")
    snaps = read_table("snapshots")
    strata = read_table("strata")
    byid = {r["repo_id"]: r for r in repos}

    if not snaps:
        sel, n_tag, n_star = snapshot_tier(repos, snapshot_budget, min_stars)
        for r in repos:
            r["tier"] = "frame"
        for r in sel:
            byid[r["repo_id"]]["tier"] = "snapshot"
        write_table("repos", repos)
        (WORK / "snapshot_tier.txt").write_text("\n".join(r["repo_id"] for r in sel))
        print("  snapshot tier: %d repos = %d topic-tagged + %d untagged with >=%d stars, "
              "of %d included in the frame"
              % (len(sel), n_tag, n_star, min_stars,
                 sum(1 for r in repos if r.get("included"))))
        return

    take3, take4, cells = history_tier(snaps, repos, strata, history_budget, all_godot3)
    for rid in take3 + take4:
        if rid in byid:
            byid[rid]["tier"] = "history"
    write_table("repos", repos)
    (WORK / "deep_tier.txt").write_text("\n".join(take3 + take4))
    (WORK / "demand_tier.txt").write_text("\n".join(take3 + take4))
    print("  history tier: %d repos -- %d still declaring Godot 3 (%s), %d migrators "
          "sampled stratified by kind and star band"
          % (len(take3) + len(take4), len(take3),
             "whole cohort" if all_godot3 else "top by stars", len(take4)))
    print("  migrator strata: %s" % json.dumps(cells))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-budget", type=int, default=6000)
    ap.add_argument("--history-budget", type=int, default=200)
    ap.add_argument("--all-godot3", action="store_true",
                    help="take the entire still-on-Godot-3 cohort, not the top half-budget")
    ap.add_argument("--min-stars", type=int, default=10,
                    help="star floor for untagged repositories in the snapshot tier")
    a = ap.parse_args()
    main(a.snapshot_budget, a.history_budget, a.min_stars, a.all_godot3)
