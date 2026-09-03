"""Stage 2 -- project files, declared versions, strata.

Runs fetch_trees.py in batches under `ev.py sh`, so each batch's raw
NDJSON output is stored with the exact command that produced it, then
parses the stored artifacts back into three tables. Nothing is read from
the fetcher's return value in memory: the artifact on disk is the source.
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import json, re, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (ROOT, WORK, ev_sh, ev_body, write_table, read_table, code_digest,
                    ev_claim, today, ENGINE, load_state, save_state)

BATCH = 250


def tier_s(repos, min_stars=0, topics_only=False):
    """Snapshot tier: the repos worth one core-API call each."""
    out = []
    for r in repos:
        if not r.get("included"):
            continue
        t = set(r.get("topics") or [])
        tagged = bool(t & {"godot", "godot-engine", "godot-game", "gdscript"})
        if topics_only and not tagged:
            continue
        if r.get("stars", 0) < min_stars and not tagged:
            continue
        out.append(r)
    return out


def run_batches(rows, tag="snapshot"):
    ev_ids = []
    inp = WORK / "snapshot_input.jsonl"
    with inp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"repo_id": r["repo_id"],
                                "default_branch": r.get("default_branch")}) + "\n")
    # Batches are cached by offset, so the cache is only valid for the same
    # input list. Key it on the input digest or a changed tier silently
    # reuses another tier's results.
    import hashlib
    digest = (hashlib.sha256(inp.read_bytes()).hexdigest()[:12] + "/" +
              code_digest(ROOT / "miner/fetch_trees.py", ROOT / "miner/engines.py"))
    st = load_state("snapshot", {"input": digest, "batches": {}})
    if st.get("input") != digest:
        print("  input changed (%s -> %s); discarding cached batch offsets"
              % (st.get("input"), digest))
        st = {"input": digest, "batches": {}}
    n = len(rows)
    for start in range(0, n, BATCH):
        key = str(start)
        if key in st["batches"]:
            ev_ids.append(st["batches"][key]); continue
        cmd = ("python3 %s --repos %s --start %d --limit %d --workers 6"
               % (ROOT / "miner/fetch_trees.py", inp, start, BATCH))
        print("  batch %d..%d of %d" % (start, min(start + BATCH, n), n))
        eid = ev_sh(cmd, cwd=ROOT, tag=tag,
                    note="stage2: recursive tree + project.godot parse, repos %d..%d"
                         % (start, min(start + BATCH, n)), timeout=3600)
        if not eid:
            print("    batch FAILED -- leaving gap, not substituting"); continue
        st["batches"][key] = eid
        save_state("snapshot", st)
        ev_ids.append(eid)
    return ev_ids


# ------------------------------------------------------------- classification

NAME_RULES = [
    ("addon",             r"\b(addon|addons|plugin|plugins)\b"),
    ("learning-material", r"\b(tutorial|course|learn|learning|workshop|exercise|lesson|book|kata)\b"),
    ("template",          r"\b(template|boilerplate|starter|skeleton|scaffold)\b"),
    ("demo",              r"\b(demo|demos|example|examples|sample|samples|showcase|playground)\b"),
    ("tool",              r"\b(tool|tools|toolkit|multitool|editor|importer|exporter|generator|converter|inspector|profiler|debugger|linter|formatter)\b"
                          r"|\b(unit[- ]?test|testing|test[- ]?framework|test[- ]?runner)\b"),
    ("library",           r"\b(lib|library|sdk|bindings?|framework|wrapper|api|runtime)\b"),
    ("game",              r"\b(game|rpg|platformer|shooter|roguelike|puzzle|jam)\b"),
]
TOPIC_RULES = [
    ("addon",             {"godot-addon", "godot-plugin", "godot-addons", "addon", "plugin"}),
    ("learning-material", {"tutorial", "education", "learning", "course"}),
    ("template",          {"template", "boilerplate", "starter"}),
    ("demo",              {"demo", "godot-demo", "example", "examples"}),
    ("tool",              {"tool", "tools", "gamedev-tools", "editor", "testing",
                           "unit-testing", "test-framework"}),
    # NOT gamedev / game-development: pixel-art editors and engines carry
    # those too, and a topic that fires on both sides of the distinction
    # cannot make it.
    ("game",              {"game", "godot-game", "indie-game", "2d-game", "3d-game",
                           "videogame", "video-game"}),
]


def classify_stratum(repo, rec):
    """Rule-based strata, explicit evidence before inferred shape.

    NOT ground truth. Every row is method="rule"; a two-coder manual pass
    with a reported kappa is required before these labels carry a
    published figure. The rule exists to make that pass affordable.

    Order matters and was set by counter-example. Shape ran first in an
    earlier version and labelled Maaack/Godot-Game-Template an addon,
    because a template that bundles eight plugins has most of its scripts
    under addons/. What the owner called it beats what the tree looks like.
    """
    sig = rec.get("signals", {}) or {}
    topics = {t.lower() for t in (repo.get("topics") or [])}
    name = repo["repo_id"].split("/")[-1].lower().replace("_", "-")
    desc = (repo.get("description") or "").lower()
    why = {}

    # 1. Declared topics: the owner asserting what this is.
    for stratum, keys in TOPIC_RULES:
        hit = topics & keys
        if hit:
            why["topic"] = sorted(hit)[0]
            return stratum, why, 0.75

    # 2. Repository name. Chosen for the artefact itself, unlike a
    #    description, which often says what the artefact is FOR --
    #    "a tool for making games" would otherwise classify a tool as a game.
    for stratum, rx in NAME_RULES:
        m = re.search(rx, name)
        if m:
            why["name"] = m.group(0)
            return stratum, why, 0.65

    # 3. Shape: an addon repository is one whose own source lives under
    #    addons/. A game that vendors three plugins is not an addon, so
    #    plugin.cfg alone cannot decide it.
    gd, gd_add = sig.get("gd", 0), sig.get("gd_in_addons", 0)
    if gd >= 3 and gd_add / gd >= 0.6:
        why["shape"] = "%d/%d scripts under addons/" % (gd_add, gd)
        return "addon", why, 0.8
    if not rec.get("project_files") and sig.get("plugin_cfg", 0) > 0:
        why["shape"] = "plugin.cfg present, no project.godot"
        return "addon", why, 0.75

    # 4. Description, weakest signal, lowest confidence.
    for stratum, rx in NAME_RULES:
        m = re.search(rx, desc)
        if m:
            why["description"] = m.group(0)
            return stratum, why, 0.4

    # 5. Many scenes and scripts outside addons/ is what a game looks like
    #    when nobody said so.
    if sig.get("scene", 0) - sig.get("scene_in_addons", 0) >= 5 and (gd - gd_add) >= 5:
        why["shape"] = "scenes and scripts outside addons/"
        return "game", why, 0.45
    why["shape"] = "no discriminating signal"
    return "unknown", why, 0.2


def main(min_stars=0, topics_only=False, limit=0):
    repos = {r["repo_id"]: r for r in read_table("repos")}
    if not repos:
        sys.exit("no repos table -- run build_frame.py first")
    tier_file = WORK / "snapshot_tier.txt"
    if tier_file.exists():
        ids = [l.strip() for l in tier_file.read_text().splitlines() if l.strip()]
        rows = [repos[i] for i in ids if i in repos]
        print("  snapshot tier: %d repos from select_tiers.py" % len(rows))
    else:
        rows = tier_s(list(repos.values()), min_stars, topics_only)
        if limit:
            rows = sorted(rows, key=lambda r: -r.get("stars", 0))[:limit]
        print("  snapshot tier: %d of %d repos (min_stars=%s topics_only=%s)"
              % (len(rows), len(repos), min_stars, topics_only))

    ev_ids = run_batches(rows)
    if not ev_ids:
        sys.exit("no batches stored")

    pf_rows, snap_rows, strata_rows, dep_rows, sig_rows = [], [], [], [], []
    stats = dict(seen=0, tree_ok=0, truncated=0, with_project=0, multi_project=0,
                 cv4=0, cv5=0, other_cv=0, no_key=0, fetch_failed=0)
    snap_on = today()
    for eid in ev_ids:
        for line in ev_body(eid).decode("utf-8", "ignore").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            rid = rec["repo_id"]
            repo = repos.get(rid)
            if not repo:
                continue
            stats["seen"] += 1
            if rec.get("error"):
                repo["included"] = False
                repo["exclusion_reason"] = repo.get("exclusion_reason") or rec["error"]
                continue
            stats["tree_ok"] += 1
            stats["truncated"] += bool(rec.get("truncated"))
            pfs = rec.get("project_files") or []
            if not pfs:
                repo["included"] = False
                repo["exclusion_reason"] = "no-project-file"
                continue
            stats["with_project"] += 1
            stats["multi_project"] += rec.get("project_file_count", 0) > 1

            # Persisted because they are the inputs the strata rule is
            # adjudicated against, and because build_validation needs the
            # workflow paths -- the CI pin is the strongest independent
            # version signal there is, and it was previously unreachable.
            sig_rows.append(dict(
                repo_id=rid, engine=ENGINE, tree_sha=rec.get("tree_sha"),
                branch=rec.get("branch"), branch_fallback=bool(rec.get("branch_fallback")),
                blob_count=rec.get("blob_count"), truncated=bool(rec.get("truncated")),
                project_file_count=rec.get("project_file_count"),
                signals=rec.get("signals") or {}, workflow_paths=rec.get("workflows") or [],
                ev_id=eid))
            for aname in rec.get("addons") or []:
                dep_rows.append(dict(repo_id=rid, engine=ENGINE, addon_name=aname,
                                     source="vendored-tree",
                                     truncated=bool(rec.get("addons_truncated")),
                                     ev_id=eid))
            st, why, conf = classify_stratum(repo, rec)
            strata_rows.append(dict(repo_id=rid, engine=ENGINE, stratum=st, signals=why,
                                    method="rule", coder="rule-v0.1", confidence=conf,
                                    ev_id=eid))
            for i, pf in enumerate(pfs):
                p = pf["parse"]
                pf_rows.append(dict(repo_id=rid, engine=ENGINE, path=pf["path"],
                                    is_root=("/" not in pf["path"]), depth=pf["path"].count("/"),
                                    blob_sha=pf.get("blob_sha"), size_bytes=pf.get("size"),
                                    is_primary=(i == 0), ev_id=eid))
                status = p.get("status")
                if status == "fetch-failed":
                    stats["fetch_failed"] += 1
                elif status == "no-version-key":
                    stats["no_key"] += 1
                elif p.get("config_version") == 4:
                    stats["cv4"] += 1
                elif p.get("config_version") == 5:
                    stats["cv5"] += 1
                else:
                    stats["other_cv"] += 1
                snap_rows.append(dict(
                    repo_id=rid, engine=ENGINE, path=pf["path"], snapshot_on=snap_on,
                    commit_sha=rec.get("tree_sha"), config_version=p.get("config_version"),
                    features_raw=p.get("features_raw"), declared_minor=p.get("minor"),
                    renderer=p.get("renderer"),
                    uses_csharp=bool(p.get("uses_csharp")) or bool((rec.get("signals") or {}).get("csproj")),
                    engine_major=p.get("major"), parse_status=status,
                    content_sha256=pf.get("content_sha256"), ev_id=eid))

    write_table("project_files", pf_rows)
    write_table("snapshots", snap_rows)
    write_table("strata", strata_rows)
    write_table("dependencies", dep_rows)
    write_table("repo_signals", sig_rows)
    write_table("repos", list(repos.values()))
    print("  " + json.dumps(stats))

    ev_claim("Of %d repositories probed by recursive tree listing on %s, %d returned a tree, "
             "%d contained at least one project.godot, and %d contained more than one. "
             "Declared versions: %d files on config_version=4 (Godot 3.x), %d on "
             "config_version=5 (Godot 4.x), %d with no version key, %d unfetchable."
             % (stats["seen"], snap_on, stats["tree_ok"], stats["with_project"],
                stats["multi_project"], stats["cv4"], stats["cv5"], stats["no_key"],
                stats["fetch_failed"]),
             "MEASURED", ev_ids[:40],
             caveat="Default-branch trees only. %d trees came back truncated by the API and are "
                    "undercounted. A declared version is a claim by the project, not a "
                    "measurement of the engine in use (C9)." % stats["truncated"])
    return stats


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-stars", type=int, default=0)
    ap.add_argument("--topics-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    main(a.min_stars, a.topics_only, a.limit)
