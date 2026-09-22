"""C0 hygiene gate — static build-context audit (no Docker daemon required).

Applies the repo-root `.dockerignore` to every file that actually exists on disk under
the repo root, using a deliberately simple, documented approximation of Docker's own
ignore-pattern algorithm (itself derived from gitignore semantics):

  * blank lines and lines starting with '#' are skipped
  * a pattern ending in '/' matches a directory (and everything under it) at any depth
  * a pattern with no other '/' is a basename glob: matches any path component,
    at any depth (gitignore-style)
  * a pattern containing an internal '/' is anchored to the context root
  * '!' negates a later, more specific re-inclusion
  * patterns are applied strictly in file order, top to bottom

KNOWN SIMPLIFICATION (documented, not hidden): this does not implement Docker/BuildKit's
rule that a negated pattern cannot re-include a file whose ANCESTOR directory was already
excluded — the .dockerignore in this repo never relies on that case (its one negation,
`!README.md`, re-includes a top-level file, not something nested under an excluded dir),
so the simplification does not affect this audit's correctness for the current file. This
must be re-verified against a real `docker build` once Docker Desktop is available (G4).

Usage:  python audit_build_context.py [repo_root]
Output: docker/context.manifest (sorted list of every path that WOULD enter the build
        context today) + a human-readable summary printed to stdout.
"""
from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path

# Directories that can never meaningfully be "in the build context" and would make the
# walk enormous / irrelevant to this audit (the .git object database itself). Docker
# also excludes .git by default in modern versions; the repo's .dockerignore excludes it
# explicitly too (`.git/`), so this is belt-and-suspenders, not a hidden rule.
ALWAYS_PRUNE = {".git"}


def load_patterns(dockerignore: Path) -> list[tuple[bool, str]]:
    """Returns [(is_negation, pattern_without_leading_!), ...] in file order."""
    out = []
    for raw in dockerignore.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        neg = line.startswith("!")
        pat = line[1:] if neg else line
        out.append((neg, pat))
    return out


def pattern_matches(pattern: str, relpath: str) -> bool:
    """relpath uses forward slashes, no leading slash."""
    is_dir_pattern = pattern.endswith("/")
    pat = pattern.rstrip("/")
    parts = relpath.split("/")

    if "/" in pat:
        # Anchored to the context root.
        if is_dir_pattern:
            return relpath == pat or relpath.startswith(pat + "/")
        return fnmatch.fnmatch(relpath, pat)

    # Basename glob — matches any single path component, at any depth.
    if is_dir_pattern:
        return any(fnmatch.fnmatch(part, pat) for part in parts[:-1]) or (
            len(parts) >= 1 and fnmatch.fnmatch(parts[-1], pat)
        )
    return any(fnmatch.fnmatch(part, pat) for part in parts)


def classify(relpath: str, patterns: list[tuple[bool, str]]) -> tuple[bool, str | None]:
    """Returns (excluded, last_matching_pattern)."""
    excluded = False
    last = None
    for neg, pat in patterns:
        if pattern_matches(pat, relpath):
            excluded = neg is False
            last = ("!" if neg else "") + pat
    return excluded, last


def walk_all_files(root: Path) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_PRUNE]
        for f in filenames:
            p = Path(dirpath, f).relative_to(root)
            out.append(p.as_posix())
    return sorted(out)


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
    dockerignore = root / ".dockerignore"
    patterns = load_patterns(dockerignore)
    all_files = walk_all_files(root)

    included, excluded_map = [], {}
    for rel in all_files:
        excl, last = classify(rel, patterns)
        if excl:
            excluded_map.setdefault(last, []).append(rel)
        else:
            included.append(rel)

    manifest_path = root / "docker" / "context.manifest"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        "\n".join(included) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print(f"repo root         : {root}")
    print(f".dockerignore     : {len(patterns)} pattern lines")
    print(f"files on disk     : {len(all_files)}")
    print(f"INCLUDED in context (written to docker/context.manifest): {len(included)}")
    print(f"EXCLUDED by .dockerignore: {sum(len(v) for v in excluded_map.values())}")
    print()
    print("Exclusions by pattern (top offenders):")
    for pat, files in sorted(excluded_map.items(), key=lambda kv: -len(kv[1])):
        print(f"  {pat!r:32} -> {len(files)} file(s), e.g. {files[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
