import re
import shutil
from pathlib import Path

import pytest

import scripts.stage_lambda as stage_module
import scripts.verify_runtime_requirements as requirements_module
from scripts.build_static_demo import build_static_demo
from scripts.stage_lambda import stage_lambda_source
from scripts.verify_runtime_requirements import verify_runtime_requirements

ROOT = Path(__file__).resolve().parent.parent


def test_lambda_runtime_requirements_match_the_frozen_lock() -> None:
    verify_runtime_requirements()


def test_unreviewable_local_environment_template_is_not_public_source() -> None:
    ignored = (ROOT / ".gitignore").read_text().splitlines()

    assert ".env" in ignored
    assert ".env.example" in ignored


def test_cloud_function_url_requires_iam_before_invocation() -> None:
    template = (ROOT / "infra" / "template.yaml").read_text()

    assert "AuthType: AWS_IAM" in template
    assert "AuthType: NONE" not in template
    assert 'RECALLLEASE_PUBLIC_SESSION_LIMIT_PER_HOUR: "20"' in template
    assert 'RECALLLEASE_SESSION_USE_LIMIT: "8"' in template
    assert "RECALLLEASE_EMBEDDING_BACKEND: deterministic" in template
    assert "bedrock:InvokeModel" not in template
    assert "CodeUri: ../build/lambda" in template
    assert "CodeUri: ../\n" not in template
    assert "CockroachDatabaseUrlParameterName:" in template
    assert "RECALLLEASE_DATABASE_URL_PARAMETER:" in template
    assert "RECALLLEASE_DATABASE_URL:" not in template
    assert "ssm:GetParameter" in template


def test_lambda_stage_contains_only_runtime_allowlist(tmp_path: Path) -> None:
    staged = stage_lambda_source(tmp_path / "lambda")
    inventory = {
        path.relative_to(staged).as_posix() for path in staged.rglob("*") if path.is_file()
    }
    top_level = {path.parts[0] for path in map(Path, inventory)}

    assert top_level == {"frontend", "main.py", "recalllease", "requirements.txt"}
    assert not any("__pycache__" in path for path in inventory)
    assert not any(path.endswith((".pyc", ".pyo")) for path in inventory)
    assert not any(Path(path).name.startswith(".env") for path in inventory)
    assert not ({"tests", "docs", "design"} & top_level)
    assert "frontend/static-replay.js" not in inventory


def test_lambda_stage_excludes_an_untracked_descendant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy2(ROOT / "main.py", source / "main.py")
    shutil.copy2(ROOT / "requirements.txt", source / "requirements.txt")
    shutil.copytree(ROOT / "recalllease", source / "recalllease")
    shutil.copytree(ROOT / "frontend", source / "frontend")
    (source / "frontend" / "private-notes.txt").write_text("must not ship")
    monkeypatch.setattr(stage_module, "ROOT", source)

    staged = stage_module.stage_lambda_source(tmp_path / "isolated-stage")

    assert not (staged / "frontend" / "private-notes.txt").exists()


def test_repository_local_build_tool_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        requirements_module.shutil,
        "which",
        lambda _name: str(ROOT / "scripts" / "untrusted-tool"),
    )

    with pytest.raises(RuntimeError, match="repository-local"):
        requirements_module.resolve_external_executable("uv")


def test_static_demo_contains_only_browser_replay_allowlist(tmp_path: Path) -> None:
    built = build_static_demo(tmp_path / "site")
    inventory = {path.relative_to(built).as_posix() for path in built.rglob("*") if path.is_file()}
    index = (built / "index.html").read_text()

    assert inventory == {
        "assets/app.js",
        "assets/favicon.svg",
        "assets/static-replay.js",
        "assets/styles.css",
        "index.html",
    }
    assert '<meta name="recalllease-mode" content="static-replay" />' in index
    assert "connect-src 'none'" in index
    assert '<meta name="referrer" content="no-referrer" />' in index
    assert index.index("static-replay.js") < index.index("app.js")
    assert 'href="./assets/' in index
    assert 'src="./assets/' in index
    assert 'href="/assets/' not in index
    assert 'src="/assets/' not in index
    assert "fetch(" not in (built / "assets" / "static-replay.js").read_text()
    combined = "\n".join(path.read_text() for path in built.rglob("*") if path.is_file())
    assert "/Users/" not in combined
    assert "/Volumes/" not in combined
    assert "@gmail.com" not in combined


def test_loopback_capability_is_read_from_and_removed_from_the_fragment() -> None:
    app = (ROOT / "frontend" / "app.js").read_text()

    assert "window.location.hash" in app
    assert "window.history.replaceState" in app
    assert '"X-RecallLease-Loopback-Capability"' in app
    assert "localStorage" not in app
    assert "sessionStorage" not in app


def test_static_demo_refuses_repository_root_as_destination() -> None:
    with pytest.raises(RuntimeError, match="unsafe static demo destination"):
        build_static_demo(ROOT)


def test_pages_workflow_builds_only_the_static_replay_with_pinned_actions() -> None:
    workflow = (ROOT / ".github" / "workflows" / "deploy-pages.yml").read_text()
    action_refs = re.findall(r"uses: ([^\s]+)", workflow)

    assert "python3 -m scripts.build_static_demo" in workflow
    assert "path: ./dist" in workflow
    assert action_refs == [
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/configure-pages@45bfe0192ca1faeb007ade9deae92b16b8254a0d",
        "actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9",
        "actions/deploy-pages@cd2ce8fcbc39b97be8ca5fce6e763baed58fa128",
    ]
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference) for reference in action_refs)
