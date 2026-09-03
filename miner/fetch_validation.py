# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
#!/usr/bin/env python3
"""Validation-study fetcher: independent version signals per repository.

A project.godot version is a claim the project makes about itself. This
pulls the signals that can contradict it -- CI pins, container tags,
export presets, README badges -- so the agreement rate can be reported
rather than assumed (caveat C9). Emits NDJSON; run under `ev.py sh`.
"""
import argparse, json, os, re, subprocess, sys, time, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

UA = "evidence-mining/1.0 (academic research)"
TOKEN = None

VER = r"(\d+\.\d+(?:\.\d+)?)"
PATTERNS = [
    ("ci_env",        re.compile(r"GODOT[_-]?VERSION\s*[:=]\s*[\"']?v?" + VER, re.I)),
    ("ci_action",     re.compile(r"godot[_-]version\s*:\s*[\"']?v?" + VER, re.I)),
    ("container_tag", re.compile(r"(?:godot[\w.-]*ci|godot[\w.-]*engine|godot)\s*:\s*v?" + VER + r"[\w.-]*", re.I)),
    ("download_url",  re.compile(r"godot[_-]v?" + VER + r"[-_.]?(?:stable|beta|rc|dev)", re.I)),
    ("badge",         re.compile(r"[Gg]odot[-_ ]v?" + VER + r"[-_ ]?(?:blue|green|red|orange|informational|brightgreen)", re.I)),
    ("prose",         re.compile(r"\b[Gg]odot\s+(?:Engine\s+)?v?" + VER, re.I)),
    ("preset_ver",    re.compile(r"godot[\w./-]*" + VER, re.I)),
]
FILE_KINDS = {
    "workflow":       ["ci_env", "ci_action", "container_tag", "download_url"],
    "gitlab_ci":      ["ci_env", "ci_action", "container_tag", "download_url"],
    "dockerfile":     ["container_tag", "download_url", "ci_env"],
    "readme":         ["badge", "prose", "download_url"],
    "export_presets": ["preset_ver"],
}


def raw(repo, ref, path):
    url = "https://raw.githubusercontent.com/%s/%s/%s" % (repo, ref, urllib.parse.quote(path))
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}),
                                    timeout=30) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception:
        return None


def hints(text, kinds):
    out = []
    for name, rx in PATTERNS:
        if name not in kinds:
            continue
        for m in rx.finditer(text or ""):
            v = m.group(1)
            if not re.match(r"^[1-4]\.\d", v):        # engine majors that exist
                continue
            out.append({"pattern": name, "version": v,
                        "context": text[max(0, m.start() - 60):m.end() + 40].replace("\n", " ")[:160]})
            if len(out) >= 8:
                return out
    return out


def probe(row):
    repo = row["repo_id"]
    ref = row.get("default_branch") or "HEAD"
    rec = {"repo_id": repo, "branch": ref,
           "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "declared": row.get("declared"), "signals": []}
    targets = [(p, "workflow") for p in (row.get("workflows") or [])[:6]]
    targets += [(".gitlab-ci.yml", "gitlab_ci"), ("Dockerfile", "dockerfile"),
                ("README.md", "readme"), ("readme.md", "readme"), ("README", "readme"),
                ("export_presets.cfg", "export_presets")]
    seen_readme = False
    for path, kind in targets:
        if kind == "readme" and seen_readme:
            continue
        body = raw(repo, ref, path)
        if body is None:
            continue
        if kind == "readme":
            seen_readme = True
        h = hints(body[:200000], FILE_KINDS[kind])
        if h:
            rec["signals"].append({"path": path, "kind": kind, "hints": h})
    return rec


def main():
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", required=True)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    TOKEN = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
    rows = [json.loads(l) for l in open(a.repos, encoding="utf-8") if l.strip()]
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for rec in ex.map(probe, rows):
            sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
