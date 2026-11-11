"""Sphinx configuration for the public Async ORM Kit reference."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from async_orm_kit import __version__  # noqa: E402

project = "Async ORM Kit"
author = "Async ORM Kit contributors"
release = __version__
version = release
copyright = "2026, Async ORM Kit contributors"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.autosectionlabel",
]
autosectionlabel_prefix_document = True
autodoc_typehints = "none"
autodoc_member_order = "bysource"
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False

root_doc = "index"
source_suffix = ".rst"
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
html_theme = "sphinx_rtd_theme"
html_title = "Async ORM Kit Documentation"
html_show_sourcelink = True
html_static_path = []
html_theme_options = {
    "navigation_depth": 4,
    "collapse_navigation": False,
    "sticky_navigation": True,
}

# Optional integrations are not imported until their functions are called.
# The package and SQLAlchemy are installed by the Read the Docs build.
