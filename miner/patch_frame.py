"""Recover the repositories that the first frame pass could not retrieve.

Seven date slices came back above GitHub's 1,000-result retrieval cap
(C2). Paging them yields 1,000 of a larger total, so the remainder is
missing -- and a truncated frame looks exactly like a complete one. This
re-bisects those slices to day granularity and merges what they yield.

Run after build_frame.py. Idempotent: repositories already present are
merged, not duplicated.
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import WORK, write_table, read_table, ev_claim, today
import build_frame as bf


def main():
    st = json.loads((WORK / "frame.json").read_text())
    over = [tuple(x) for x in st["slices"] if x[2] > bf.CAP]
    if not over:
        print("  no over-cap slices"); return
    print("  %d slices over the %d cap, %d results unretrieved in the first pass"
          % (len(over), bf.CAP, sum(x[2] - bf.CAP for x in over)))

    repos = {r["repo_id"]: r for r in read_table("repos")}
    before = len(repos)
    ev_ids, new_slices = [], []
    for q, _eid, total in over:
        base = q.split(" fork:false")[0]
        lo, hi = q.split("created:")[1].split("..")
        print("  re-bisecting %s (%d)" % (q, total))
        sub = []
        bf.bisect(base, lo, hi, sub)
        new_slices += sub
    print("  -> %d day-level sub-slices" % len(new_slices))

    fresh = {}
    for q, eid, total in new_slices:
        got = bf.enumerate_slice(q, total, fresh, ev_ids)
        print("      %s -> %d" % (q.split("created:")[1], got))

    added = 0
    for rid, r in fresh.items():
        if rid in repos:
            for v in r["discovered_via"]:
                if v not in repos[rid].get("discovered_via", []):
                    repos[rid].setdefault("discovered_via", []).append(v)
        else:
            reason = "fork" if r["is_fork"] else None
            r["exclusion_reason"] = reason
            r["included"] = reason is None
            r["tier"] = "frame"
            repos[rid] = r
            added += 1
    write_table("repos", list(repos.values()))
    print("  frame: %d -> %d repositories (+%d recovered)" % (before, len(repos), added))
    ev_claim("Re-bisecting the %d date slices that exceeded GitHub's 1,000-result retrieval "
             "cap to day granularity recovered %d repositories the first pass could not "
             "retrieve, taking the frame from %d to %d."
             % (len(over), added, before, len(repos)),
             "MEASURED", ev_ids[:40],
             caveat="A day slice that is still over the cap cannot be split further on "
                    "created: and is recorded as knowingly truncated.")


if __name__ == "__main__":
    main()
