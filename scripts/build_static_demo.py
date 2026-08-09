from __future__ import annotations

import shutil
from pathlib import Path

from scripts.verify_runtime_requirements import ROOT

FRONTEND = ROOT / "frontend"
STATIC_ROOT = ROOT / "dist"
STATIC_ASSETS = ("app.js", "favicon.svg", "static-replay.js", "styles.css")
STATIC_META = (
    '    <meta name="recalllease-mode" content="static-replay" />\n'
    '    <meta name="referrer" content="no-referrer" />\n'
    "    <meta\n"
    '      http-equiv="Content-Security-Policy"\n'
    "      content=\"default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'\"\n"
    "    />\n"
)
APP_SCRIPT = '    <script src="/assets/app.js" defer></script>'
STATIC_SCRIPTS = (
    '    <script src="./assets/static-replay.js" defer></script>\n'
    '    <script src="./assets/app.js" defer></script>'
)


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f"Refusing symlink in static demo source: {path}")


def _validate_destination(destination: Path) -> Path:
    requested = destination.expanduser()
    if requested.is_symlink():
        raise RuntimeError(f"Refusing symlinked static demo destination: {requested}")
    target = requested.resolve()
    frontend = FRONTEND.resolve()
    root = ROOT.resolve()
    home = Path.home().resolve()
    if (
        target == frontend
        or frontend in target.parents
        or target == root
        or target in root.parents
        or target == home
        or target in home.parents
    ):
        raise RuntimeError(f"Refusing unsafe static demo destination: {target}")
    return target


def _render_index() -> str:
    index = FRONTEND / "index.html"
    _reject_symlink(index)
    html = index.read_text()
    charset = '    <meta charset="UTF-8" />\n'
    if html.count(charset) != 1 or html.count(APP_SCRIPT) != 1:
        raise RuntimeError("Static demo markers no longer match frontend/index.html")
    html = html.replace(charset, charset + STATIC_META, 1)
    html = html.replace(APP_SCRIPT, STATIC_SCRIPTS, 1)
    html = html.replace('href="/assets/', 'href="./assets/')
    html = html.replace('href="/"', 'href="./"', 1)
    return html


def _validate_inventory(destination: Path) -> None:
    expected = {"index.html", *(f"assets/{name}" for name in STATIC_ASSETS)}
    inventory = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }
    if inventory != expected:
        unexpected = sorted(inventory.symmetric_difference(expected))
        raise RuntimeError(f"Unexpected static demo inventory: {unexpected}")
    for path in destination.rglob("*"):
        if path.is_symlink() or path.name.startswith(".env") or "__pycache__" in path.parts:
            raise RuntimeError(f"Forbidden static demo path: {path.relative_to(destination)}")


def build_static_demo(destination: Path | None = None) -> Path:
    target = _validate_destination(destination or STATIC_ROOT)
    sources = [FRONTEND / "index.html", *(FRONTEND / name for name in STATIC_ASSETS)]
    for source in sources:
        if not source.is_file():
            raise RuntimeError(f"Missing required static demo source: {source}")
        _reject_symlink(source)

    if target.exists():
        shutil.rmtree(target)
    assets = target / "assets"
    assets.mkdir(parents=True)
    (target / "index.html").write_text(_render_index())
    for name in STATIC_ASSETS:
        shutil.copy2(FRONTEND / name, assets / name)
    _validate_inventory(target)
    return target


if __name__ == "__main__":
    built = build_static_demo()
    print(f"Built browser-only static replay at {built}")
