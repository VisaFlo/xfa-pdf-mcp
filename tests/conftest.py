"""Shared test configuration and fixtures."""

import os
from pathlib import Path

import pytest

# Test PDF can be set via environment variable or defaults to ~/Downloads/imm5257e.pdf
TEST_PDF_PATH = os.environ.get(
    "XFA_TEST_PDF",
    str(Path.home() / "Downloads" / "imm5257e.pdf"),
)

DOWNLOADS_DIR = os.environ.get(
    "XFA_TEST_DOWNLOADS",
    str(Path.home() / "Downloads"),
)


def get_test_pdf() -> Path:
    """Get the path to the test PDF, skipping if not available."""
    path = Path(TEST_PDF_PATH)
    if not path.exists():
        pytest.skip(f"Test PDF not found: {path}. Set XFA_TEST_PDF env var.")
    return path


def get_downloads_dir() -> Path:
    """Get the downloads directory for integration tests."""
    return Path(DOWNLOADS_DIR)
