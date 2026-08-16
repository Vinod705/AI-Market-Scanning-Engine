"""Project file/dependency cleanup audit — AUDIT ONLY, never deletes or
modifies anything, ever. See this module's classification taxonomy below
and the "Important safety rule" this was built against: a file must not
be classified as unused merely because it has no obvious Python import —
this scans the *entire* repository as text (config files, Docker,
.env.example, docs, scripts, tests — not just other .py files' `import`
statements) specifically so dynamic imports, CLI entry points, scheduled
jobs, Docker/service configuration, migrations, and test discovery all
count as real references.

Deliberately heuristic, not exhaustive static analysis (no AST-level
call-graph, no bytecode tracing) — this project's own instruction was to
keep this "small, reliable, read-only, and focused," not build a second
static-analysis platform. Every non-trivial finding is reported at LOW or
MEDIUM confidence unless it's something unambiguous (byte-identical file
content) — false positives here (flagging something active as
removable) are the failure mode this module is built to avoid, so it
errs toward under-confidence rather than over-confidence.
"""

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

# Directories/files never scanned as *candidates* (but their names are
# still searched as reference text for other candidates) — these are
# structurally always "used" by their role in the project, regardless of
# whether anything imports them by dotted path.
_EXEMPT_DIR_NAMES = {
    ".git", ".venv", "venv", "__pycache__", "node_modules", "logs",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "htmlcov",
}  # fmt: skip
_EXEMPT_CANDIDATE_PATTERNS = (
    "__init__.py",
    "conftest.py",
    "alembic/env.py",
    "alembic/versions/",  # migrations are entry points via revision chain, never imported by name
    "tests/",  # test files are pytest-discovered, never referenced by dotted path elsewhere
)
_ENTRYPOINT_BASENAMES = {"main.py", "__main__.py"}
# File extensions worth full-text scanning for *references* (broader than
# the .py-only candidate set — a docker-compose.yml or .env.example
# mentioning a module counts as a real usage).
_REFERENCE_SCAN_EXTENSIONS = {
    ".py", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".md", ".sh", ".txt", ".example",
}  # fmt: skip
_CANDIDATE_EXTENSIONS = {".py"}
_LEGACY_NAME_MARKERS = ("old", "legacy", "deprecated", "obsolete", "backup", "_v0", "unused")


class CleanupClassification(StrEnum):
    CONFIRMED_UNUSED = "CONFIRMED_UNUSED"
    LIKELY_UNUSED = "LIKELY_UNUSED"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
    CONFIRMED_DUPLICATE = "CONFIRMED_DUPLICATE"
    LEGACY = "LEGACY"
    UNKNOWN = "UNKNOWN"


@dataclass
class CleanupFinding:
    path: str  # repo-relative, forward-slash
    classification: CleanupClassification
    confidence: str  # "HIGH" | "MEDIUM" | "LOW"
    reason: str
    references: list[str] = field(default_factory=list)
    recommended_action: str = "Manually verify before considering any change — never auto-removed."


@dataclass
class CleanupAuditReport:
    scanned_files: int
    findings: list[CleanupFinding] = field(default_factory=list)
    unused_dependency_candidates: list[CleanupFinding] = field(default_factory=list)

    @property
    def unused_count(self) -> int:
        return sum(
            1
            for f in self.findings
            if f.classification in (CleanupClassification.CONFIRMED_UNUSED, CleanupClassification.LIKELY_UNUSED)
        )

    @property
    def duplicate_count(self) -> int:
        return sum(
            1
            for f in self.findings
            if f.classification
            in (CleanupClassification.CONFIRMED_DUPLICATE, CleanupClassification.POSSIBLE_DUPLICATE)
        )

    @property
    def legacy_count(self) -> int:
        return sum(1 for f in self.findings if f.classification == CleanupClassification.LEGACY)


def _is_exempt_candidate(rel_path: str) -> bool:
    if any(part in _EXEMPT_DIR_NAMES for part in Path(rel_path).parts):
        return True
    if Path(rel_path).name in _ENTRYPOINT_BASENAMES:
        return True
    return any(pattern in rel_path for pattern in _EXEMPT_CANDIDATE_PATTERNS)


def _iter_files(root: Path, extensions: set[str]) -> list[Path]:
    out = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _EXEMPT_DIR_NAMES for part in path.parts):
            continue
        if path.suffix in extensions:
            out.append(path)
    return out


def _module_reference_tokens(rel_path: str) -> list[str]:
    """Strings worth grepping for elsewhere in the repo to see if this
    file is referenced: its dotted module path (app.foo.bar), its bare
    filename, and its filename without extension — covers `import
    app.foo.bar`, `from app.foo import bar`, a CLI `python -m app.foo`,
    a scheduler job string, a Docker CMD, or a plain filename mention in
    docs/scripts."""
    p = Path(rel_path)
    stem = p.stem
    dotted = rel_path.removesuffix(".py").replace("/", ".").replace("\\", ".")
    return [dotted, p.name, stem]


def _count_references(token: str, self_path: str, all_text_by_path: dict[str, str]) -> list[str]:
    hits = []
    for path, text in all_text_by_path.items():
        if path == self_path:
            continue
        if token and token in text:
            hits.append(path)
    return hits


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_cleanup_audit(repo_root: Path) -> CleanupAuditReport:
    """The main entry point. `repo_root` must be the project's top-level
    directory (containing `app/`, `pyproject.toml`, etc). Pure read —
    opens files for reading only, writes nothing anywhere."""
    reference_files = _iter_files(repo_root, _REFERENCE_SCAN_EXTENSIONS)
    all_text_by_path: dict[str, str] = {}
    for path in reference_files:
        rel = path.relative_to(repo_root).as_posix()
        try:
            all_text_by_path[rel] = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

    candidate_files = [
        p for p in reference_files if p.suffix in _CANDIDATE_EXTENSIONS and p.name != "__init__.py"
    ]

    findings: list[CleanupFinding] = []
    hash_to_paths: dict[str, list[str]] = {}

    for path in candidate_files:
        rel = path.relative_to(repo_root).as_posix()
        try:
            file_hash = _sha256(path)
        except OSError:
            continue
        hash_to_paths.setdefault(file_hash, []).append(rel)

    # --- CONFIRMED_DUPLICATE: byte-identical content ---
    for _file_hash, paths in hash_to_paths.items():
        if len(paths) < 2:
            continue
        for rel in paths:
            others = [p for p in paths if p != rel]
            findings.append(
                CleanupFinding(
                    path=rel,
                    classification=CleanupClassification.CONFIRMED_DUPLICATE,
                    confidence="HIGH",
                    reason="Byte-identical content to another file in the repository.",
                    references=others,
                    recommended_action=(
                        "Confirm both copies are intentional (e.g. a template) before "
                        "consolidating — never auto-merged."
                    ),
                )
            )

    flagged_paths = {f.path for f in findings}

    # NOTE: deliberately NOT flagging same-basename files across different
    # directories as POSSIBLE_DUPLICATE — this project (like most Python
    # projects) has one `models.py`/`engine.py`/`scorer.py` per subpackage
    # by design (app/api/health.py vs app/schemas/health.py vs
    # app/scanner/models.py vs app/candidates/models.py, etc.). Confirmed
    # live: that heuristic produced dozens of false positives against this
    # codebase's real, healthy structure. Basename reuse across
    # directories is normal Python package convention, not a duplication
    # signal — genuine near-duplicates are instead caught by the LEGACY
    # naming-marker check below (foo_old.py, foo_v2.py, etc. next to foo.py).

    # --- LEGACY: naming heuristic ---
    for path in candidate_files:
        rel = path.relative_to(repo_root).as_posix()
        if rel in flagged_paths or _is_exempt_candidate(rel):
            continue
        lowered = rel.lower()
        if any(marker in lowered for marker in _LEGACY_NAME_MARKERS):
            findings.append(
                CleanupFinding(
                    path=rel,
                    classification=CleanupClassification.LEGACY,
                    confidence="LOW",
                    reason=f"Filename/path suggests legacy status (matched: "
                    f"{next(m for m in _LEGACY_NAME_MARKERS if m in lowered)!r}) — "
                    "naming heuristic only, not a usage analysis.",
                )
            )
            flagged_paths.add(rel)

    # --- UNUSED: zero references anywhere in the repo's text ---
    for path in candidate_files:
        rel = path.relative_to(repo_root).as_posix()
        if rel in flagged_paths or _is_exempt_candidate(rel):
            continue

        tokens = _module_reference_tokens(rel)
        all_hits: set[str] = set()
        for token in tokens:
            all_hits.update(_count_references(token, rel, all_text_by_path))

        if not all_hits:
            findings.append(
                CleanupFinding(
                    path=rel,
                    classification=CleanupClassification.LIKELY_UNUSED,
                    confidence="MEDIUM",
                    reason=(
                        "No references found anywhere in the repository's text "
                        "(source, config, docs, scripts, tests) under its module path, "
                        "filename, or stem — reported LIKELY not CONFIRMED, since this "
                        "scan cannot see references generated purely at runtime."
                    ),
                )
            )

    findings.sort(key=lambda f: f.path)

    dependency_findings = _check_unused_dependencies(repo_root, all_text_by_path)

    return CleanupAuditReport(
        scanned_files=len(candidate_files),
        findings=findings,
        unused_dependency_candidates=dependency_findings,
    )


def _check_unused_dependencies(
    repo_root: Path, all_text_by_path: dict[str, str]
) -> list[CleanupFinding]:
    """Best-effort, explicitly LOW-confidence: package name vs. import
    name commonly differ (e.g. `psycopg2-binary` imports as `psycopg2`),
    which this only partially normalizes for — never treat a hit here as
    definitive without manually checking the actual import."""
    findings: list[CleanupFinding] = []
    combined_source_text = "\n".join(
        text for path, text in all_text_by_path.items() if path.endswith(".py")
    )

    for req_file in ("requirements.txt", "requirements-dev.txt"):
        req_path = repo_root / req_file
        if not req_path.exists():
            continue
        for raw_line in req_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            package_name = line.split("==")[0].split(">=")[0].split("[")[0].strip()
            if not package_name:
                continue
            import_candidates = {
                package_name.replace("-", "_"),
                package_name.replace("-", ""),
                package_name.split("-")[0],
            }
            found = any(
                f"import {candidate}" in combined_source_text  # `import fastapi`
                or f"from {candidate}" in combined_source_text  # `from fastapi import X`
                or f"from {candidate}." in combined_source_text  # `from fastapi.responses import X`
                for candidate in import_candidates
            )
            if found:
                continue
            findings.append(
                CleanupFinding(
                    path=f"{req_file}:{package_name}",
                    classification=CleanupClassification.UNKNOWN,
                    confidence="LOW",
                    reason=(
                        f"No `import {{name derived from '{package_name}'}}` found in any .py "
                        "file — package/import names often differ, so this is a weak signal only."
                    ),
                    recommended_action="Manually confirm the actual import name before assuming unused.",
                )
            )
    return findings
