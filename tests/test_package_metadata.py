"""Packaging contract tests for Hypernote's shipped product surface."""

from __future__ import annotations

import tomllib
from pathlib import Path


def _dependency_names(dependencies: list[str]) -> set[str]:
    names: set[str] = set()
    for dependency in dependencies:
        requirement = dependency.split(";", 1)[0]
        for separator in ("[", "<", ">", "=", "!", "~", " "):
            requirement = requirement.split(separator, 1)[0]
        names.add(requirement.strip().lower())
    return names


def test_default_install_includes_jupyterlab_integration_stack() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    dependencies = _dependency_names(pyproject["project"]["dependencies"])
    optional_dependencies = pyproject["project"].get("optional-dependencies", {})

    assert "jupyterlab" in dependencies
    assert "jupyter-collaboration" in dependencies
    assert "jupyter-docprovider" in dependencies
    assert "lab" not in optional_dependencies
