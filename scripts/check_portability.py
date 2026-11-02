#!/usr/bin/env python3
"""Read-only check of a cloned project's portable files and skill links."""

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


def main() -> int:
    errors = []
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            errors.append(f"missing required file: {name}")

    skill_dir = ROOT / ".agents/skills"
    links = list(skill_dir.iterdir()) if skill_dir.is_dir() else []
    if not links:
        errors.append("missing project skill links")
    for link in links:
        if not link.is_symlink():
            errors.append(f"skill entry is not a symlink: {link.relative_to(ROOT)}")
        elif not link.exists():
            errors.append(f"broken skill link: {link.relative_to(ROOT)}")
        else:
            try:
                link.resolve().relative_to(ROOT)
            except ValueError:
                errors.append(f"skill link points outside project: {link.relative_to(ROOT)}")

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

    print(f"Project: {ROOT}")
    print(f"Git origin: {remote}")
    print(f"Skill entries: {len(links)}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Portable checkout: OK (hardware and chat handoff not tested)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
