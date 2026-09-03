"""Shared plumbing: paths, JSONL IO, ledger binding, GitHub calls.

Every row this package writes carries an ev_id naming the stored
artifact it was computed from. check.py fails the build on a row
without one.
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import hashlib, json, os, re, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"
DATASET = ROOT / "dataset" / "v0.1"
WORK = ROOT / "work"
# The evidence ledger CLI. Resolution order: $EV_PY, then a copy
# vendored next to this package, then the authoring machine's install.
# A clone must be able to run the pipeline without that last one, so the
# vendored copy is what ships.
def _find_ev():
    env = os.environ.get("EV_PY")
    if env:
        return Path(env)
    local = Path(__file__).resolve().parent / "ev.py"
    if local.exists():
        return local
    return Path.home() / ".claude/skills/evidence-mining/scripts/ev.py"


EV_PY = _find_ev()

SCHEMA_VERSION = "0.1"
ENGINE = "godot"

for d in (EVIDENCE, DATASET, WORK):
    d.mkdir(parents=True, exist_ok=True)


def today():
    return time.strftime("%Y-%m-%d", time.gmtime())


# ------------------------------------------------------------------ ledger

def ev(*args, timeout=900):
    """Run ev.py and return the stored artifact id, or None on failure."""
    p = subprocess.run([sys.executable, str(EV_PY), "--root", str(EVIDENCE)] + list(args),
                       capture_output=True, text=True, timeout=timeout)
    out = p.stdout
    m = re.search(r"^stored ([0-9a-f]{12})", out, re.M)
    if not m:
        sys.stderr.write("ev.py did not store: %s\n%s\n" % (" ".join(args[:3]), out[:800] + p.stderr[:800]))
        return None
    if "ACCESS-WALL MARKERS" in out:
        sys.stderr.write("!! wall markers in %s -- artifact is not the content\n" % m.group(1))
    if "incomplete_results" in out:
        sys.stderr.write("!! incomplete_results in %s -- count is PARTIAL\n" % m.group(1))
    return m.group(1)


def ev_gh(gh_args, tag=None, note=None, timeout=900):
    # ev.py enforces its OWN timeout on the child process; passing only the
    # outer subprocess timeout lets it kill a long batch at its 120s/600s
    # default and report no artifact.
    a = ["gh", "--timeout", str(timeout)]
    if tag: a += ["--tag", tag]
    if note: a += ["--note", note]
    return ev(*(a + ["--"] + gh_args), timeout=timeout + 120)


def ev_sh(cmd, cwd=None, tag=None, note=None, timeout=900):
    a = ["sh", "--cmd", cmd, "--timeout", str(timeout)]
    if cwd: a += ["--cwd", str(cwd)]
    if tag: a += ["--tag", tag]
    if note: a += ["--note", note]
    return ev(*a, timeout=timeout + 120)


def ev_body(eid):
    """Read a stored artifact back from disk. Never report from memory."""
    for line in (EVIDENCE / "ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("id") == eid and r.get("file"):
            return (EVIDENCE / r["file"]).read_bytes()
    raise KeyError("no stored artifact %s" % eid)


def ev_json(eid):
    """Parse a stored artifact as JSON; concatenated pages from --paginate
    arrive as several top-level arrays, so decode them in sequence."""
    raw = ev_body(eid).decode("utf-8", "ignore")
    dec, idx, out = json.JSONDecoder(), 0, []
    while idx < len(raw):
        while idx < len(raw) and raw[idx].isspace():
            idx += 1
        if idx >= len(raw):
            break
        obj, idx = dec.raw_decode(raw, idx)
        out.append(obj)
    if len(out) == 1:
        return out[0]
    if all(isinstance(o, list) for o in out):
        return [x for o in out for x in o]
    return out


def ev_claim(text, tier, src, locator=None, caveat=None):
    a = ["claim", text, "--tier", tier]
    if src: a += ["--src", ",".join(src) if isinstance(src, (list, tuple)) else src]
    if locator: a += ["--locator", locator]
    if caveat: a += ["--caveat", caveat]
    subprocess.run([sys.executable, str(EV_PY), "--root", str(EVIDENCE)] + a,
                   capture_output=True, text=True)


# ------------------------------------------------------------------ tables

def write_table(name, rows):
    """Rewrite a dataset table wholesale. Refuses rows with no provenance."""
    path = DATASET / (name + ".jsonl")
    bad = [i for i, r in enumerate(rows) if not (r.get("ev_id") or r.get("ev_ids"))]
    if bad:
        raise ValueError("%s: %d rows with no ev_id (first at index %d)" % (name, len(bad), bad[0]))
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            # Strip any schema/v carried in from read_table: a table that is
            # loaded, amended and written back must not collide on them.
            body = {k: v for k, v in r.items() if k not in ("schema", "v")}
            f.write(json.dumps(dict(schema=name, v=SCHEMA_VERSION, **body),
                               ensure_ascii=False, sort_keys=True) + "\n")
    print("%-22s %6d rows -> %s" % (name, len(rows), path.relative_to(ROOT)))
    return path


def read_table(name):
    path = DATASET / (name + ".jsonl")
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def code_digest(*paths):
    """Digest of the fetcher/miner source. Batch caches must key on this:
    a stored artifact is the output of a specific version of the code, and
    reusing it after the code changes silently serves stale measurements.
    """
    h = hashlib.sha256()
    for p in paths:
        h.update(Path(p).read_bytes())
    return h.hexdigest()[:12]


def load_state(name, default=None):
    p = WORK / (name + ".json")
    return json.loads(p.read_text()) if p.exists() else (default if default is not None else {})


def save_state(name, obj):
    (WORK / (name + ".json")).write_text(json.dumps(obj))
