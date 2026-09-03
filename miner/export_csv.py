"""Flatten the JSONL tables to CSV for consumers who want tabular input.
Nested objects are dropped rather than stringified: a column that is
sometimes JSON and sometimes a scalar is worse than an absent one.
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATASET, read_table

TABLES = ["releases", "repos", "project_files", "snapshots", "strata", "version_events",
          "migration_windows", "churn", "branches", "repo_history", "validation",
          "blocked_demand", "lag_observations"]


def main():
    out = DATASET / "csv"
    out.mkdir(exist_ok=True)
    for t in TABLES:
        rows = read_table(t)
        if not rows:
            continue
        cols, skipped = [], []
        for r in rows:
            for k, v in r.items():
                if k in cols or k in skipped:
                    continue
                (skipped if isinstance(v, (dict, list)) else cols).append(k)
        with (out / (t + ".csv")).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k) for k in cols})
        print("%-22s %6d rows  %2d cols  (dropped nested: %s)"
              % (t, len(rows), len(cols), ",".join(skipped) or "-"))


if __name__ == "__main__":
    main()
