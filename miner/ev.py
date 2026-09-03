#!/usr/bin/env python3
"""ev - evidence ledger for empirical research.

Every fact that will appear in a paper passes through here. Fetch, store,
hash, record. Claims bind to stored artifacts by id. Anything not in the
ledger is not citable and must be written as [DATA?].

Layout (default root: ./evidence):
    ledger.jsonl   one record per stored artifact
    claims.jsonl   one record per claim, bound to artifact ids
    raw/<id>.<ext> stored bodies, content-addressed
    inbox/<slug>   drop files for human-supplied content
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time
import urllib.request, urllib.error

UA = "Mozilla/5.0 (X11; Linux x86_64) evidence-mining/1.0 (academic research)"
TIERS = ("MEASURED", "QUOTED", "DERIVED", "REPORTED", "UNVERIFIED")
REASONS = ("login-required", "403-bot-block", "js-only", "rate-limited",
           "paywall", "deleted-404", "robots", "timeout", "other")
WALL_MARKERS = (
    "sign in to continue", "log in to continue", "you must be logged in",
    "please enable javascript", "enable javascript and cookies",
    "captcha", "cf-browser-verification", "just a moment...",
    "access denied", "create an account to continue", "attention required!",
    "rate limit exceeded", "api rate limit",
)

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def root(args):
    r = args.root or os.environ.get("EV_ROOT") or os.path.join(os.getcwd(), "evidence")
    for sub in ("", "raw", "inbox"):
        os.makedirs(os.path.join(r, sub), exist_ok=True)
    return r

def append(path, rec):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out

def digest(data):
    return hashlib.sha256(data).hexdigest()

def ext_for(ctype, url):
    ctype = (ctype or "").lower()
    if "json" in ctype: return "json"
    if "html" in ctype: return "html"
    if "pdf" in ctype: return "pdf"
    if "xml" in ctype: return "xml"
    if "csv" in ctype: return "csv"
    m = re.search(r"\.([a-z0-9]{1,5})(?:\?|$)", (url or "").lower())
    return m.group(1) if m else "txt"

def sniff_wall(data):
    try:
        head = data[:20000].decode("utf-8", "ignore").lower()
    except Exception:
        return []
    return [m for m in WALL_MARKERS if m in head]

def store(r, data, meta):
    """Content-address the body, write it, append a ledger record. Returns rec."""
    h = digest(data)
    eid = h[:12]
    ext = meta.pop("ext", "txt")
    rel = os.path.join("raw", "%s.%s" % (eid, ext))
    path = os.path.join(r, rel)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(data)
    rec = {"id": eid, "ts": now(), "sha256": h, "bytes": len(data), "file": rel}
    rec.update(meta)
    wall = sniff_wall(data)
    if wall:
        rec["wall_markers"] = wall
    append(os.path.join(r, "ledger.jsonl"), rec)
    return rec

def report(rec, r):
    print("stored %s  %d bytes  %s" % (rec["id"], rec["bytes"], rec["file"]))
    if rec.get("wall_markers"):
        print("!! ACCESS-WALL MARKERS IN BODY: %s" % ", ".join(rec["wall_markers"]))
        print("!! This artifact is probably NOT the content you asked for.")
        print("!! Read %s before recording any claim from it." %
              os.path.join(r, rec["file"]))
    if rec.get("incomplete_results"):
        print("!! GitHub returned incomplete_results=true - the count is PARTIAL.")
    return rec

# ---------------------------------------------------------------- commands

def cmd_fetch(args):
    r = root(args)
    req = urllib.request.Request(args.url, headers={
        "User-Agent": UA, "Accept": args.accept})
    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as resp:
            data = resp.read()
            meta = {"kind": "fetch", "url": args.url, "status": resp.status,
                    "ctype": resp.headers.get("Content-Type", ""),
                    "final_url": resp.geturl(),
                    "ext": ext_for(resp.headers.get("Content-Type"), args.url)}
    except urllib.error.HTTPError as e:
        body = e.read()
        meta = {"kind": "fetch", "url": args.url, "status": e.code,
                "ctype": e.headers.get("Content-Type", "") if e.headers else "",
                "ext": "html", "http_error": True}
        rec = store(r, body, dict(meta, tag=args.tag, note=args.note))
        report(rec, r)
        print("!! HTTP %d - treat as BLOCKED, not as absence of content." % e.code)
        print("!! Next: ev.py blocked %s --reason %s" %
              (args.url, "403-bot-block" if e.code in (401, 403) else "other"))
        return 1
    except Exception as e:
        print("!! FETCH FAILED (%s): %s" % (type(e).__name__, e))
        print("!! Next: ev.py blocked %s --reason other" % args.url)
        return 1
    if args.tag: meta["tag"] = args.tag
    if args.note: meta["note"] = args.note
    rec = store(r, data, meta)
    report(rec, r)
    return 0

def cmd_gh(args):
    r = root(args)
    argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if not argv:
        print("!! no gh arguments. Usage: ev.py gh [--tag T] -- api ...")
        return 1
    cmd = ["gh"] + argv
    p = subprocess.run(cmd, capture_output=True, timeout=args.timeout)
    data = p.stdout
    meta = {"kind": "gh", "cmd": " ".join(cmd), "rc": p.returncode, "ext": "json"}
    if p.returncode != 0:
        print("!! gh exited %d" % p.returncode)
        print(p.stderr.decode("utf-8", "ignore")[:2000])
        print("!! No ledger entry written. Do not report a number for this query.")
        return 1
    try:
        parsed = json.loads(data)
        if isinstance(parsed, dict):
            if parsed.get("incomplete_results"):
                meta["incomplete_results"] = True
            if "total_count" in parsed:
                meta["total_count"] = parsed["total_count"]
    except Exception:
        meta["ext"] = "txt"
    if args.tag: meta["tag"] = args.tag
    if args.note: meta["note"] = args.note
    rec = store(r, data, meta)
    report(rec, r)
    if "total_count" in rec:
        print("total_count = %s   (GitHub search counts are APPROXIMATE, include "
              "forks, and drift between runs - restate that wherever cited)"
              % rec["total_count"])
    return 0

def cmd_sh(args):
    r = root(args)
    p = subprocess.run(args.cmd, shell=True, capture_output=True,
                       timeout=args.timeout, cwd=args.cwd or None)
    data = p.stdout
    meta = {"kind": "sh", "cmd": args.cmd, "cwd": args.cwd or os.getcwd(),
            "rc": p.returncode, "ext": "txt"}
    if p.returncode != 0:
        print("!! command exited %d" % p.returncode)
        print(p.stderr.decode("utf-8", "ignore")[:2000])
        if not args.keep_failed:
            print("!! No ledger entry written (use --keep-failed to record anyway).")
            return 1
    if args.tag: meta["tag"] = args.tag
    if args.note: meta["note"] = args.note
    rec = store(r, data, meta)
    report(rec, r)
    return 0

def cmd_blocked(args):
    r = root(args)
    slug = re.sub(r"[^a-z0-9]+", "-", args.url.lower())[:60].strip("-")
    inbox = os.path.join(r, "inbox", slug + ".md")
    rec = {"id": "blocked-" + digest(args.url.encode())[:8], "ts": now(),
           "kind": "blocked", "url": args.url, "reason": args.reason,
           "need": args.need or "", "inbox": os.path.relpath(inbox, r),
           "resolved": False}
    append(os.path.join(r, "ledger.jsonl"), rec)
    if not os.path.exists(inbox):
        with open(inbox, "w", encoding="utf-8") as f:
            f.write("<!-- PASTE THE PAGE CONTENT BELOW THIS LINE -->\n"
                    "<!-- url:    %s -->\n"
                    "<!-- reason: %s -->\n"
                    "<!-- needed: %s -->\n"
                    "<!-- fetched-by: HUMAN. Record the date you copied it. -->\n\n"
                    % (args.url, args.reason, args.need or "(unspecified)"))
    print()
    print("=" * 66)
    print("HUMAN FETCH REQUEST - I cannot read this source")
    print("=" * 66)
    print("URL     : %s" % args.url)
    print("Reason  : %s" % args.reason)
    print("I need  : %s" % (args.need or "the full page content"))
    print("Paste to: %s" % inbox)
    print()
    print("Open the URL in your logged-in browser, copy the content into that")
    print("file, and tell me. Until it exists, everything depending on this")
    print("source stays [DATA?] - I will not fill the gap from memory.")
    print("=" * 66)
    return 0

def cmd_adopt(args):
    r = root(args)
    with open(args.path, "rb") as f:
        data = f.read()
    if not data.strip():
        print("!! %s is empty - nothing adopted." % args.path)
        return 1
    ext = os.path.splitext(args.path)[1].lstrip(".") or "txt"
    meta = {"kind": "human", "url": args.url, "src_path": os.path.abspath(args.path),
            "ext": ext, "collected_by": "human",
            "collected_on": args.collected or "[DATA?]"}
    if args.tag: meta["tag"] = args.tag
    if args.note: meta["note"] = args.note
    rec = store(r, data, meta)
    report(rec, r)
    print("provenance = HUMAN-SUPPLIED. Cannot be re-verified by re-fetching.")
    print("Claims from it must say so, and must not be tier MEASURED unless you")
    print("computed them yourself from this stored text.")
    for e in read_jsonl(os.path.join(r, "ledger.jsonl")):
        if e.get("kind") == "blocked" and e.get("url") == args.url and not e.get("resolved"):
            append(os.path.join(r, "ledger.jsonl"),
                   {"id": e["id"], "ts": now(), "kind": "blocked-resolved",
                    "url": args.url, "resolved_by": rec["id"]})
            print("resolved blocked entry %s" % e["id"])
            break
    return 0

def cmd_claim(args):
    r = root(args)
    ids = {e["id"] for e in read_jsonl(os.path.join(r, "ledger.jsonl"))}
    srcs = [s.strip() for s in args.src.split(",") if s.strip()] if args.src else []
    if args.tier != "UNVERIFIED":
        if not srcs:
            print("!! tier %s requires --src. Refusing." % args.tier)
            return 1
        missing = [s for s in srcs if s not in ids]
        if missing:
            print("!! unknown evidence id(s): %s" % ", ".join(missing))
            print("!! Fetch and store the source first. Refusing to record.")
            return 1
    rec = {"ts": now(), "kind": "claim", "tier": args.tier, "text": args.text,
           "src": srcs, "locator": args.locator or "", "caveat": args.caveat or ""}
    append(os.path.join(r, "claims.jsonl"), rec)
    print("recorded [%s] %s" % (args.tier, args.text))
    if args.tier == "UNVERIFIED":
        print("-> must appear in prose as [DATA?], never as a value.")
    return 0

def cmd_ls(args):
    r = root(args)
    what = args.what
    if what in ("ledger", "blocked", "all"):
        resolved = {e["id"] for e in read_jsonl(os.path.join(r, "ledger.jsonl"))
                    if e.get("kind") == "blocked-resolved"}
        for e in read_jsonl(os.path.join(r, "ledger.jsonl")):
            k = e.get("kind")
            if k == "blocked-resolved":
                continue
            if what == "blocked" and k != "blocked":
                continue
            if k == "blocked":
                state = "RESOLVED" if e["id"] in resolved else "OPEN"
                print("%-14s blocked/%-8s %-9s %s" % (e["id"], e["reason"], state, e["url"]))
            else:
                label = e.get("url") or e.get("cmd", "")
                print("%-14s %-9s %-6s %s" % (e["id"], k, e.get("tag", "-"), label[:90]))
    if what in ("claims", "all"):
        for c in read_jsonl(os.path.join(r, "claims.jsonl")):
            print("[%-10s] %s   <- %s" % (c["tier"], c["text"], ",".join(c["src"]) or "-"))
    return 0

def cmd_show(args):
    r = root(args)
    for e in read_jsonl(os.path.join(r, "ledger.jsonl")):
        if e.get("id") == args.id and e.get("file"):
            print(json.dumps(e, indent=2, ensure_ascii=False))
            print("-" * 60)
            p = os.path.join(r, e["file"])
            with open(p, "rb") as f:
                sys.stdout.write(f.read(args.bytes).decode("utf-8", "ignore"))
            print()
            return 0
    print("!! no stored artifact with id %s" % args.id)
    return 1

def cmd_verify(args):
    r = root(args)
    led = read_jsonl(os.path.join(r, "ledger.jsonl"))
    ids = {e["id"] for e in led}
    bad = 0
    for e in led:
        if not e.get("file"):
            continue
        p = os.path.join(r, e["file"])
        if not os.path.exists(p):
            print("MISSING FILE  %s  %s" % (e["id"], e["file"])); bad += 1; continue
        with open(p, "rb") as f:
            if digest(f.read()) != e["sha256"]:
                print("HASH MISMATCH %s  %s" % (e["id"], e["file"])); bad += 1
    for c in read_jsonl(os.path.join(r, "claims.jsonl")):
        for s in c["src"]:
            if s not in ids:
                print("DANGLING SRC  %s  <- %s" % (c["text"][:60], s)); bad += 1
        if c["tier"] == "UNVERIFIED":
            print("UNVERIFIED    %s" % c["text"])
    resolved = {e["id"] for e in led if e.get("kind") == "blocked-resolved"}
    for e in led:
        if e.get("kind") == "blocked" and e["id"] not in resolved:
            print("BLOCKED OPEN  %s  %s" % (e["reason"], e["url"]))
    walls = [e for e in led if e.get("wall_markers")]
    for e in walls:
        print("WALL MARKERS  %s  %s" % (e["id"], e.get("url", "")))
    print("\n%d artifacts, %d integrity problems" % (len(led), bad))
    return 1 if bad else 0

def cmd_protocol(args):
    """Emit a re-runnable query log from the ledger (search_protocol.md style)."""
    r = root(args)
    print("# Query log - generated %s\n" % now())
    print("Every line below was actually executed. Re-run and diff before "
          "submission.\n")
    by_kind = {}
    for e in read_jsonl(os.path.join(r, "ledger.jsonl")):
        by_kind.setdefault(e.get("kind", "?"), []).append(e)
    for kind in ("gh", "fetch", "sh", "human", "blocked"):
        rows = by_kind.get(kind)
        if not rows:
            continue
        print("## %s\n" % kind)
        print("```")
        for e in rows:
            label = e.get("cmd") or e.get("url", "")
            extra = ""
            if "total_count" in e:
                extra = "  -> %s" % e["total_count"]
                if e.get("incomplete_results"):
                    extra += "  (INCOMPLETE)"
            if kind == "blocked":
                extra = "  -> BLOCKED (%s)" % e.get("reason")
            print("%s  [%s]%s" % (label, e.get("id", "-"), extra))
        print("```\n")
    return 0

def main():
    ap = argparse.ArgumentParser(prog="ev", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="evidence dir (default ./evidence or $EV_ROOT)")
    sub = ap.add_subparsers(dest="c", required=True)

    f = sub.add_parser("fetch", help="HTTP GET a URL and store the body")
    f.add_argument("url"); f.add_argument("--tag"); f.add_argument("--note")
    f.add_argument("--accept", default="text/html,application/json,*/*")
    f.add_argument("--timeout", type=int, default=30)
    f.set_defaults(fn=cmd_fetch)

    g = sub.add_parser("gh", help="run a gh command and store stdout; put "
        "--tag/--note BEFORE the gh args, or separate with --")
    g.add_argument("--tag"); g.add_argument("--note")
    g.add_argument("argv", nargs=argparse.REMAINDER)
    g.add_argument("--timeout", type=int, default=120)
    g.set_defaults(fn=cmd_gh)

    s = sub.add_parser("sh", help="run a shell command and store stdout")
    s.add_argument("--cmd", required=True); s.add_argument("--cwd")
    s.add_argument("--tag"); s.add_argument("--note")
    s.add_argument("--keep-failed", action="store_true")
    s.add_argument("--timeout", type=int, default=600)
    s.set_defaults(fn=cmd_sh)

    b = sub.add_parser("blocked", help="record an unreadable source, ask the human")
    b.add_argument("url"); b.add_argument("--reason", required=True, choices=REASONS)
    b.add_argument("--need", help="what you need from the page")
    b.set_defaults(fn=cmd_blocked)

    a = sub.add_parser("adopt", help="ingest human-pasted content into the ledger")
    a.add_argument("path"); a.add_argument("--url", required=True)
    a.add_argument("--collected", help="YYYY-MM-DD the human copied it")
    a.add_argument("--tag"); a.add_argument("--note")
    a.set_defaults(fn=cmd_adopt)

    c = sub.add_parser("claim", help="record a claim bound to evidence ids")
    c.add_argument("text"); c.add_argument("--tier", required=True, choices=TIERS)
    c.add_argument("--src", help="comma-separated evidence ids")
    c.add_argument("--locator", help="line/section/offset inside the artifact")
    c.add_argument("--caveat")
    c.set_defaults(fn=cmd_claim)

    l = sub.add_parser("ls"); l.add_argument("what", nargs="?", default="all",
        choices=["all", "ledger", "claims", "blocked"]); l.set_defaults(fn=cmd_ls)

    sh = sub.add_parser("show"); sh.add_argument("id")
    sh.add_argument("--bytes", type=int, default=4000); sh.set_defaults(fn=cmd_show)

    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    sub.add_parser("protocol").set_defaults(fn=cmd_protocol)

    args = ap.parse_args()
    sys.exit(args.fn(args))

if __name__ == "__main__":
    main()
