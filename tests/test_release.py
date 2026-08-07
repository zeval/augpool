from __future__ import annotations

import re
import tomllib
from pathlib import Path

import augpool


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
README = ROOT / "README.md"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
RELEASE_VERSION = "0.2.0"


def _workflow() -> str:
    assert WORKFLOW.is_file(), "release workflow must exist"
    return WORKFLOW.read_text(encoding="utf-8")


def test_release_version_matches_project_and_runtime() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]

    assert project["version"] == RELEASE_VERSION
    assert augpool.__version__ == RELEASE_VERSION


def test_release_metadata_uses_spdx_and_includes_license() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]

    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]
    assert (ROOT / "LICENSE").is_file()


def test_pypi_release_has_install_docs_and_project_links() -> None:
    metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    readme = README.read_text(encoding="utf-8")

    assert metadata["project"]["urls"] == {
        "Homepage": "https://github.com/zeval/augpool",
        "Repository": "https://github.com/zeval/augpool",
        "Issues": "https://github.com/zeval/augpool/issues",
    }
    assert "pipx install augpool" in readme
    assert "python3 -m pip install --user augpool" in readme


def test_release_workflow_runs_only_for_version_tags_and_checks_version() -> None:
    workflow = _workflow()

    assert re.search(r"(?m)^\s+tags:\s*$", workflow)
    assert re.search(r'(?m)^\s+- ["\']v\*["\']\s*$', workflow)
    assert "GITHUB_REF_NAME#v" in workflow
    assert "package version" in workflow.lower()


def test_release_workflow_tests_builds_and_validates_distributions() -> None:
    workflow = _workflow()

    assert "pytest" in workflow
    assert "python -m build" in workflow
    assert "twine check" in workflow
    assert "actions/upload-artifact@" in workflow
    assert "actions/download-artifact@" in workflow


def test_release_workflow_publishes_to_pypi_then_github() -> None:
    workflow = _workflow()

    assert re.search(r"(?m)^\s+name: pypi\s*$", workflow)
    assert "id-token: write" in workflow
    assert "pypa/gh-action-pypi-publish@" in workflow
    assert re.search(r"(?s)publish-github:.*needs: publish-pypi", workflow)
    assert "contents: write" in workflow
    assert "gh release create" in workflow


def test_release_workflow_pins_actions_to_commit_shas() -> None:
    uses = re.findall(r"(?m)^\s*- uses: ([^\s]+)\s*$", _workflow())

    assert uses
    assert all(re.search(r"@[0-9a-f]{40}$", action) for action in uses)
