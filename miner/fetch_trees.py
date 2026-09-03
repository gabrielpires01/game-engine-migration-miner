# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
#!/usr/bin/env python3
"""Pure fetcher: one NDJSON record per repository, emitted to stdout.

Run under `ev.py sh` so the command and its full output land in the
ledger as one artifact per batch. Nothing here writes a dataset table;
build_snapshot.py parses the stored artifact back.

Per repo this makes ONE core-API call (the recursive tree) plus one
unauthenticated raw.githubusercontent fetch per project file found.
raw fetches do not consume the core rate limit, which is what makes the
snapshot affordable at corpus scale.
"""
import argparse, hashlib, json, os, re, subprocess, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engines

UA = "evidence-mining/1.0 (academic research)"
MAX_PROJECT_FILES = 25          # monorepos of demos carry dozens; cap the raw fetches
TOKEN = None


def gh_json(path):
    req = urllib.request.Request("https://api.github.com/" + path,
                                 headers={"User-Agent": UA,
                                          "Accept": "application/vnd.github+json",
                                          "Authorization": "Bearer %s" % TOKEN})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8", "ignore")), r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            hdrs = dict(e.headers or {})
            if e.code in (403, 429) and hdrs.get("x-ratelimit-remaining") == "0":
                reset = int(hdrs.get("x-ratelimit-reset", "0"))
                wait = max(5, min(900, reset - int(time.time()) + 3))
                sys.stderr.write("rate limited, sleeping %ds\n" % wait)
                time.sleep(wait); continue
            return None, e.code, hdrs
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None, 0, {}


def raw(repo, ref, path):
    url = "https://raw.githubusercontent.com/%s/%s/%s" % (repo, ref, urllib.parse.quote(path))
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(1.5 * (attempt + 1))
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


# ---- signals read off the tree, for strata classification and validation ----
SIG = {
    "plugin_cfg":      re.compile(r"(^|/)addons/[^/]+/plugin\.cfg$"),
    "addons_dir":      re.compile(r"(^|/)addons/"),
    "csproj":          re.compile(r"\.csproj$"),
    "export_presets":  re.compile(r"(^|/)export_presets\.cfg$"),
    "workflow":        re.compile(r"^\.github/workflows/.+\.ya?ml$"),
    "dockerfile":      re.compile(r"(^|/)(Dockerfile|devcontainer\.json)$", re.I),
    "readme":          re.compile(r"^readme(\.md|\.rst|\.txt)?$", re.I),
    "test_file":       re.compile(r"(^|/)(tests?|spec)/.*\.(gd|cs)$|(^|/)test_[^/]*\.gd$|_test\.gd$", re.I),
    "gd":              re.compile(r"\.gd$"),
    # Scripts that live under addons/ vs. anywhere. Nearly every game
    # vendors a plugin, so the presence of addons/<x>/plugin.cfg says
    # nothing about what the repository IS; the share of its own source
    # that sits under addons/ does.
    "gd_in_addons":    re.compile(r"(^|/)addons/.*\.gd$"),
    "scene_in_addons": re.compile(r"(^|/)addons/.*\.tscn$"),
    "scene":           re.compile(r"\.tscn$"),
    "resource":        re.compile(r"\.tres$"),
    "shader":          re.compile(r"\.(gdshader|shader)$"),
}
PROJECT_FILE = re.compile(r"(^|/)project\.godot$")
ADDON_DIR = re.compile(r"(?:^|/)addons/([^/]+)/")


def probe(row):
    repo, branch = row["repo_id"], row.get("default_branch") or "HEAD"
    rec = {"repo_id": repo, "branch": branch, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tree, status, _ = gh_json("repos/%s/git/trees/%s?recursive=1" % (repo, branch))
    if (tree is None or "tree" not in tree) and branch != "HEAD":
        # The frame's default_branch can be stale -- a repository renamed
        # master to main after it was enumerated 404s here and would be
        # silently dropped as "no Godot project".
        tree, status, _ = gh_json("repos/%s/git/trees/HEAD?recursive=1" % repo)
        if tree is not None and "tree" in tree:
            rec["branch"] = branch = "HEAD"
            rec["branch_fallback"] = True
    rec["tree_status"] = status
    if tree is None or "tree" not in tree:
        rec["error"] = "tree-unavailable"
        return rec
    rec["tree_sha"] = tree.get("sha")
    rec["truncated"] = bool(tree.get("truncated"))
    entries = [e for e in tree["tree"] if e.get("type") == "blob"]
    rec["blob_count"] = len(entries)

    counts = {k: 0 for k in SIG}
    workflows, project_files, addons = [], [], set()
    for e in entries:
        p = e["path"]
        am = ADDON_DIR.search(p)
        if am:
            addons.add(am.group(1))
        for k, rx in SIG.items():
            if rx.search(p):
                counts[k] += 1
        if SIG["workflow"].search(p) and len(workflows) < 12:
            workflows.append(p)
        if PROJECT_FILE.search(p):
            project_files.append({"path": p, "blob_sha": e.get("sha"), "size": e.get("size")})
    rec["signals"] = counts
    # Vendored addon directory names. The rival explanation "blocked by its
    # dependencies" needs the project's dependencies to be observable at
    # all; addons/<name>/ is the only place Godot records them, and it
    # records a name, not a repository -- so matching to a corpus repo is a
    # name match and must be reported as one.
    rec["addons"] = sorted(addons)[:40]
    rec["addons_truncated"] = len(addons) > 40
    rec["workflows"] = workflows
    rec["project_file_count"] = len(project_files)

    project_files.sort(key=lambda d: (d["path"].count("/"), -(d["size"] or 0), d["path"]))
    eng = engines.get("godot")
    out = []
    for pf in project_files[:MAX_PROJECT_FILES]:
        body = raw(repo, branch, pf["path"])
        d = dict(pf)
        if body is None:
            d["parse"] = {"status": "fetch-failed"}
        else:
            d["content_sha256"] = hashlib.sha256(body).hexdigest()
            d["content_bytes"] = len(body)
            d["parse"] = eng.parse(body.decode("utf-8", "ignore"))
        out.append(d)
    rec["project_files"] = out
    rec["project_files_truncated"] = len(project_files) > MAX_PROJECT_FILES
    return rec


def main():
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", required=True, help="NDJSON or JSON list with repo_id/default_branch")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()

    TOKEN = os.environ.get("GITHUB_TOKEN") or subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
    if not TOKEN:
        sys.exit("no GitHub token")

    txt = open(a.repos, encoding="utf-8").read().strip()
    rows = json.loads(txt) if txt.startswith("[") else [json.loads(l) for l in txt.splitlines() if l.strip()]
    if isinstance(rows, dict):
        rows = [dict(repo_id=k, **v) for k, v in rows.items()]
    rows = rows[a.start:]
    if a.limit:
        rows = rows[:a.limit]

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for rec in ex.map(probe, rows):
            sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    import urllib.parse
    main()
