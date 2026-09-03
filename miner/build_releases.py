"""Stage 0 -- the engine release timeline.

Stables come from godotengine/godot (no backfill). Prereleases come from
godotengine/godot-builds, using created_at, because that repo backfilled
every pre-2023-09-12 release with published_at=2023-09-12 (caveat C12).
The field used is recorded per row rather than left to be inferred.
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import re, sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from common import ENGINE, ev_gh, ev_json, write_table, ev_claim

TAG = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?-(stable|rc\d*|beta\d*|alpha\d*|dev\d*)$")
BACKFILL = "2023-09-12"


def channel(suffix):
    return re.sub(r"\d+$", "", suffix)


def parse_tag(tag):
    m = TAG.match(tag)
    if not m:
        return None
    maj, minor, patch, extra, suf = m.groups()
    version = ".".join(p for p in (maj, minor, patch, extra) if p)
    return dict(version=version, line="%s.x" % maj, minor_series="%s.%s" % (maj, minor),
                channel=channel(suf), patch=patch)


def main():
    stable_ev = ev_gh(["api", "-X", "GET", "repos/godotengine/godot/releases",
                       "-f", "per_page=100", "--paginate"],
                      tag="releases-stable",
                      note="stage0: stable release timeline, no backfill in this repo")
    pre_ev = ev_gh(["api", "-X", "GET", "repos/godotengine/godot-builds/releases",
                    "-f", "per_page=100", "--paginate"],
                   tag="releases-builds",
                   note="stage0: prerelease timeline; use created_at, published_at is backfilled (C12)")
    if not (stable_ev and pre_ev):
        sys.exit("release fetch failed -- refusing to build a partial timeline")

    rows, seen = {}, set()

    for r in ev_json(stable_ev):
        p = parse_tag(r["tag_name"])
        if not p or p["channel"] != "stable":
            continue
        rows[r["tag_name"]] = dict(
            engine=ENGINE, tag=r["tag_name"], released_on=r["published_at"][:10],
            date_field="published_at", date_source="godotengine/godot",
            is_first_of_minor=p["patch"] is None, ev_id=stable_ev,
            **{k: v for k, v in p.items() if k != "patch"})
        seen.add(r["tag_name"])

    disagree = []
    for r in ev_json(pre_ev):
        p = parse_tag(r["tag_name"])
        if not p:
            continue
        # created_at survives the backfill; published_at does not.
        created, published = r["created_at"][:10], r["published_at"][:10]
        if r["tag_name"] in seen:
            if rows[r["tag_name"]]["released_on"] != created and published >= BACKFILL:
                disagree.append((r["tag_name"], rows[r["tag_name"]]["released_on"], created))
            continue
        rows[r["tag_name"]] = dict(
            engine=ENGINE, tag=r["tag_name"],
            released_on=created if published <= BACKFILL else published,
            date_field="created_at" if published <= BACKFILL else "published_at",
            date_source="godotengine/godot-builds",
            is_first_of_minor=p["patch"] is None and p["channel"] == "stable",
            ev_id=pre_ev, **{k: v for k, v in p.items() if k != "patch"})

    out = sorted(rows.values(), key=lambda r: (r["released_on"], r["version"], r["channel"]))
    write_table("releases", out)

    st = [r for r in out if r["channel"] == "stable"]
    print("  stable=%d prerelease=%d  span %s..%s"
          % (len(st), len(out) - len(st), out[0]["released_on"], out[-1]["released_on"]))
    print("  4.x stable=%d   3.x stable=%d" % (
        sum(1 for r in st if r["line"] == "4.x"), sum(1 for r in st if r["line"] == "3.x")))
    if disagree:
        print("  !! %d tags where the two sources disagree on the date:" % len(disagree))
        for t, a, b in disagree[:5]:
            print("     %s  godot=%s  builds=%s" % (t, a, b))

    ev_claim("The Godot release timeline used by this dataset holds %d releases "
             "(%d stable, %d prerelease) spanning %s to %s."
             % (len(out), len(st), len(out) - len(st), out[0]["released_on"], out[-1]["released_on"]),
             "MEASURED", [stable_ev, pre_ev],
             caveat="Stables dated by published_at from godotengine/godot; prereleases by "
                    "created_at from godotengine/godot-builds, whose published_at is a "
                    "2023-09-12 backfill for everything older (C12).")
    return out


if __name__ == "__main__":
    main()
