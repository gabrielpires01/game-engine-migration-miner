# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
#!/usr/bin/env python3
"""Blocked-demand fetcher: issue-tracker evidence that a lagging project
is or is not being held back.

Lag alone is a version number. Lag with documented blocked demand is
debt. This gathers the candidate evidence for both directions -- issues
asking for the new version AND maintainers saying the old one is fine --
because a collector that could only find demand would make the negative
thesis untestable by construction.

Human-subject data: author handles are captured here so provenance is
re-checkable in the ledger, and stripped by build_demand.py before the
row reaches the released dataset. Quoting any individual needs UFRJ CEP
clearance; aggregate counts do not (caveat C7).
Emits NDJSON; run under `ev.py sh`.
"""
import argparse, json, os, subprocess, sys, time, urllib.parse, urllib.request, urllib.error

UA = "evidence-mining/1.0 (academic research)"
TOKEN = None


def api(path, retries=4):
    req = urllib.request.Request("https://api.github.com/" + path,
                                 headers={"User-Agent": UA,
                                          "Accept": "application/vnd.github+json",
                                          "Authorization": "Bearer %s" % TOKEN})
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8", "ignore")), None
        except urllib.error.HTTPError as e:
            h = dict(e.headers or {})
            if e.code in (403, 429):
                reset = int(h.get("x-ratelimit-reset", "0") or 0)
                wait = max(10, min(120, reset - int(time.time()) + 2)) if reset else 25
                sys.stderr.write("throttled, sleeping %ds\n" % wait)
                time.sleep(wait); continue
            return None, "http-%d" % e.code
        except Exception as e:
            time.sleep(3 * (i + 1))
    return None, "exhausted"


def probe(repo):
    rec = {"repo_id": repo, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "queries": [], "items": []}
    for q in ('repo:%s "godot 4"' % repo,
              'repo:%s godot4 in:title,body' % repo):
        d, err = api("search/issues?q=%s&per_page=50&sort=created&order=asc"
                     % urllib.parse.quote(q, safe=":"))
        rec["queries"].append({"q": q, "error": err,
                               "total": (d or {}).get("total_count"),
                               "incomplete": (d or {}).get("incomplete_results")})
        if not d:
            continue
        for it in d.get("items", []):
            if any(x["number"] == it["number"] for x in rec["items"]):
                continue
            rec["items"].append({
                "number": it["number"], "url": it["html_url"],
                "is_pr": "pull_request" in it,
                "title": (it.get("title") or "")[:300],
                "body": (it.get("body") or "")[:1500],
                "state": it.get("state"),
                "created_at": (it.get("created_at") or "")[:10],
                "closed_at": (it.get("closed_at") or "")[:10] if it.get("closed_at") else None,
                "comments": it.get("comments"),
                "author_handle": ((it.get("user") or {}).get("login")),
                "author_association": it.get("author_association"),
                "labels": [l["name"] for l in (it.get("labels") or [])][:8],
            })
        time.sleep(2.2)                 # search API: 30 req/min
    return rec


def main():
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", required=True)
    a = ap.parse_args()
    TOKEN = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
    ids = [l.strip() for l in open(a.repos) if l.strip()]
    for rid in ids:
        sys.stdout.write(json.dumps(probe(rid), ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
