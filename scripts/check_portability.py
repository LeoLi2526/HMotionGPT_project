#!/usr/bin/env python3
"""Read-only check of a cloned project's portable files and skill copies."""

import hashlib
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "AGENTS.md",
    "PROJECT_GOAL.yaml",
    "SUBAGENT_POLICY.yaml",
    "OPERATIONS_POLICY.yaml",
    "ROADMAP.md",
    "PROJECT_HANDOFF.md",
    ".aris/vendor/aris/LICENSE",
    ".aris/installed-skill-names.txt",
    "knowledge_base/PAPER_SOURCES.md",
)


def file_tree(directory):
    """Map relative file paths to hashes without following directory links."""
    result = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("symbolic link: " + str(path.relative_to(directory)))
        if path.is_file():
            relative = path.relative_to(directory).as_posix()
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def main():
    errors = []
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            errors.append("missing required file: " + name)

    skill_dir = ROOT / ".agents/skills"
    source_dir = ROOT / ".aris/vendor/aris/skills/skills-codex"
    names_file = ROOT / ".aris/installed-skill-names.txt"
    names = names_file.read_text().splitlines() if names_file.is_file() else []
    expected = set(names + ["shared-references"])
    actual = set(path.name for path in skill_dir.iterdir()) if skill_dir.is_dir() else set()
    if len(names) != 87 or len(set(names)) != 87:
        errors.append("installed skill inventory should contain 87 unique names")
    if actual != expected:
        errors.append("skill directory inventory differs from installed-skill-names.txt")

    mismatches = []
    for name in sorted(expected):
        local = skill_dir / name
        source = source_dir / name
        if local.is_symlink() or not local.is_dir():
            mismatches.append(name + " (not an ordinary directory)")
        elif not source.is_dir():
            mismatches.append(name + " (vendor source missing)")
        else:
            try:
                if file_tree(local) != file_tree(source):
                    mismatches.append(name + " (differs from pinned vendor copy)")
            except ValueError as exc:
                mismatches.append(name + " (" + str(exc) + ")")
    if mismatches:
        errors.append("skill copy errors: " + ", ".join(mismatches[:8]))
        if len(mismatches) > 8:
            errors.append("and " + str(len(mismatches) - 8) + " more skill copy errors")

    tools_dir = ROOT / ".aris/tools"
    vendor_tools = ROOT / ".aris/vendor/aris/tools"
    if tools_dir.is_symlink() or not tools_dir.is_dir():
        errors.append(".aris/tools is not an ordinary directory")
    elif vendor_tools.is_dir():
        try:
            if file_tree(tools_dir) != file_tree(vendor_tools):
                errors.append(".aris/tools differs from pinned vendor tools")
        except ValueError as exc:
            errors.append(".aris/tools: " + str(exc))
    else:
        errors.append("pinned vendor tools are missing")

    try:
        remote = subprocess.run(
            ["git", "-C", str(ROOT), "remote", "get-url", "origin"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        errors.append("Git origin remote is unavailable")
        remote = "unknown"

    print("Project: " + str(ROOT))
    print("Git origin: " + remote)
    print("Skill directories: " + str(len(actual)))
    if errors:
        for error in errors:
            print("ERROR: " + error, file=sys.stderr)
        return 1
    print("Portable checkout: OK (hardware and chat handoff not tested)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
