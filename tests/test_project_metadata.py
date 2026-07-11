from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_URL = "https://github.com/ToxMCP/admetlab-mcp"


def test_license_metadata_matches_root_license() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    readme = (ROOT / "README.md").read_text()
    citation = (ROOT / "CITATION.cff").read_text()
    license_text = (ROOT / "LICENSE").read_text()

    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert not any(value.startswith("License ::") for value in project["classifiers"])
    assert "Apache License 2.0" in readme
    assert 'license: "Apache-2.0"' in citation
    assert "Apache License" in license_text


def test_project_urls_point_to_current_repository() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    contributing = (ROOT / "CONTRIBUTING.md").read_text()

    assert project["urls"] == {
        "Homepage": REPOSITORY_URL,
        "Repository": REPOSITORY_URL,
        "Issues": f"{REPOSITORY_URL}/issues",
    }
    assert f"{REPOSITORY_URL}/issues" in contributing
    assert "senseibelbi/ADMETlab_MCP" not in contributing
