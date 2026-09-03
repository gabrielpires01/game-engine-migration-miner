"""Integrity gate. A problem reported here means a row in the dataset is
not traceable to a stored evidence artifact, or references a repository
absent from the frame. Run it after any build stage.
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATASET, EVIDENCE, read_table

TABLES = ["releases", "repos", "project_files", "snapshots", "strata", "version_events",
          "migration_windows", "churn", "branches", "satd", "repo_history", "validation",
          "dependencies", "repo_signals",
          "blocked_demand", "lag_observations"]
KEYS = {
    "project_files": [("repo_id", "repos")], "snapshots": [("repo_id", "repos")],
    "strata": [("repo_id", "repos")], "version_events": [("repo_id", "repos")],
    "migration_windows": [("repo_id", "repos")], "churn": [("repo_id", "repos")],
    "branches": [("repo_id", "repos")], "validation": [("repo_id", "repos")],
    "lag_observations": [("repo_id", "repos")], "satd": [("repo_id", "repos")],
    "blocked_demand": [("repo_id", "repos")], "repo_history": [("repo_id", "repos")],
    "dependencies": [("repo_id", "repos")], "repo_signals": [("repo_id", "repos")],
}


def main():
    ids = set()
    lp = EVIDENCE / "ledger.jsonl"
    if lp.exists():
        for line in lp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                ids.add(json.loads(line).get("id"))
    problems, loaded = [], {}
    for t in TABLES:
        rows = read_table(t)
        loaded[t] = rows
        if not rows:
            print("%-20s EMPTY" % t); continue
        no_prov = [r for r in rows if not (r.get("ev_id") or r.get("ev_ids"))]
        dangling = []
        for r in rows:
            for e in ([r["ev_id"]] if r.get("ev_id") else []) + (r.get("ev_ids") or []):
                if e not in ids:
                    dangling.append((t, e))
        print("%-20s %7d rows   no-provenance=%d  dangling-ev=%d"
              % (t, len(rows), len(no_prov), len(dangling)))
        if no_prov:
            problems.append("%s: %d rows with no ev_id" % (t, len(no_prov)))
        if dangling:
            problems.append("%s: %d rows citing an evidence id not in the ledger (%s...)"
                            % (t, len(dangling), dangling[0][1]))
    for t, refs in KEYS.items():
        for col, target in refs:
            if not loaded.get(t) or not loaded.get(target):
                continue
            have = {r["repo_id"] for r in loaded[target]}
            orphans = {r[col] for r in loaded[t] if r.get(col) not in have}
            if orphans:
                problems.append("%s: %d %s values not present in %s (e.g. %s)"
                                % (t, len(orphans), col, target, sorted(orphans)[0]))
    print()
    if problems:
        for p in problems:
            print("PROBLEM  " + p)
        return 1
    print("no integrity problems")
    return 0


if __name__ == "__main__":
    sys.exit(main())
