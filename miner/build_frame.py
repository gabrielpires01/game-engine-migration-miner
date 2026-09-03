"""Stage 1 -- the corpus frame.

GitHub search caps retrieval at 1,000 results however large total_count
is (C2), so each query is bisected on created: until every slice is
under the cap, and every slice is enumerated in full. Excluded repos are
kept with a reason: a frame that keeps only survivors cannot be audited.
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import json, sys, time
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import (ev_gh, ev_json, write_table, ev_claim, today,
                    load_state, save_state, WORK)

CAP = 1000
PER_PAGE = 100
# 4.0-stable. A project created after it never faced the 3->4 decision,
# so the never-migrated cohort would be meaningless with them included.
CUTOFF = "2023-03-01"
BASE_QUERIES = ["topic:godot", "topic:godot-engine", "language:GDScript"]
FLOOR = "2014-01-01"   # godotengine/godot itself was created 2014-01-04


def search(q, page=1):
    eid = ev_gh(["api", "-X", "GET", "search/repositories",
                 # sort by an IMMUTABLE field: paging on sort=updated is
                 # unstable, because a repository pushed between two page
                 # requests moves across the page boundary and is missed.
                 # Creation dates do not change (caveat C13).
                 "-f", "q=%s" % q, "-f", "sort=created", "-f", "order=asc",
                 "-f", "per_page=%d" % PER_PAGE, "-f", "page=%d" % page],
                tag="frame", note="stage1 corpus frame")
    if not eid:
        return None, None
    time.sleep(2.2)          # search API: 30 req/min authenticated
    return eid, ev_json(eid)


def count(q):
    eid, d = search(q, 1)
    if d is None:
        return None, None, None
    return eid, d.get("total_count"), d.get("incomplete_results", False)


def bisect(base, lo, hi, out):
    """Split [lo,hi] on created: until each slice is under the retrieval cap."""
    q = "%s fork:false created:%s..%s" % (base, lo, hi)
    eid, total, incomplete = count(q)
    if total is None:
        print("    FAILED %s" % q); return
    if incomplete:
        print("    !! incomplete_results on %s -- count is PARTIAL" % q)
    print("    %s..%s -> %s%s" % (lo, hi, total, " INCOMPLETE" if incomplete else ""))
    if total == 0:
        return
    if total <= CAP or lo == hi:
        out.append((q, eid, total))
        return
    y0, y1 = int(lo[:4]), int(hi[:4])
    if y1 - y0 >= 1:
        mid = "%d-12-31" % ((y0 + y1) // 2)
        bisect(base, lo, mid, out)
        bisect(base, "%d-01-01" % ((y0 + y1) // 2 + 1), hi, out)
    else:
        m0, m1 = int(lo[5:7]), int(hi[5:7])
        if m0 != m1:                         # same year: split on months
            mid = (m0 + m1) // 2
            last = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mid - 1]
            bisect(base, lo, "%d-%02d-%02d" % (y0, mid, last), out)
            bisect(base, "%d-%02d-01" % (y0, mid + 1), hi, out)
            return
        d0, d1 = int(lo[8:10]), int(hi[8:10])   # same month: split on days
        if d0 == d1:
            # A single day over the cap cannot be sliced further on created:.
            # Record it as knowingly truncated rather than pretending it is complete.
            print("    !! %s exceeds the retrieval cap on a single day -- TRUNCATED" % q)
            out.append((q, eid, total)); return
        mid = (d0 + d1) // 2
        bisect(base, lo, "%d-%02d-%02d" % (y0, m0, mid), out)
        bisect(base, "%d-%02d-%02d" % (y0, m0, mid + 1), hi, out)


def enumerate_slice(q, total, repos, ev_ids):
    pages = min((total + PER_PAGE - 1) // PER_PAGE, CAP // PER_PAGE)
    got = 0
    for page in range(1, pages + 1):
        eid, d = search(q, page)
        if d is None:
            print("      page %d FAILED" % page); continue
        ev_ids.append(eid)
        items = d.get("items", [])
        got += len(items)
        for it in items:
            fn = it["full_name"]
            r = repos.setdefault(fn, dict(
                repo_id=fn, host="github.com", url=it["html_url"],
                default_branch=it.get("default_branch"),
                created_at=it["created_at"][:10], pushed_at=it["pushed_at"][:10],
                updated_at=it["updated_at"][:10],
                stars=it["stargazers_count"], forks=it["forks_count"],
                open_issues=it["open_issues_count"], size_kb=it["size"],
                language=it.get("language"), topics=it.get("topics") or [],
                license=(it.get("license") or {}).get("spdx_id"),
                is_fork=it["is_fork"] if "is_fork" in it else it.get("fork", False),
                archived=it.get("archived", False), disabled=it.get("disabled", False),
                description=(it.get("description") or "")[:300],
                homepage=(it.get("homepage") or "")[:200],
                discovered_via=[], discovered_on=today(), ev_ids=[]))
            if q not in r["discovered_via"]:
                r["discovered_via"].append(q)
            if eid not in r["ev_ids"]:
                r["ev_ids"].append(eid)
        if len(items) < PER_PAGE:
            break
    return got


def main():
    st = load_state("frame", {"slices": [], "done": []})
    slices = [tuple(s) for s in st["slices"]]
    if not slices:
        for base in BASE_QUERIES:
            print("  bisecting %s" % base)
            bisect(base, FLOOR, CUTOFF, slices)
        st["slices"] = [list(s) for s in slices]
        save_state("frame", st)
    print("  %d slices under the %d cap" % (len(slices), CAP))

    repos, ev_ids = {}, []
    for i, (q, eid, total) in enumerate(slices, 1):
        print("  [%d/%d] %s (%s)" % (i, len(slices), q, total))
        got = enumerate_slice(q, total, repos, ev_ids)
        print("      +%d  (running unique: %d)" % (got, len(repos)))
        save_state("frame_partial", {"n": len(repos)})
        with open(WORK / "frame_repos.json", "w") as f:
            json.dump(repos, f)

    rows = list(repos.values())
    for r in rows:
        reason = None
        if r["repo_id"] in ("godotengine/godot", "Redot-Engine/redot-engine",
                            "godotengine/godot-docs", "godotengine/awesome-godot"):
            reason = "engine-itself"
        elif r["is_fork"]:
            reason = "fork"
        r["exclusion_reason"] = reason
        r["included"] = reason is None      # refined in stage 2 by project-file presence
    write_table("repos", rows)
    print("  unique repos: %d   provisionally included: %d"
          % (len(rows), sum(1 for r in rows if r["included"])))

    ev_claim("The corpus frame enumerates %d unique non-fork GitHub repositories matching "
             "topic:godot, topic:godot-engine or language:GDScript and created before "
             "%s (4.0-stable), retrieved on %s across %d date-sliced queries."
             % (len(rows), CUTOFF, today(), len(slices)),
             "MEASURED", ev_ids[:40],
             caveat="Enumerated, not counted: each slice was kept under GitHub's 1,000-result "
                    "retrieval cap (C2) and paged in full. Search indexing is approximate and "
                    "results drift between runs (C1); the retrieval date is recorded per row.")
    return rows


if __name__ == "__main__":
    main()
