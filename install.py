#!/usr/bin/env python3
"""Install the reviewed dashboard into this user's local application directory."""
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def copy_payload(source: Path, app: Path) -> None:
    """Copy the distributable files, never local environments or credentials."""
    if source == app:
        return
    app.mkdir(parents=True, exist_ok=True)
    for name in ("dashboard", "tests", "docs", "formatter"):
        if (source / name).is_dir():
            shutil.copytree(source / name, app / name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "node_modules"))
    for name in ("pyproject.toml", "README.md", "install.py", "AGENTS.md", "Brewfile", "uv.lock", "THIRD_PARTY.md"):
        if (source / name).is_file():
            shutil.copy2(source / name, app / name)


def main():
    if sys.platform != "darwin":
        raise SystemExit("This installer targets macOS. The native layout and process monitor require macOS.")
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 or newer is required. Try: uv run --no-project --python 3.13 python install.py")
    source = Path(__file__).resolve().parent
    destination = Path.home() / ".local/share/ghostty-project-dashboard"
    launcher = Path.home() / ".local/bin/dashboard"
    marker = "# Managed by Ghostty Project Dashboard"
    if launcher.exists() and marker not in launcher.read_text(errors="replace"):
        raise SystemExit("An unrelated dashboard command already exists; it was left unchanged.")
    for tool in ("uv", "tmux", "git", "btop", "node", "npm"):
        if not shutil.which(tool):
            raise SystemExit(f"Missing {tool}. Install it with Homebrew before running this installer.")
    destination.mkdir(parents=True, exist_ok=True)
    app = destination / "app"
    copy_payload(source, app)
    subprocess.run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=app / "formatter", check=True)
    venv = destination / "venv"
    if not (venv / "bin/python").exists():
        subprocess.run(["uv", "venv", str(venv), "--python", sys.executable], check=True)
    subprocess.run(["uv", "pip", "install", "--python", str(venv / "bin/python"), str(app)], check=True)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/bin/sh\n" + marker + "\nexec " +
                        shlex.join([str(venv / "bin/python"), "-m", "dashboard.cli"]) + ' "$@"\n')
    launcher.chmod(0o755)
    from dashboard.notes_keys import install as install_notes_keys
    install_notes_keys(Path.home(), destination)
    print(f"Installed: {launcher}")
    print("From a Git project, run: dashboard")
    print('If the command is not found, add to your shell PATH: export PATH="$HOME/.local/bin:$PATH"')


if __name__ == "__main__":
    main()
