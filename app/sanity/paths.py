"""Single source of truth for "where is the project root" — used by the
cleanup audit (needs to walk the whole repo) and the CLI. Derived from
this file's own location rather than the process's current working
directory, so `python -m app.sanity` works the same regardless of where
it's invoked from."""

from pathlib import Path


def repo_root() -> Path:
    # app/sanity/paths.py -> app/sanity -> app -> repo root
    return Path(__file__).resolve().parents[2]
