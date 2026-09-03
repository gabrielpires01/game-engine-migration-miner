"""Stage 6 -- blocked demand, the lag x demand table.

Applies a transparent rule to the candidate issues and labels every row
method="rule". These labels are NOT ground truth: a manually adjudicated
subsample with two coders and a reported kappa is required before any
of them carries a published figure. The rule exists to make the manual
pass affordable, not to replace it.

Author handles are dropped here. They stay in the ledger artifact so
provenance is re-checkable; they do not enter the released dataset.
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import json, os, re, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (ROOT, WORK, ev_sh, ev_body, write_table, read_table, code_digest,
                    ev_claim, ENGINE, load_state, save_state)

ASK = re.compile(r"\b(port|migrat|upgrad|update|move|switch|convert|support)\w*\b[^.\n]{0,40}"
                 r"\b(to\s+)?godot\s*4|\bgodot\s*4\b[^.\n]{0,40}\b(support|port|migration|version)\b", re.I)
BLOCKER = re.compile(r"\b(block(ed|er|ing)?|wait(ing)?\s+(for|on)|depends?\s+on|"
                     r"can'?t|cannot|not\s+possible|missing|unsupported|no\s+equivalent|"
                     r"regress\w*|broken)\b", re.I)
DECLINE = re.compile(r"\b(no\s+plans?|not\s+plan\w*|won'?t\s+(be\s+)?(port|migrat|upgrad)|"
                     r"stay\w*\s+on\s+(godot\s*)?3|remain\w*\s+on\s+3|"
                     r"3\.\d+\s+is\s+(fine|enough|sufficient|stable)|"
                     r"no\s+need\s+to\s+(upgrade|migrate|port)|not\s+worth)\b", re.I)
MAINTAINER = {"OWNER", "MEMBER", "COLLABORATOR"}


VERSION_TOK = re.compile(r"godot\s*4|\b4\.\d|\bgodot4\b", re.I)


def classify(item):
    """Rule labels with an explicit confidence, because the tiers are not
    interchangeable. An earlier version labelled any issue containing a
    blocker word and a "godot 4" anywhere in 1,500 characters of body as
    demand, which swept in ordinary bug reports -- "Cannot use EXR as
    internal heightmap format" is not a migration blocker. The weakest
    rule now requires the version in the TITLE.
    """
    title = item.get("title") or ""
    text = title + "\n" + (item.get("body") or "")
    maint = item.get("author_association") in MAINTAINER
    if DECLINE.search(text):
        return ("maintainer-declines-migration" if maint else "contributor-notes-no-need",
                "counter-demand", "medium" if maint else "low")
    if ASK.search(title):
        return (("contributor-names-blocker" if BLOCKER.search(text)
                 else "issue-requests-new-version"), "demand", "high")
    if ASK.search(text):
        return (("contributor-names-blocker" if BLOCKER.search(text)
                 else "issue-requests-new-version"), "demand", "medium")
    if BLOCKER.search(text) and VERSION_TOK.search(title):
        return "contributor-names-blocker", "demand", "low"
    return None, None, None


def main(repo_ids=None):
    if repo_ids is None:
        p = WORK / "demand_tier.txt"
        if not p.exists():
            sys.exit("no demand tier list -- write work/demand_tier.txt")
        repo_ids = [l.strip() for l in p.read_text().splitlines() if l.strip()]
    inp = WORK / "demand_input.txt"
    inp.write_text("\n".join(repo_ids))

    import hashlib
    key = "n=%d:%s/%s" % (len(repo_ids), hashlib.sha256(inp.read_bytes()).hexdigest()[:12],
                          code_digest(ROOT / "miner/fetch_demand.py"))
    st = load_state("demand", {})
    eid = st.get(key)
    if not eid:
        eid = ev_sh("python3 %s --repos %s" % (ROOT / "miner/fetch_demand.py", inp),
                    cwd=ROOT, tag="demand",
                    note="stage6: issue-tracker demand and counter-demand signals, %d repos"
                         % len(repo_ids), timeout=7200)
        if not eid:
            sys.exit("demand fetch failed")
        st[key] = eid
        save_state("demand", st)

    branches = {}
    for b in read_table("branches"):
        branches.setdefault(b["repo_id"], []).append(b)
    hist = {h["repo_id"]: h for h in read_table("repo_history")}

    rows, tally = [], Counter()
    repos_seen = set()
    for line in ev_body(eid).decode("utf-8", "ignore").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        rid = rec["repo_id"]
        repos_seen.add(rid)
        for it in rec.get("items", []):
            sig, pol, conf = classify(it)
            if not sig:
                continue
            tally[sig] += 1
            rows.append(dict(
                repo_id=rid, engine=ENGINE, signal=sig, polarity=pol,
                url=it["url"], is_pr=it["is_pr"], state=it["state"],
                observed_on=it["created_at"], closed_on=it.get("closed_at"),
                comments=it.get("comments"), labels=it.get("labels"),
                snippet=(it.get("title") or "")[:300],
                author_is_maintainer=it.get("author_association") in MAINTAINER,
                coder="rule-v0.1", method="rule", confidence=conf, ev_id=eid))
    # a live migration branch is demand that needs no tracker at all
    for rid, bs in branches.items():
        for b in bs:
            if (b.get("commits") or 0) > 0 or b.get("merged"):
                tally["migration-branch-exists"] += 1
                rows.append(dict(
                    repo_id=rid, engine=ENGINE, signal="migration-branch-exists",
                    polarity="demand", url="https://github.com/%s/tree/%s" % (rid, b["branch"]),
                    is_pr=False, state="merged" if b.get("merged") else "open",
                    observed_on=b.get("first_on"), closed_on=b.get("last_on"),
                    comments=None, labels=[], snippet="branch %s (%s commits)"
                    % (b["branch"], b.get("commits")),
                    author_is_maintainer=True, coder="rule-v0.1", method="rule",
                    confidence="high", ev_id=b["ev_id"]))
    # A migration branch that won outright leaves no branch to find:
    # godot-aseprite-wizard renamed its default to godot_4 and left master
    # on the old line. That is the strongest demand signal there is.
    for rid, h in hist.items():
        if h.get("default_branch_line") == "4.x":
            tally["default-branch-is-migration"] += 1
            rows.append(dict(
                repo_id=rid, engine=ENGINE, signal="default-branch-is-migration",
                polarity="demand",
                url="https://github.com/%s/tree/%s" % (rid, h.get("default_branch")),
                is_pr=False, state="default", observed_on=h.get("last_commit_on"),
                closed_on=None, comments=None, labels=[],
                snippet="default branch is %s" % h.get("default_branch"),
                author_is_maintainer=True, coder="rule-v0.1", method="rule",
                confidence="high", ev_id=h["ev_id"]))
    write_table("blocked_demand", rows)
    print("  " + json.dumps(dict(tally)))
    STRONG = ("high", "medium")
    with_demand = {r["repo_id"] for r in rows if r["polarity"] == "demand"}
    strong_demand = {r["repo_id"] for r in rows
                     if r["polarity"] == "demand" and r["confidence"] in STRONG}
    counter = {r["repo_id"] for r in rows if r["polarity"] == "counter-demand"}
    print("  repos probed: %d" % len(repos_seen))
    print("  with any demand signal: %d   with a high/medium one: %d   "
          "with a counter-demand signal: %d"
          % (len(with_demand), len(strong_demand), len(counter)))
    print("  by confidence: %s"
          % json.dumps(dict(Counter(r["confidence"] for r in rows).most_common())))

    ev_claim("Of %d repositories searched for issue-tracker evidence, %d carry at least one "
             "signal of demand for the newer engine version and %d carry at least one signal "
             "against migrating."
             % (len(repos_seen), len(with_demand), len(counter)),
             "MEASURED", [eid],
             caveat="Rule-labelled, not adjudicated: candidate evidence pending a two-coder "
                    "manual pass with a reported kappa. The demand rules were tuned for recall "
                    "and the counter-demand rule for precision, so the in-debt fraction computed "
                    "from these labels is an UPPER bound and must not be reported as an estimate. "
                    "Issue-tracker evidence also over-represents organised teams; solo developers "
                    "who decided either way in silence leave no record (C7).")
    return rows


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos")
    a = ap.parse_args()
    ids = [l.strip() for l in open(a.repos) if l.strip()] if a.repos else None
    main(ids)
