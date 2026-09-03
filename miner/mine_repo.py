# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
#!/usr/bin/env python3
"""Deep miner: one repository -> one NDJSON record of its full migration
history. Emitted to stdout; run under `ev.py sh` so the command and its
output are recorded.

Method, and why it is not the obvious one:

  For every commit that touched project.godot, across ALL refs, the file
  is READ at that commit and at its first parent and the two declared
  versions compared. Not commit messages, and not diffs either: reading
  does not depend on rename detection and cannot be fooled by a pickaxe
  regex matching a context line.

  The motivating case is Thrive's 3->4 boundary commit 217ef43e, subject
  "Project file upgrades" -- a message search misses it entirely (caveat
  C5). The subject is still recorded, with flags for whether it names the
  engine, a version, or a migration, so the miss rate a message-based
  study would suffer is measured rather than asserted.

The clone is size-filtered (--filter=blob:limit=256k): every text blob in
the whole history arrives in the initial pack, art and audio do not. A
blobless clone would be wrong here -- git issues one network round trip
per object even under `cat-file --batch`, measured at ~0.55s per blob.
"""
import argparse, json, os, re, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engines

# Must name the engine or an explicit move to a major version. An earlier
# version accepted bare `4[._-]\d+` and bare `upgrade`, which matched
# Thrive's sse4_1_disable and better_upgrade_visibility -- branches with
# nothing to do with an engine migration.
#
# Both lines are captured, not just the new one. A repository carrying a
# godot_3 maintenance branch alongside a godot_4 branch is keeping two
# engine versions alive, which is the cost the branches table exists to
# record; a pattern that saw only the new line would miss half of it.
# (?!\d) rather than \b after the captured digit: \b fails on godot_4_7,
# because the character after the 4 is an underscore, which is a word
# character. (?!\d) still rejects godot_45.
BRANCH_PAT = re.compile(
    r"godot[-_ ]?(\d)(?!\d)"
    r"|godot(\d)(?!\d)"
    r"|\b(\d)\.x\b"
    r"|(?:port|migrat\w*|upgrad\w*|move|switch|convert)[-_ ]?(?:to[-_ ]?)?(?:godot[-_ ]?)?(\d)(?!\d)",
    re.I)


def branch_major(name):
    m = BRANCH_PAT.search(name)
    if not m:
        return None, None
    major = next((g for g in m.groups() if g), None)
    if major not in ("2", "3", "4", "5"):
        return None, None
    return major, m.group(0)


NAMES_ENGINE = re.compile(r"godot|engine|redot", re.I)
# "New project.godot format" is godot_heightmap_plugin's real 3->4 boundary
# subject. It names the engine only as part of a FILENAME, and a
# message-based search for "godot" would not surface it as a migration.
# Counting it as naming the engine understates the miss rate -- which is
# the one number this dataset exists to produce -- so filenames are
# stripped before the test.
FILENAME_TOK = re.compile(r"\S*\.(godot|tscn|tres|gd|cfg|import|gdshader)\b", re.I)


def subject_flags(subject):
    probe = FILENAME_TOK.sub(" ", subject or "")
    return (bool(NAMES_ENGINE.search(probe)),
            bool(NAMES_VERSION.search(probe)),
            bool(NAMES_MIGRATION.search(probe)))
NAMES_VERSION = re.compile(r"\b[34](\.\d+)+\b|\bgodot\s*[34]\b|\b4\.x\b|\bv?[34]\.\d", re.I)
NAMES_MIGRATION = re.compile(r"migrat|upgrad|\bport(ed|ing)?\b|convert|bump|update.*version|version.*update", re.I)


def run(cmd, cwd, timeout=600, check=False):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       timeout=timeout, errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError("%s -> %d: %s" % (" ".join(cmd[:4]), p.returncode, p.stderr[:300]))
    return p.stdout


def cat_file_batch(repo_dir, revs):
    """Read many <rev>:<path> blobs in ONE git invocation.

    On a blobless clone this matters more than it looks: git issues a
    single bulk lazy fetch for everything named on stdin, instead of one
    network round trip per commit. Reading the file at every commit that
    touched it -- rather than diffing -- also removes any dependence on
    rename detection or on -G matching context lines.
    """
    if not revs:
        return {}
    p = subprocess.Popen(["git", "cat-file", "--batch"], cwd=repo_dir,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    out, _ = p.communicate(("\n".join(revs) + "\n").encode(), timeout=1800)
    res, pos, i = {}, 0, 0
    while pos < len(out) and i < len(revs):
        nl = out.find(b"\n", pos)
        if nl < 0:
            break
        header = out[pos:nl].decode("utf-8", "replace").split()
        pos = nl + 1
        if len(header) >= 2 and header[-1] in ("missing", "ambiguous"):
            res[revs[i]] = None
            i += 1
            continue
        try:
            size = int(header[2])
        except (IndexError, ValueError):
            res[revs[i]] = None
            i += 1
            continue
        res[revs[i]] = out[pos:pos + size].decode("utf-8", "replace")
        pos += size + 1
        i += 1
    return res


def version_at(text):
    if text is None:
        return None
    p = engines.get("godot").parse(text)
    return (p["config_version"], p["minor"]) if p["status"] in ("ok", "unknown-config-version") else None


def version_events(repo_dir, path, default_shas):
    """Every commit where the declared version differs from its first
    parent's. Reads file content, never commit messages (caveat C5)."""
    fmt = "%H\x02%P\x02%at\x02%ct\x02%s"
    txt = run(["git", "log", "--all", "--date-order", "--format=" + fmt, "--", path],
              repo_dir, timeout=900)
    commits = []
    for line in txt.split("\n"):
        if "\x02" not in line:
            continue
        f = line.split("\x02")
        commits.append(dict(sha=f[0], parents=f[1].split() if f[1] else [],
                            authored=int(f[2]), committed=int(f[3]),
                            subject=f[4] if len(f) > 4 else ""))
    revs = []
    for c in commits:
        revs.append("%s:%s" % (c["sha"], path))
        for par in c["parents"][:1]:
            revs.append("%s:%s" % (par, path))
    blobs = cat_file_batch(repo_dir, list(dict.fromkeys(revs)))

    evs = []
    for c in commits:
        here = version_at(blobs.get("%s:%s" % (c["sha"], path)))
        if here is None:
            continue
        par = c["parents"][0] if c["parents"] else None
        there = version_at(blobs.get("%s:%s" % (par, path))) if par else None
        if here == there:
            continue
        (fcv, fmn) = there if there else (None, None)
        (tcv, tmn) = here
        evs.append(dict(
            commit_sha=c["sha"], parent_sha=par, parents=c["parents"], path=path,
            authored_on=time.strftime("%Y-%m-%d", time.gmtime(c["authored"])),
            committed_on=time.strftime("%Y-%m-%d", time.gmtime(c["committed"])),
            from_config_version=fcv, to_config_version=tcv,
            from_minor=fmn, to_minor=tmn,
            subject=c["subject"][:300],
            **dict(zip(("subject_names_engine", "subject_names_version",
                        "subject_names_migration"), subject_flags(c["subject"]))),
            on_default_branch=c["sha"] in default_shas,
            detection="content-at-commit"))
    evs.sort(key=lambda e: e["authored_on"])
    for e in evs:
        e["event_type"] = classify(e)
    return evs


def classify(e):
    f, t = e["from_config_version"], e["to_config_version"]
    if f is None and t is not None and e["parent_sha"] is None:
        return "initial"
    if f is None and t is not None:
        return "initial"          # file added on this commit
    if f is not None and t is not None and f != t:
        return "major_migration" if t > f else "major_rollback"
    fm, tm = e["from_minor"], e["to_minor"]
    if fm and tm:
        try:
            a = tuple(int(x) for x in fm.split("."))
            b = tuple(int(x) for x in tm.split("."))
        except ValueError:
            return "minor_upgrade"
        if b > a:
            e["minor_distance"] = (b[0] - a[0]) * 100 + (b[1] - a[1])
            return "minor_upgrade"
        return "minor_downgrade"
    if tm and not fm:
        return "initial" if f is None else "minor_upgrade"
    return "unknown"


# --------------------------------------------------------------- SATD / tests
SATD_GD = re.compile(r"#\s*(TODO|FIXME|HACK|XXX|BUG|WORKAROUND|OPTIMIZE)\b", re.I)
SATD_CS = re.compile(r"//\s*(TODO|FIXME|HACK|XXX|BUG|WORKAROUND|OPTIMIZE)\b", re.I)
COMMENT_GD = re.compile(r"^\s*#\s?(.*)$")
COMMENT_CS = re.compile(r"^\s*//\s?(.*)$")
# A commented line counts as commented-out CODE only if what follows the
# marker parses as a statement, not as prose. Deliberately conservative:
# a doc comment that happens to contain "=" should not inflate the count.
CODE_ISH = re.compile(r"^\s*(func |var |const |if |elif |else|for |while |return\b|"
                      r"print\(|emit_signal|extends |class_name |@?export|"
                      r"public |private |void |int |float |string |new )|"
                      r"^[\w\.\[\]]+\s*[:+\-*/]?=\s*\S|"
                      r"^[\w\.]+\(.*\)\s*;?\s*$", re.I)
TEST_PATH = re.compile(r"(^|/)(tests?|spec)/|(^|/)test_[^/]*\.(gd|cs)$|_test\.(gd|cs)$", re.I)
TEST_CASE = re.compile(r"^\s*func\s+test_|\[Test\]|\[Fact\]|\[TestCase", re.I | re.M)
TEST_SKIP = re.compile(r"\[Ignore\]|\[Skip|pending\s*\(|\.skip\s*\(|"
                       r"^\s*#\s*func\s+test_|SkipTest|@ignore", re.I | re.M)


def satd_at(repo_dir, sha):
    """Counts over .gd and .cs at one tree. One bulk blob read."""
    txt = run(["git", "ls-tree", "-r", sha], repo_dir, timeout=300)
    paths = []
    for line in txt.split("\n"):
        if "\t" not in line:
            continue
        meta, name = line.split("\t", 1)
        parts = meta.split()
        if len(parts) < 3 or parts[1] != "blob":
            continue
        if name.lower().endswith((".gd", ".cs")):
            paths.append((name, parts[2]))
    out = dict(tree_sha=sha, todo=0, fixme=0, hack=0, xxx=0, satd_total=0,
               commented_code_lines=0, test_files=0, test_cases=0, skipped_tests=0,
               loc_gd=0, loc_cs=0, loc_total=0, files_gd=0, files_cs=0)
    if not paths:
        return out
    blobs = cat_file_batch(repo_dir, [b for _, b in paths[:8000]])
    for name, blob in paths[:8000]:
        body = blobs.get(blob)
        if body is None:
            continue
        is_cs = name.lower().endswith(".cs")
        lines = body.split("\n")
        n = len(lines)
        out["loc_total"] += n
        if is_cs:
            out["loc_cs"] += n; out["files_cs"] += 1
        else:
            out["loc_gd"] += n; out["files_gd"] += 1
        rx = SATD_CS if is_cs else SATD_GD
        for m in rx.finditer(body):
            kind = m.group(1).lower()
            out["satd_total"] += 1
            if kind in ("todo", "fixme", "hack", "xxx"):
                out[kind] += 1
        crx = COMMENT_CS if is_cs else COMMENT_GD
        for line in lines:
            cm = crx.match(line)
            if cm and CODE_ISH.match(cm.group(1) or ""):
                out["commented_code_lines"] += 1
        if TEST_PATH.search(name):
            out["test_files"] += 1
            out["test_cases"] += len(TEST_CASE.findall(body))
            out["skipped_tests"] += len(TEST_SKIP.findall(body))
    out["files_scanned"] = min(len(paths), 8000)
    out["files_truncated"] = len(paths) > 8000
    return out


def churn(repo_dir, sha, merge_aware=True):
    args = ["git", "show", "--numstat", "--format=", sha]
    if merge_aware:
        args.insert(2, "--first-parent")
    txt = run(args, repo_dir, timeout=300)
    ins = dele = files = 0
    by_ext = {}
    for line in txt.strip().split("\n"):
        if not line.strip():
            continue
        p = line.split("\t")
        if len(p) < 3:
            continue
        a, d, name = p[0], p[1], p[2]
        files += 1
        ai = int(a) if a.isdigit() else 0
        di = int(d) if d.isdigit() else 0
        ins += ai; dele += di
        ext = os.path.splitext(name)[1].lower() or "<none>"
        e = by_ext.setdefault(ext, {"files": 0, "ins": 0, "del": 0})
        e["files"] += 1; e["ins"] += ai; e["del"] += di
    return dict(files_changed=files, insertions=ins, deletions=dele, by_ext=by_ext)


def tree_size(repo_dir, sha):
    txt = run(["git", "ls-tree", "-r", "-l", sha], repo_dir, timeout=300)
    n = total = gd = scene = res = 0
    for line in txt.split("\n"):
        if not line.strip():
            continue
        try:
            meta, name = line.split("\t", 1)
            size = meta.split()[3]
        except (ValueError, IndexError):
            continue
        n += 1
        if size.isdigit():
            total += int(size)
        low = name.lower()
        gd += low.endswith(".gd"); scene += low.endswith(".tscn"); res += low.endswith(".tres")
    return dict(files=n, bytes=total, gd_files=gd, scene_files=scene, resource_files=res)


def tree_manifest(repo_dir, sha, out_path):
    txt = run(["git", "ls-tree", "-r", "-l", sha], repo_dir, timeout=300)
    n = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for line in txt.split("\n"):
            if not line.strip():
                continue
            try:
                meta, name = line.split("\t", 1)
                mode, typ, blob, size = meta.split()
            except ValueError:
                continue
            f.write(json.dumps({"path": name, "blob": blob, "size": int(size) if size.isdigit() else None,
                                "mode": mode}, ensure_ascii=False) + "\n")
            n += 1
    return n


# A burst walks backwards from the boundary while commits keep arriving.
# On a repository with continuous daily activity there is never a gap, so
# it must also stop on span and count -- otherwise the window swallows
# months of unrelated work and the churn measure becomes meaningless.
# When a bound is hit the row is marked saturated so those windows can be
# excluded rather than silently pooled with clean ones.
MAX_GAP_DAYS = 14
MAX_SPAN_DAYS = 60
MAX_BURST_COMMITS = 150


def window(repo_dir, ev):
    """Bound the port work around a boundary commit."""
    sha, parents = ev["commit_sha"], ev.get("parents") or []
    if len(parents) >= 2:
        txt = run(["git", "log", "--format=%H\x02%at", "%s..%s" % (parents[0], parents[1])],
                  repo_dir, timeout=300).strip()
        rows = [l.split("\x02") for l in txt.split("\n") if l.strip()]
        if rows:
            ts = sorted(int(r[1]) for r in rows)
            return dict(window_rule="branch-merge", commits=len(rows), saturated=None,
                        start_sha=rows[-1][0], end_sha=rows[0][0],
                        start_on=time.strftime("%Y-%m-%d", time.gmtime(ts[0])),
                        end_on=time.strftime("%Y-%m-%d", time.gmtime(ts[-1])),
                        span_days=int((ts[-1] - ts[0]) / 86400))
    # commit-burst: walk out along first-parent while gaps stay under 14 days
    txt = run(["git", "log", "--first-parent", "--format=%H\x02%at", "-n", "400", sha],
              repo_dir, timeout=300).strip()
    rows = [(r.split("\x02")[0], int(r.split("\x02")[1]))
            for r in txt.split("\n") if "\x02" in r]
    if not rows:
        return dict(window_rule="single-commit", commits=1, start_sha=sha, end_sha=sha,
                    start_on=ev["authored_on"], end_on=ev["authored_on"], span_days=0,
                    saturated=None)
    kept, saturated = [rows[0]], None
    for prev, cur in zip(rows, rows[1:]):
        if prev[1] - cur[1] > MAX_GAP_DAYS * 86400:
            break
        if rows[0][1] - cur[1] > MAX_SPAN_DAYS * 86400:
            saturated = "span"; break
        if len(kept) >= MAX_BURST_COMMITS:
            saturated = "commits"; break
        kept.append(cur)
    ts = sorted(t for _, t in kept)
    return dict(window_rule="commit-burst" if len(kept) > 1 else "single-commit",
                commits=len(kept), start_sha=kept[-1][0], end_sha=kept[0][0],
                start_on=time.strftime("%Y-%m-%d", time.gmtime(ts[0])),
                end_on=time.strftime("%Y-%m-%d", time.gmtime(ts[-1])),
                span_days=int((ts[-1] - ts[0]) / 86400),
                saturated=saturated)


def branches(repo_dir, default_branch):
    txt = run(["git", "for-each-ref", "--format=%(refname:short)\x02%(committerdate:short)",
               "refs/remotes/origin"], repo_dir, timeout=300)
    out = []
    for line in txt.strip().split("\n"):
        if "\x02" not in line:
            continue
        ref, last = line.split("\x02", 1)
        name = ref.split("/", 1)[1] if "/" in ref else ref
        if name in ("HEAD", "origin", default_branch):
            continue
        major, matched = branch_major(name)
        if not major:
            continue
        base = "origin/" + default_branch
        cnt = run(["git", "rev-list", "--count", "%s..%s" % (base, ref)], repo_dir, timeout=300).strip()
        behind = run(["git", "rev-list", "--count", "%s..%s" % (ref, base)], repo_dir, timeout=300).strip()
        # No -n 1 here: git applies the limit BEFORE reversing, so
        # `-n 1 --reverse` returns the newest commit, not the oldest, and
        # first_on would collapse onto last_on with a zero lifetime.
        flog = run(["git", "log", "--format=%ad", "--date=short", "--reverse",
                    "%s..%s" % (base, ref)], repo_dir, timeout=300).strip()
        first = flog.split("\n")[0] if flog else ""
        merged = run(["git", "branch", "-r", "--merged", base, "--list", ref],
                     repo_dir, timeout=300).strip() != ""
        # The cost of carrying two engine versions in parallel is the work
        # done twice. `git cherry` marks '-' for a branch commit whose patch
        # already has an equivalent on the base branch: that is duplicated
        # effort, measured rather than inferred from branch lifetime.
        dup = uniq = None
        try:
            ch = run(["git", "cherry", base, ref], repo_dir, timeout=300)
            marks = [l[:1] for l in ch.split("\n") if l[:1] in ("+", "-")]
            dup, uniq = marks.count("-"), marks.count("+")
        except Exception:
            pass
        out.append(dict(branch=name, pattern=matched, line="%s.x" % major,
                        last_on=last, first_on=first or None,
                        commits=int(cnt) if cnt.isdigit() else None,
                        behind=int(behind) if behind.isdigit() else None,
                        duplicated_commits=dup, unique_commits=uniq,
                        merged=merged))
    return out


def archive_tree(repo_dir, sha, out_path):
    """Full tree tarball. Requires an unfiltered clone: a size-filtered one
    is missing exactly the art and audio a conversion run needs."""
    with open(out_path, "wb") as f:
        p1 = subprocess.Popen(["git", "archive", "--format=tar", sha],
                              cwd=repo_dir, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        p2 = subprocess.Popen(["zstd", "-q", "-3", "-o", "/dev/stdout"],
                              stdin=p1.stdout, stdout=f, stderr=subprocess.DEVNULL)
        p1.stdout.close()
        p2.communicate(timeout=1800)
    return os.path.getsize(out_path)


def mine(repo_id, workdir, keep_trees=False, tree_dir=None, want_satd=False,
         full_clone=False, archive=False):
    rec = {"repo_id": repo_id, "mined_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    workdir = os.path.abspath(workdir)
    d = os.path.join(workdir, repo_id.replace("/", "__"))
    if os.path.exists(d):
        shutil.rmtree(d)
    url = "https://github.com/%s.git" % repo_id
    try:
        # blob:limit, NOT blob:none. A blobless clone defers every file
        # body to a lazy fetch, and git issues one network round trip per
        # object even under `cat-file --batch` -- measured at ~0.55s per
        # blob, which makes reading project.godot at every commit
        # unaffordable. A 256k size filter brings the entire text history
        # down in the initial pack (source, scenes, configs) while still
        # leaving art and audio behind. Measured on one repository: 7.2s
        # of lazy fetches becomes 0.004s of local reads.
        clone = ["git", "clone", "--no-checkout", "--quiet"]
        if not full_clone:
            clone.insert(2, "--filter=blob:limit=256k")
        run(clone + [url, d], workdir, timeout=3600, check=True)
    except Exception as e:
        rec["error"] = "clone-failed: %s" % str(e)[:200]
        return rec
    try:
        default_branch = run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                             d, timeout=60).strip().split("/", 1)[-1] or "master"
        rec["default_branch"] = default_branch
        # The migration branch sometimes wins outright: godot-aseprite-wizard
        # renamed its default to godot_4 and left master on the old line. A
        # branch inventory that skips the default branch misses that entirely.
        dmaj, dmatch = branch_major(default_branch)
        rec["default_branch_line"] = ("%s.x" % dmaj) if dmaj else None
        rec["default_branch_names_version"] = dmatch
        head = run(["git", "rev-parse", "origin/" + default_branch], d, timeout=60).strip()
        rec["head_sha"] = head
        rec["commits_total"] = int(run(["git", "rev-list", "--count", "--all"], d, timeout=300).strip() or 0)
        rec["commits_default"] = int(run(["git", "rev-list", "--count", head], d, timeout=300).strip() or 0)
        first_last = run(["git", "log", "--format=%ad", "--date=short", head], d, timeout=300).strip().split("\n")
        rec["first_commit_on"] = first_last[-1] if first_last and first_last[0] else None
        rec["last_commit_on"] = first_last[0] if first_last and first_last[0] else None
        rec["authors_total"] = len(set(run(["git", "log", "--format=%aE", "--all"], d, timeout=300).strip().split("\n")))

        default_shas = set(run(["git", "rev-list", head], d, timeout=300).split())
        paths = sorted({p for p in run(["git", "log", "--all", "--format=", "--name-only",
                                        "--", "*project.godot"], d, timeout=900).split("\n")
                        if p.strip().endswith("project.godot")})
        rec["project_paths"] = paths
        rec["events"] = []
        for p in paths[:25]:
            rec["events"] += version_events(d, p, default_shas)
        rec["events"].sort(key=lambda e: e["authored_on"])

        rec["branches"] = branches(d, default_branch)

        rec["windows"], rec["churn"], rec["trees"] = [], [], []
        for ev in rec["events"]:
            if ev["event_type"] not in ("major_migration", "major_rollback"):
                continue
            w = window(d, ev); w["boundary_sha"] = ev["commit_sha"]
            rec["windows"].append(w)
            c = churn(d, ev["commit_sha"]); c.update(boundary_sha=ev["commit_sha"], scope="boundary-commit")
            rec["churn"].append(c)
            if w["commits"] > 1:
                cw = churn_range(d, w["start_sha"], w["end_sha"])
                cw.update(boundary_sha=ev["commit_sha"], scope="window")
                wpre = tree_size(d, parent_or_empty(d, w["start_sha"]))
                cw.update(pre_files=wpre["files"], pre_bytes=wpre["bytes"],
                          pre_gd_files=wpre["gd_files"], pre_scene_files=wpre["scene_files"])
                rec["churn"].append(cw)
            par = ev["parent_sha"]
            if par:
                pre, post = tree_size(d, par), tree_size(d, ev["commit_sha"])
                rec["trees"].append(dict(boundary_sha=ev["commit_sha"], pre_sha=par,
                                         pre=pre, post=post))
                if want_satd:
                    # Across the window, not the boundary commit: the port
                    # lands in the commits around the version flip, so a
                    # boundary-only delta reads as "nothing changed".
                    pre_ref = "%s^" % w["start_sha"] if w["commits"] > 1 else par
                    post_ref = w["end_sha"] if w["commits"] > 1 else ev["commit_sha"]
                    scope = "window" if w["commits"] > 1 else "boundary-commit"
                    try:
                        rec.setdefault("satd", []).append(
                            dict(boundary_sha=ev["commit_sha"], side="pre", scope=scope,
                                 **satd_at(d, pre_ref)))
                        rec.setdefault("satd", []).append(
                            dict(boundary_sha=ev["commit_sha"], side="post", scope=scope,
                                 **satd_at(d, post_ref)))
                    except Exception as se:
                        rec.setdefault("satd_errors", []).append(
                            dict(boundary_sha=ev["commit_sha"], error=str(se)[:200]))
                if keep_trees and tree_dir:
                    td = os.path.join(tree_dir, repo_id.replace("/", "__"), ev["commit_sha"])
                    os.makedirs(td, exist_ok=True)
                    npre = tree_manifest(d, par, os.path.join(td, "pre.manifest.jsonl"))
                    npost = tree_manifest(d, ev["commit_sha"], os.path.join(td, "post.manifest.jsonl"))
                    rec["trees"][-1].update(pre_manifest_rows=npre, post_manifest_rows=npost,
                                            manifest_dir=os.path.relpath(td, tree_dir))
                    if archive and full_clone:
                        try:
                            rec["trees"][-1].update(
                                pre_tar_bytes=archive_tree(d, par, os.path.join(td, "pre.tar.zst")),
                                post_tar_bytes=archive_tree(d, ev["commit_sha"],
                                                            os.path.join(td, "post.tar.zst")))
                        except Exception as ae:
                            rec["trees"][-1]["archive_error"] = str(ae)[:200]
    except Exception as e:
        rec["error"] = "mine-failed: %s: %s" % (type(e).__name__, str(e)[:300])
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return rec


def parent_or_empty(repo_dir, sha):
    """The commit's first parent, or git's empty-tree hash for a root commit,
    which has no parent and would make `<sha>^` an invalid revision."""
    out = run(["git", "rev-parse", "--verify", "--quiet", "%s^" % sha], repo_dir, timeout=60).strip()
    return out or "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def churn_range(repo_dir, start_sha, end_sha):
    base = parent_or_empty(repo_dir, start_sha)
    txt = run(["git", "diff", "--numstat", base, end_sha], repo_dir, timeout=300)
    ins = dele = files = 0
    by_ext = {}
    for line in txt.strip().split("\n"):
        p = line.split("\t")
        if len(p) < 3:
            continue
        files += 1
        ai = int(p[0]) if p[0].isdigit() else 0
        di = int(p[1]) if p[1].isdigit() else 0
        ins += ai; dele += di
        ext = os.path.splitext(p[2])[1].lower() or "<none>"
        e = by_ext.setdefault(ext, {"files": 0, "ins": 0, "del": 0})
        e["files"] += 1; e["ins"] += ai; e["del"] += di
    return dict(files_changed=files, insertions=ins, deletions=dele, by_ext=by_ext)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", help="file with one repo_id per line")
    ap.add_argument("--repo", action="append", default=[])
    ap.add_argument("--workdir", default="/tmp/gem-work")
    ap.add_argument("--tree-dir")
    ap.add_argument("--keep-trees", action="store_true")
    ap.add_argument("--satd", action="store_true")
    ap.add_argument("--full-clone", action="store_true",
                    help="no size filter -- needed for --archive")
    ap.add_argument("--workers", type=int, default=4,
                    help="repositories mined concurrently; clones are network-bound")
    ap.add_argument("--archive", action="store_true",
                    help="write pre/post tar.zst for differential analysis")
    a = ap.parse_args()
    ids = list(a.repo)
    if a.repos:
        ids += [l.strip() for l in open(a.repos) if l.strip() and not l.startswith("#")]
    os.makedirs(a.workdir, exist_ok=True)
    lock = threading.Lock()

    def one(rid):
        try:
            rec = mine(rid, a.workdir, a.keep_trees, a.tree_dir, a.satd,
                       a.full_clone, a.archive)
        except Exception as e:
            rec = {"repo_id": rid, "error": "uncaught: %s: %s" % (type(e).__name__, str(e)[:200])}
        # One writer at a time: a partially written NDJSON line is
        # indistinguishable from a corrupt artifact downstream.
        with lock:
            sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    if a.workers <= 1:
        for rid in ids:
            one(rid)
    else:
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            list(ex.map(one, ids))


if __name__ == "__main__":
    main()
