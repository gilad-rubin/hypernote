"""Packaging contract tests for Hypernote's shipped product surface."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_default_install_includes_jupyterlab_integration_stack() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    dependencies = set(pyproject["project"]["dependencies"])
    optional_dependencies = pyproject["project"].get("optional-dependencies", {})

    assert "jupyterlab>=4.0" in dependencies
    assert "jupyter-collaboration>=3.0" in dependencies
    assert "jupyter-docprovider>=2.0" in dependencies
    assert "lab" not in optional_dependencies
