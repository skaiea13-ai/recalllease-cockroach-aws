from __future__ import annotations

import subprocess

from scripts.stage_lambda import stage_lambda_source
from scripts.verify_runtime_requirements import (
    ROOT,
    resolve_external_executable,
    verify_runtime_requirements,
)


def build() -> None:
    verify_runtime_requirements()
    sam = resolve_external_executable("sam")
    stage_lambda_source()
    subprocess.run(  # noqa: S603 - fixed executable and argument list
        [sam, "build", "--template-file", "infra/template.yaml"],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    build()
