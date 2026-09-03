"""Offline self-test: adapter fixtures plus a reproduction check.

Runs without network access or credentials, so a reviewer can verify the
instrument and the derivation before deciding whether to rebuild
anything. Exits non-zero on the first failure.
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import json, os, subprocess, sys, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engines
from common import DATASET

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(name, got, want):
    if got != want:
        fails.append("%s\n    got  %r\n    want %r" % (name, got, want))


def adapters():
    """The version instrument, against files whose content we control.

    Godot 3.x carries no config/features, so `minor` must stay None
    rather than being imputed; config_version 3 is Godot 2.x and is in
    the corpus; an unknown config_version must degrade to a status, not
    raise.
    """
    g = engines.get("godot")
    check("godot3 major", g.parse("config_version=4\n")["major"], "3")
    check("godot3 minor not imputed", g.parse("config_version=4\n")["minor"], None)
    check("godot2 major", g.parse("config_version=3\n")["major"], "2")
    p = g.parse('config_version=5\nconfig/features=PackedStringArray("4.7", "Forward Plus")\n')
    check("godot4 minor", p["minor"], "4.7")
    check("godot4 renderer", p["renderer"], "Forward Plus")
    p = g.parse('config_version=5\nconfig/features=PackedStringArray("4.2", "C#", "Mobile")\n')
    check("csharp detected", p["uses_csharp"], True)
    check("csharp minor", p["minor"], "4.2")
    p = g.parse('config_version=5\nconfig/features=["4.0", "Vulkan Clustered"]\n')
    check("legacy list form", p["minor"], "4.0")
    check("no version key", g.parse("name=x\n")["status"], "no-version-key")
    check("unknown config_version", g.parse("config_version=9\n")["status"],
          "unknown-config-version")

    u = engines.get("unity")
    check("unity minor", u.parse("m_EditorVersion: 2021.3.16f1\n")["minor"], "2021.3")
    check("unity major", u.parse("m_EditorVersion: 6000.0.83f1\n")["major"], "6000")
    check("unity absent", u.parse("nothing\n")["status"], "no-version-key")


def version_ordering():
    """String ordering puts 4.10 below 4.7; the corpus contains both."""
    from derive import vkey
    check("4.10 sorts above 4.7", vkey("4.10") > vkey("4.7"), True)
    check("4.2.1 sorts above 4.2", vkey("4.2.1") > vkey("4.2"), True)


def reproduction():
    """Re-deriving at the release's own observation date must be a no-op.

    The derived tables are pure functions of the measured tables and of
    that one date. If this fails, either a measured table has changed or
    a derivation has stopped being deterministic.
    """
    summary = DATASET / "summary.json"
    if not summary.exists():
        fails.append("reproduction: %s missing" % summary)
        return
    pinned = json.loads(summary.read_text())["generated_on"]
    derived = ["lag_observations.jsonl", "migration_windows.jsonl",
               "strata.jsonl", "summary.json"]
    tmp = tempfile.mkdtemp(prefix="gem-selftest-")
    try:
        for f in derived:
            shutil.copy2(DATASET / f, os.path.join(tmp, f))
        r = subprocess.run([sys.executable, os.path.join(HERE, "derive.py"),
                            "--observed-on", pinned],
                           capture_output=True, text=True)
        if r.returncode != 0:
            fails.append("reproduction: derive.py exited %d\n%s"
                         % (r.returncode, r.stderr[-800:]))
            return
        for f in derived:
            before = open(os.path.join(tmp, f), "rb").read()
            after = (DATASET / f).read_bytes()
            if before != after:
                fails.append("reproduction: %s changed when re-derived at %s" % (f, pinned))
    finally:
        for f in derived:                      # leave the release as we found it
            src = os.path.join(tmp, f)
            if os.path.exists(src):
                shutil.copy2(src, DATASET / f)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    for stage in (adapters, version_ordering, reproduction):
        stage()
        print("%-18s %s" % (stage.__name__, "ok" if not fails else "FAILED"))
        if fails:
            break
    print()
    if fails:
        for f in fails:
            print("FAIL  " + f)
        return 1
    print("selftest passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
