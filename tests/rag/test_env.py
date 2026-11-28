"""Baseline environment and workspace layout smoke tests"""

import importlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_python_version_supported() -> None:
    """Ensure Python runtime is 3.11 or newer."""
    assert sys.version_info >= (3, 11)


def test_core_packages_importable() -> None:
    """Verify core dependencies required for RAG harness are importable."""
    for pkg in ("pydantic", "pytest", "pypdf", "qdrant_client"):
        module = importlib.import_module(pkg)
        assert module is not None


def test_rag_workspace_structure() -> None:
    """Verify required directories and textbook PDF exist at expected paths."""
    required_dirs = [
        PROJECT_ROOT / "data" / "raw_pdfs",
        PROJECT_ROOT / "data" / "processed",
        PROJECT_ROOT / "data" / "indexes",
        PROJECT_ROOT / "src" / "rag",
        PROJECT_ROOT / "tests" / "rag",
    ]
    for directory in required_dirs:
        assert directory.is_dir(), f"Missing required directory: {directory}"

    sgk_pdf = PROJECT_ROOT / "data" / "raw_pdfs" / "sgk-tin-hoc-10-kntt.pdf"
    assert sgk_pdf.is_file(), f"Missing textbook PDF at {sgk_pdf}"
