"""Engine adapters.

The dataset is about engine version migration, not about Godot. An
adapter is the only engine-specific part of the miner: it names the file
that declares the version and knows how to read a version out of it.
Godot is instantiated; Unity and Unreal are declared with their
extraction rules so the interface is demonstrably not Godot-shaped, but
neither is mined in v0.1.
"""

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Gabriel Pires
import re

class Engine:
    name = None
    version_files = ()      # basenames that declare the engine version
    def parse(self, text):
        """-> dict(major, minor, config_version, features_raw, renderer,
                   uses_csharp, status)"""
        raise NotImplementedError


class Godot(Engine):
    name = "godot"
    version_files = ("project.godot",)

    # config_version -> engine major. Measured, not assumed: see the
    # format-probe artifacts in the ledger (ff34aa03db6e, 14072db90f7f).
    CONFIG_MAJOR = {3: "2", 4: "3", 5: "4"}

    RE_CV = re.compile(r"^\s*config_version\s*=\s*(\d+)", re.M)
    RE_FEAT = re.compile(r"^\s*config/features\s*=\s*PackedStringArray\(([^)]*)\)", re.M)
    RE_FEAT_OLD = re.compile(r"^\s*config/features\s*=\s*\[([^\]]*)\]", re.M)
    RE_VERTOK = re.compile(r"^\d+\.\d+$")
    RENDERERS = ("Forward Plus", "Mobile", "GL Compatibility", "Vulkan Clustered",
                 "Vulkan Mobile", "GLES3", "GLES2")

    def parse(self, text):
        out = dict(major=None, minor=None, config_version=None, features_raw=None,
                   renderer=None, uses_csharp=False, status="ok")
        m = self.RE_CV.search(text)
        if not m:
            out["status"] = "no-version-key"
            return out
        cv = int(m.group(1))
        out["config_version"] = cv
        out["major"] = self.CONFIG_MAJOR.get(cv)
        if out["major"] is None:
            out["status"] = "unknown-config-version"

        f = self.RE_FEAT.search(text) or self.RE_FEAT_OLD.search(text)
        if f:
            raw = f.group(1).strip()
            out["features_raw"] = raw
            items = [s.strip().strip('"').strip("'") for s in raw.split(",")]
            items = [s for s in items if s]
            for it in items:
                if self.RE_VERTOK.match(it) and out["minor"] is None:
                    out["minor"] = it
                elif it in self.RENDERERS and out["renderer"] is None:
                    out["renderer"] = it
                elif it in ("C#", "CSharp", "Double Precision"):
                    if it != "Double Precision":
                        out["uses_csharp"] = True
        # Godot 3.x carries no config/features at all -- that is the
        # instrument's limit, not a parse failure. Do not impute a minor.
        return out


class Unity(Engine):
    """Not mined in v0.1. ProjectSettings/ProjectVersion.txt reads
    `m_EditorVersion: 2021.3.16f1`; the minor series is the first two
    dotted components. Present to keep the schema honest about being
    engine-agnostic."""
    name = "unity"
    version_files = ("ProjectVersion.txt",)
    RE = re.compile(r"^m_EditorVersion:\s*(\S+)", re.M)

    def parse(self, text):
        m = self.RE.search(text)
        if not m:
            return dict(major=None, minor=None, config_version=None, features_raw=None,
                        renderer=None, uses_csharp=True, status="no-version-key")
        v = m.group(1)
        parts = v.split(".")
        return dict(major=parts[0], minor=".".join(parts[:2]), config_version=None,
                    features_raw=v, renderer=None, uses_csharp=True, status="ok")


class Unreal(Engine):
    """Not mined in v0.1. A .uproject is JSON with an EngineAssociation
    field, which is either a version string or a local GUID -- the GUID
    case is unresolvable from the repository alone and would have to be
    recorded as status=unresolvable."""
    name = "unreal"
    version_files = (".uproject",)


REGISTRY = {e.name: e() for e in (Godot, Unity, Unreal)}


def get(name="godot"):
    return REGISTRY[name]
