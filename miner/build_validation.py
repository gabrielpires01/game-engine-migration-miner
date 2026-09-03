"""Stage 4 -- the agreement study.

Does the declared version match the engine actually in use? Run this
BEFORE trusting any version-derived timeline; caveat C9 is only
quotable once this number exists.

`no-secondary-signal` is reported separately from `agree`. Folding them
together would inflate agreement with every project that simply has no
CI, which is most small ones -- the single easiest way to publish a
reassuring and meaningless validation rate.
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import json, os, re, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (ROOT, WORK, ev_sh, ev_body, write_table, read_table, code_digest,
                    ev_claim, ENGINE, load_state, save_state)


def mode(values):
    """Most common value, not the first. A workflow matrix or a stale job
    can put an old version ahead of the live one -- Pixelorama declares 4.7
    and its first workflow hit is 3.5."""
    return Counter(values).most_common(1)[0][0] if values else None


def minor(v):
    return ".".join(v.split(".")[:2]) if v else None


def major(v):
    return v.split(".")[0] if v else None


def build_input(n=50, seed_order=True):
    """Repositories with a parsed declared version, most-starred first.

    Sampling only repositories that already have CI would test agreement
    on exactly the population most likely to agree, so the no-signal
    cases are carried and reported separately instead.
    """
    snaps = {(s["repo_id"], s["path"]): s for s in read_table("snapshots")}
    primary = {}
    for pf in read_table("project_files"):
        if pf.get("is_primary"):
            primary[pf["repo_id"]] = pf["path"]
    repos = {r["repo_id"]: r for r in read_table("repos")}
    sigs = {g["repo_id"]: g for g in read_table("repo_signals")}
    rows = []
    for rid, path in primary.items():
        s = snaps.get((rid, path))
        if not s or s.get("parse_status") != "ok":
            continue
        declared = s.get("declared_minor") or ("3.x" if s.get("config_version") == 4 else None)
        g = sigs.get(rid, {})
        rows.append(dict(repo_id=rid,
                         default_branch=(g.get("branch")
                                         or repos.get(rid, {}).get("default_branch")),
                         declared=declared, config_version=s.get("config_version"),
                         workflows=g.get("workflow_paths") or [],
                         stars=repos.get(rid, {}).get("stars", 0)))
    rows.sort(key=lambda r: -r["stars"])
    return rows[:n] if n else rows


def main(n=50):
    rows = build_input(n)
    if not rows:
        sys.exit("no snapshot rows -- run build_snapshot.py first")
    inp = WORK / "validation_input.jsonl"
    inp.write_text("\n".join(json.dumps(r) for r in rows))

    # Key on the input digest, not the row count: the same N with different
    # inputs must not reuse a stored batch.
    import hashlib
    key = "n=%d:%s/%s" % (len(rows), hashlib.sha256(inp.read_bytes()).hexdigest()[:12],
                          code_digest(ROOT / "miner/fetch_validation.py"))
    st = load_state("validation", {})
    eid = st.get(key)
    if not eid:
        eid = ev_sh("python3 %s --repos %s --workers 8" % (ROOT / "miner/fetch_validation.py", inp),
                    cwd=ROOT, tag="validation",
                    note="stage4: independent version signals (CI, container, README, presets) for %d repos"
                         % len(rows), timeout=3600)
        if not eid:
            sys.exit("validation fetch failed")
        st[key] = eid
        save_state("validation", st)

    decl = {r["repo_id"]: r for r in rows}
    out, tally = [], Counter()
    for line in ev_body(eid).decode("utf-8", "ignore").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        rid = rec["repo_id"]
        d = decl.get(rid, {})
        declared, cv = d.get("declared"), d.get("config_version")
        by_kind, versions = {}, []
        for s in rec.get("signals", []):
            for h in s["hints"]:
                by_kind.setdefault(s["kind"], []).append(h["version"])
                versions.append((s["kind"], h["version"], h["pattern"], s["path"]))
        # README prose is NOT an instrument, and this study is what showed
        # it. Measured on 200 repositories: the declared version agrees with
        # a machine-readable CI pin 79% of the time (26/33) and with a
        # README mention 54% (33/61). The README disagreements are stale
        # prose -- beehave declares 4.7 and its README mentions 3.5,
        # tps-demo declares 4.5 and mentions 3.4 -- because a README carries
        # compatibility tables and changelogs, not a statement about the
        # engine currently in use. Pooling the two produced 62%, a number
        # that describes neither.
        ranked = [v for k, v, _, _ in versions if k in ("workflow", "gitlab_ci", "dockerfile")] or \
                 [v for k, v, _, _ in versions if k == "export_presets"]
        prose_only = bool(not ranked and [v for k, v, _, _ in versions if k == "readme"])
        agreement, kind, recovered = ("prose-only" if prose_only
                                      else "no-secondary-signal"), None, None
        best = None
        if ranked:
            best = Counter(ranked).most_common(1)[0][0]
            if cv == 4:            # Godot 3.x: only the major is comparable
                agreement = "agree" if major(best) == "3" else "disagree"
                kind = None if agreement == "agree" else "different-line"
                # Godot 3.x project.godot records no minor version at all.
                # A CI pin does -- oh-my-git declares config_version=4 and
                # pins 3.2.3 -- so an independent signal partially lifts the
                # instrument's hard limit for the projects that carry one.
                if agreement == "agree" and best.count(".") >= 1:
                    recovered = minor(best)
            elif declared:
                if minor(best) == declared:
                    agreement = "agree"
                elif major(best) != major(declared):
                    agreement, kind = "disagree", "different-line"
                elif minor(best) > declared:
                    agreement, kind = "disagree", "declared-older"
                else:
                    agreement, kind = "disagree", "declared-newer"
        tally[agreement] += 1
        if kind:
            tally["kind:" + kind] += 1
        out.append(dict(repo_id=rid, engine=ENGINE, declared=declared,
                        config_version=cv,
                        ci_pin=mode(by_kind.get("workflow") or by_kind.get("gitlab_ci")),
                        container_hint=mode(by_kind.get("dockerfile")),
                        export_preset_hint=mode(by_kind.get("export_presets")),
                        readme_hint=mode(by_kind.get("readme")),
                        secondary_count=len(versions), best_secondary=best,
                        recovered_minor=recovered,
                        agreement=agreement, disagreement_kind=kind,
                        signals=rec.get("signals", [])[:4], ev_id=eid))
    write_table("validation", out)
    n_test = tally["agree"] + tally["disagree"]
    rec3 = sum(1 for r in out if r.get("recovered_minor"))
    print("  " + json.dumps(dict(tally)))
    print("  prose-only (README mention, not counted as a signal): %d"
          % tally["prose-only"])
    if rec3:
        print("  Godot 3.x minor version recovered from an independent signal "
              "for %d repositories" % rec3)
    if n_test:
        print("  agreement among testable repos: %d/%d (%.0f%%); %d had no independent signal"
              % (tally["agree"], n_test, 100.0 * tally["agree"] / n_test, tally["no-secondary-signal"]))
        ev_claim("Of %d repositories checked for agreement between the version declared in "
                 "project.godot and a MACHINE-READABLE independent signal (CI pin, container tag, "
                 "export preset), %d carried one; among those, %d agreed (%.0f%%) and %d "
                 "disagreed. A further %d had only a README mention, excluded as prose."
                 % (len(out), n_test, tally["agree"], 100.0 * tally["agree"] / n_test,
                    tally["disagree"], tally["prose-only"]),
                 "MEASURED", [eid],
                 caveat="%d of %d repositories carried no independent signal at all and are "
                        "excluded from the rate rather than counted as agreeing. For Godot 3.x "
                        "projects only the major version is comparable, since project.godot "
                        "records no minor version in that era."
                        % (tally["no-secondary-signal"], len(out)))
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=50)
    main(ap.parse_args().n)
