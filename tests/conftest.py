"""Pytest fixtures and test utilities for Nano-Claw."""

import pytest
import tempfile
import shutil
from pathlib import Path
from src.context import Context
from src.tools import ToolRegistry
from src.tools.read import ReadTool
from src.tools.write import WriteTool
from src.tools.bash import BashTool


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    temp_path = Path(tempfile.mkdtemp())
    yield temp_path
    shutil.rmtree(temp_path)


@pytest.fixture
def test_context(temp_dir):
    """Create a test context with temp directory as cwd."""
    return Context(cwd=temp_dir)


@pytest.fixture
def tool_registry():
    """Create a tool registry with all standard tools."""
    registry = ToolRegistry()
    registry.register(ReadTool())
    registry.register(WriteTool())
    registry.register(BashTool())
    return registry


@pytest.fixture
def sample_file(temp_dir):
    """Create a sample test file."""
    file_path = temp_dir / "test.txt"
    file_path.write_text("Hello\nWorld\nTest")
    return file_path
