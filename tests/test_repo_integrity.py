"""Repo-integrity checks for committed support files and documented surfaces."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_required_example_and_support_files_exist():
    """Quickstart and runtime support files should exist in the repo."""
    required_paths = [
        REPO_ROOT / ".env.example",
        REPO_ROOT / "config.yaml.example",
        REPO_ROOT / "src" / "utils.py",
    ]

    missing_paths = [path for path in required_paths if not path.exists()]
    assert not missing_paths, f"Missing required files: {missing_paths}"


def test_documented_quickstart_files_exist():
    """README quickstart references should resolve to real files."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "cp config.yaml.example config.yaml" in readme
    assert "cp .env.example .env" in readme
    assert (REPO_ROOT / "config.yaml.example").exists()
    assert (REPO_ROOT / ".env.example").exists()
