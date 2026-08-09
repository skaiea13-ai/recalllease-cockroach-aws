from __future__ import annotations

import shutil
from pathlib import Path

from scripts.verify_runtime_requirements import ROOT

STAGE_ROOT = ROOT / "build" / "lambda"
RUNTIME_FILES = (
    "main.py",
    "requirements.txt",
    "recalllease/__init__.py",
    "recalllease/api.py",
    "recalllease/embeddings.py",
    "recalllease/models.py",
    "recalllease/receipts.py",
    "recalllease/schema.py",
    "recalllease/service.py",
    "recalllease/settings.py",
    "recalllease/store.py",
    "frontend/app.js",
    "frontend/favicon.svg",
    "frontend/index.html",
    "frontend/styles.css",
)


def _validate_inventory(destination: Path) -> None:
    expected = set(RUNTIME_FILES)
    actual = {
        candidate.relative_to(destination).as_posix()
        for candidate in destination.rglob("*")
        if candidate.is_file()
    }
    if actual != expected:
        raise RuntimeError(
            "Lambda staging inventory differs from the reviewed manifest: "
            f"missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
        )
    for candidate in destination.rglob("*"):
        relative = candidate.relative_to(destination)
        if candidate.is_symlink():
            raise RuntimeError(f"Refusing symlink in Lambda staging path: {relative}")


def stage_lambda_source(destination: Path | None = None) -> Path:
    target = (destination or STAGE_ROOT).resolve()
    protected = {Path("/").resolve(), Path.home().resolve(), ROOT.resolve()}
    if target in protected:
        raise RuntimeError(f"Refusing unsafe Lambda staging destination: {target}")

    for relative in RUNTIME_FILES:
        source = ROOT / relative
        if not source.is_file():
            raise RuntimeError(f"Missing required Lambda source: {source}")
        if source.is_symlink():
            raise RuntimeError(f"Refusing symlink in Lambda source: {source}")

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    for relative in RUNTIME_FILES:
        destination_path = target / relative
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination_path)

    _validate_inventory(target)
    return target


if __name__ == "__main__":
    staged = stage_lambda_source()
    print(f"Staged allowlisted Lambda source at {staged}")
