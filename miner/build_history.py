"""Stage 3 -- version events, windows, churn, branches.

Runs mine_repo.py in batches under `ev.py sh` and parses the stored
NDJSON back into four tables. Distance between minor versions is
computed here, against the releases table, rather than in the miner:
"4.1 -> 4.6" is five releases only if five releases actually shipped in
between, which is a fact about the engine, not about the repository.
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (ROOT, WORK, DATASET, ev_sh, ev_body, write_table, read_table, code_digest,
                    ev_claim, ENGINE, load_state, save_state)

BATCH = 16
WORKERS = 4


def minor_index(releases):
    """Ordered list of first-of-minor stable versions per line, e.g. 4.0..4.7."""
    firsts = [r for r in releases if r["channel"] == "stable" and r["is_first_of_minor"]]
    firsts.sort(key=lambda r: r["released_on"])
    return [r["minor_series"] for r in firsts], {r["minor_series"]: r["released_on"] for r in firsts}


def run_batches(repo_ids, tag="history", keep_trees=True):
    cd = code_digest(ROOT / "miner/mine_repo.py", ROOT / "miner/engines.py")
    st = load_state("history", {"code": cd, "batches": {}})
    if st.get("code") != cd:
        print("  miner changed (%s -> %s); discarding cached batches"
              % (st.get("code"), cd))
        st = {"code": cd, "batches": {}}
    ev_ids = []
    tree_dir = ROOT / "dataset" / "trees"
    tree_dir.mkdir(parents=True, exist_ok=True)
    for start in range(0, len(repo_ids), BATCH):
        chunk = repo_ids[start:start + BATCH]
        key = "|".join(chunk)
        if key in st["batches"]:
            ev_ids.append(st["batches"][key]); continue
        lst = WORK / ("hist_batch_%d.txt" % start)
        lst.write_text("\n".join(chunk))
        cmd = ("python3 %s --repos %s --workdir %s --tree-dir %s --workers %d --satd%s"
               % (ROOT / "miner/mine_repo.py", lst, WORK / "clones", tree_dir, WORKERS,
                  " --keep-trees" if keep_trees else ""))
        print("  batch %d..%d of %d" % (start, start + len(chunk), len(repo_ids)))
        eid = ev_sh(cmd, cwd=ROOT, tag=tag,
                    note="stage3: blobless clone + diff pickaxe over project.godot, %d repos"
                         % len(chunk), timeout=5400)
        if not eid:
            print("    batch FAILED -- gap left, not substituted"); continue
        st["batches"][key] = eid
        save_state("history", st)
        ev_ids.append(eid)
    return ev_ids


def main(repo_ids=None, keep_trees=True):
    releases = read_table("releases")
    if not releases:
        sys.exit("no releases table -- run build_releases.py first")
    order, rel_date = minor_index(releases)
    pos = {m: i for i, m in enumerate(order)}

    if repo_ids is None:
        p = WORK / "deep_tier.txt"
        if not p.exists():
            sys.exit("no deep tier list -- write work/deep_tier.txt")
        repo_ids = [l.strip() for l in p.read_text().splitlines() if l.strip()]

    ev_ids = run_batches(repo_ids, keep_trees=keep_trees)
    if not ev_ids:
        sys.exit("no history batches stored")

    events, windows, churn, branches, repo_hist, satd = [], [], [], [], [], []
    errs = []
    for eid in ev_ids:
        for line in ev_body(eid).decode("utf-8", "ignore").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            rid = r["repo_id"]
            if r.get("error"):
                errs.append((rid, r["error"])); continue
            repo_hist.append(dict(
                repo_id=rid, engine=ENGINE, default_branch=r.get("default_branch"),
                default_branch_line=r.get("default_branch_line"),
                default_branch_names_version=r.get("default_branch_names_version"),
                head_sha=r.get("head_sha"), commits_total=r.get("commits_total"),
                commits_default=r.get("commits_default"), authors_total=r.get("authors_total"),
                first_commit_on=r.get("first_commit_on"), last_commit_on=r.get("last_commit_on"),
                project_paths=r.get("project_paths"), ev_id=eid))
            for e in r.get("events", []):
                fm, tm = e.get("from_minor"), e.get("to_minor")
                dist = skipped = None
                if fm in pos and tm in pos:
                    dist = pos[tm] - pos[fm]
                    skipped = max(0, dist - 1)
                events.append(dict(
                    repo_id=rid, engine=ENGINE, path=e["path"],
                    commit_sha=e["commit_sha"], parent_sha=e.get("parent_sha"),
                    authored_on=e["authored_on"], committed_on=e["committed_on"],
                    from_config_version=e.get("from_config_version"),
                    to_config_version=e.get("to_config_version"),
                    from_minor=fm, to_minor=tm,
                    minor_distance=dist, minors_skipped=skipped,
                    to_minor_released_on=rel_date.get(tm),
                    event_type=e["event_type"], detection=e["detection"],
                    on_default_branch=e.get("on_default_branch"),
                    subject=e.get("subject"),
                    subject_names_engine=e.get("subject_names_engine"),
                    subject_names_version=e.get("subject_names_version"),
                    subject_names_migration=e.get("subject_names_migration"),
                    ev_id=eid))
            for w in r.get("windows", []):
                windows.append(dict(repo_id=rid, engine=ENGINE, ev_id=eid, **w))
            for c in r.get("churn", []):
                churn.append(dict(repo_id=rid, engine=ENGINE, ev_id=eid, **c))
            for t in r.get("trees", []):
                for c in churn:
                    if (c["repo_id"] == rid and c["boundary_sha"] == t["boundary_sha"]
                            and c.get("scope") == "boundary-commit"):
                        c["pre_files"] = t["pre"]["files"]
                        c["pre_bytes"] = t["pre"]["bytes"]
                        c["pre_gd_files"] = t["pre"]["gd_files"]
                        c["pre_scene_files"] = t["pre"]["scene_files"]
            for sd in r.get("satd", []):
                satd.append(dict(repo_id=rid, engine=ENGINE, ev_id=eid, **sd))
            for b in r.get("branches", []):
                lifetime = None
                if b.get("first_on") and b.get("last_on"):
                    from datetime import date
                    try:
                        a = date.fromisoformat(b["first_on"]); z = date.fromisoformat(b["last_on"])
                        lifetime = (z - a).days
                    except ValueError:
                        pass
                branches.append(dict(repo_id=rid, engine=ENGINE, lifetime_days=lifetime,
                                     ev_id=eid, **b))

    write_table("version_events", events)
    write_table("migration_windows", windows)
    write_table("churn", churn)
    write_table("branches", branches)
    write_table("repo_history", repo_hist)
    if satd:
        write_table("satd", satd)
    if errs:
        print("  %d repos failed:" % len(errs))
        for rid, e in errs[:8]:
            print("    %-40s %s" % (rid, e[:80]))

    majors = [e for e in events if e["event_type"] == "major_migration"]
    m34 = [e for e in majors if e["from_config_version"] == 4 and e["to_config_version"] == 5]
    named = [e for e in m34 if e["subject_names_engine"] or e["subject_names_version"]]
    print("  events=%d  major=%d  3->4=%d  minor_upgrades=%d"
          % (len(events), len(majors), len(m34),
             sum(1 for e in events if e["event_type"] == "minor_upgrade")))
    if m34:
        print("  3->4 boundary commits whose subject names the engine or a version: %d/%d (%.0f%%)"
              % (len(named), len(m34), 100.0 * len(named) / len(m34)))
        ev_claim("Across %d repositories mined by file-content pickaxe, %d Godot 3->4 boundary "
                 "commits were found; %d of them (%.0f%%) have a commit subject that names the "
                 "engine or a version, so a commit-message search would miss the remaining "
                 "%d (%.0f%%)."
                 % (len(repo_hist), len(m34), len(named), 100.0 * len(named) / len(m34),
                    len(m34) - len(named), 100.0 * (len(m34) - len(named)) / len(m34)),
                 "MEASURED", ev_ids[:40],
                 caveat="Subject line only; a body mentioning the engine is not counted, so this "
                        "is an upper bound on what a subject-based search would find and a lower "
                        "bound on the miss rate of a full-message search.")
    return events


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos")
    ap.add_argument("--no-trees", action="store_true")
    a = ap.parse_args()
    ids = None
    if a.repos:
        ids = [l.strip() for l in open(a.repos) if l.strip()]
    main(ids, keep_trees=not a.no_trees)
