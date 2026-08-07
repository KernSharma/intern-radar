import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture() -> Any:
    def _load(name: str) -> Any:
        with (FIXTURES / name).open("r", encoding="utf-8") as f:
            return json.load(f)

    return _load


@pytest.fixture
def load_text_fixture() -> Any:
    """Load a fixture verbatim — for boards served as HTML rather than JSON."""

    def _load(name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    return _load
