from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"


def resolve_external_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"{name} is required")
    resolved = Path(executable).resolve()
    if resolved.is_relative_to(ROOT.resolve()):
        raise RuntimeError(f"Refusing repository-local {name} executable: {resolved}")
    return str(resolved)


def export_runtime_requirements() -> bytes:
    uv = resolve_external_executable("uv")
    result = subprocess.run(  # noqa: S603 - fixed executable and argument list
        [
            uv,
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements.txt",
            "--no-header",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return result.stdout


def verify_runtime_requirements() -> None:
    expected = export_runtime_requirements()
    actual = REQUIREMENTS.read_bytes()
    if actual != expected:
        raise RuntimeError(
            "requirements.txt is stale; regenerate it with "
            "`uv export --frozen --no-dev --no-emit-project --format "
            "requirements.txt --no-header --output-file requirements.txt`"
        )


if __name__ == "__main__":
    verify_runtime_requirements()
    print("Lambda runtime requirements match the frozen uv lock.")
