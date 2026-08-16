"""Tests for app.sanity.cleanup_audit — the project file/dependency
cleanup audit.

Built against a synthetic fixture repo (not the real one) so these
assertions stay stable regardless of how the actual project's file list
changes over time. The audit's #1 job is to never falsely flag an
active file as removable — most of these tests exist to prove exactly
that, not to prove it finds real issues."""

from pathlib import Path

from app.sanity.cleanup_audit import CleanupClassification, run_cleanup_audit


def _write(root: Path, rel_path: str, content: str = "# content\n") -> None:
    full = root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def _classifications_for(report: object, path: str) -> list[str]:
    return [f.classification.value for f in report.findings if f.path == path]  # type: ignore[attr-defined]


def test_actively_imported_module_is_never_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "app/__init__.py", "")
    _write(tmp_path, "app/widgets/__init__.py", "")
    _write(tmp_path, "app/widgets/gadget.py", "class Gadget:\n    pass\n")
    _write(tmp_path, "app/main.py", "from app.widgets.gadget import Gadget\n")

    report = run_cleanup_audit(tmp_path)

    assert _classifications_for(report, "app/widgets/gadget.py") == []


def test_module_referenced_only_via_dotted_cli_invocation_is_not_flagged(tmp_path: Path) -> None:
    """`python -m app.tools.backfill` in a script/README is a real usage
    even though no .py file ever writes `import app.tools.backfill`."""
    _write(tmp_path, "app/__init__.py", "")
    _write(tmp_path, "app/tools/__init__.py", "")
    _write(tmp_path, "app/tools/backfill.py", "def main(): pass\n")
    _write(tmp_path, "scripts/run_backfill.sh", "python -m app.tools.backfill\n")

    report = run_cleanup_audit(tmp_path)

    assert _classifications_for(report, "app/tools/backfill.py") == []


def test_module_referenced_only_in_docker_compose_is_not_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "app/__init__.py", "")
    _write(tmp_path, "app/workers/__init__.py", "")
    _write(tmp_path, "app/workers/ingest.py", "def run(): pass\n")
    _write(
        tmp_path,
        "docker-compose.yml",
        "services:\n  worker:\n    command: python -m app.workers.ingest\n",
    )

    report = run_cleanup_audit(tmp_path)

    assert _classifications_for(report, "app/workers/ingest.py") == []


def test_test_files_are_never_flagged_regardless_of_references(tmp_path: Path) -> None:
    _write(tmp_path, "tests/test_something.py", "def test_x(): assert True\n")

    report = run_cleanup_audit(tmp_path)

    assert _classifications_for(report, "tests/test_something.py") == []


def test_migrations_are_never_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "alembic/versions/abc123_add_table.py", "def upgrade(): pass\n")

    report = run_cleanup_audit(tmp_path)

    assert _classifications_for(report, "alembic/versions/abc123_add_table.py") == []


def test_entrypoint_files_are_never_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "app/main.py", "app = object()\n")
    _write(tmp_path, "app/sanity/__main__.py", "print('sanity')\n")

    report = run_cleanup_audit(tmp_path)

    assert _classifications_for(report, "app/main.py") == []
    assert _classifications_for(report, "app/sanity/__main__.py") == []


def test_files_with_same_basename_in_different_packages_are_not_flagged_as_duplicates(
    tmp_path: Path,
) -> None:
    """The real false-positive this project's own codebase surfaced
    during development: one models.py/engine.py per subpackage is normal
    Python convention, not duplication."""
    _write(tmp_path, "app/api/health.py", "def a(): return 1\n")
    _write(tmp_path, "app/schemas/health.py", "def b(): return 2\n")
    _write(tmp_path, "app/api/__init__.py", "from app.api import health\n")
    _write(tmp_path, "app/schemas/__init__.py", "from app.schemas import health\n")

    report = run_cleanup_audit(tmp_path)

    for path in ("app/api/health.py", "app/schemas/health.py"):
        classifications = _classifications_for(report, path)
        assert CleanupClassification.POSSIBLE_DUPLICATE.value not in classifications
        assert CleanupClassification.CONFIRMED_DUPLICATE.value not in classifications


def test_byte_identical_files_are_confirmed_duplicates(tmp_path: Path) -> None:
    identical_content = "SAME_CONTENT = 1\n"
    _write(tmp_path, "app/a/copy_one.py", identical_content)
    _write(tmp_path, "app/b/copy_two.py", identical_content)

    report = run_cleanup_audit(tmp_path)

    assert CleanupClassification.CONFIRMED_DUPLICATE.value in _classifications_for(
        report, "app/a/copy_one.py"
    )
    assert CleanupClassification.CONFIRMED_DUPLICATE.value in _classifications_for(
        report, "app/b/copy_two.py"
    )


def test_never_marks_confirmed_unused_by_this_heuristic_alone(tmp_path: Path) -> None:
    """The classifier never jumps straight to CONFIRMED_UNUSED off a
    plain reference-count scan — that tier is reserved for stronger
    evidence than this module can gather; an unreferenced file is
    reported LIKELY_UNUSED, an explicitly softer claim."""
    _write(tmp_path, "app/orphan/__init__.py", "")
    _write(tmp_path, "app/orphan/nothing_calls_this.py", "def f(): pass\n")

    report = run_cleanup_audit(tmp_path)

    classifications = _classifications_for(report, "app/orphan/nothing_calls_this.py")
    assert CleanupClassification.LIKELY_UNUSED.value in classifications
    assert CleanupClassification.CONFIRMED_UNUSED.value not in classifications


def test_genuinely_unreferenced_file_is_flagged_likely_unused(tmp_path: Path) -> None:
    _write(tmp_path, "app/orphan/__init__.py", "")
    _write(tmp_path, "app/orphan/nothing_calls_this.py", "def f(): pass\n")
    _write(tmp_path, "app/main.py", "x = 1\n")  # never references the orphan

    report = run_cleanup_audit(tmp_path)

    assert CleanupClassification.LIKELY_UNUSED.value in _classifications_for(
        report, "app/orphan/nothing_calls_this.py"
    )


def test_legacy_naming_marker_is_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "app/scanner_old.py", "def f(): pass\n")

    report = run_cleanup_audit(tmp_path)

    assert CleanupClassification.LEGACY.value in _classifications_for(report, "app/scanner_old.py")


def test_findings_never_recommend_automatic_removal(tmp_path: Path) -> None:
    _write(tmp_path, "app/orphan/__init__.py", "")
    _write(tmp_path, "app/orphan/nothing_calls_this.py", "def f(): pass\n")

    report = run_cleanup_audit(tmp_path)

    for finding in report.findings:
        assert "auto" not in finding.recommended_action.lower() or "never" in finding.recommended_action.lower()
        assert "delete" not in finding.recommended_action.lower()


def test_dependency_check_recognizes_from_import_style(tmp_path: Path) -> None:
    """The real bug this project's own build hit: `from fastapi import X`
    must count as usage, not only the rare bare `import fastapi`."""
    _write(tmp_path, "requirements.txt", "fastapi==0.100.0\nunused_package_xyz==1.0.0\n")
    _write(tmp_path, "app/main.py", "from fastapi import APIRouter\n")

    report = run_cleanup_audit(tmp_path)

    flagged = {f.path for f in report.unused_dependency_candidates}
    assert "requirements.txt:fastapi" not in flagged
    assert "requirements.txt:unused_package_xyz" in flagged


def test_audit_never_writes_or_deletes_anything(tmp_path: Path) -> None:
    _write(tmp_path, "app/orphan/__init__.py", "")
    _write(tmp_path, "app/orphan/nothing_calls_this.py", "def f(): pass\n")

    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    run_cleanup_audit(tmp_path)
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    assert before == after


def test_real_repository_never_flags_known_active_files() -> None:
    """Guard against a regression against the actual project, not just
    a synthetic fixture — these files are unambiguously live (registered
    scanners, the app entrypoint, a scheduler job)."""
    from app.sanity.paths import repo_root

    report = run_cleanup_audit(repo_root())

    must_never_be_flagged_unused = {
        "app/main.py",
        "app/scanner/breakout_scanner.py",
        "app/scheduler/momentum_pipeline_jobs.py",
        "app/sanity/service.py",
        "app/sanity/checks.py",
    }
    unused_paths = {
        f.path
        for f in report.findings
        if f.classification
        in (CleanupClassification.LIKELY_UNUSED, CleanupClassification.CONFIRMED_UNUSED)
    }
    assert not (must_never_be_flagged_unused & unused_paths)
