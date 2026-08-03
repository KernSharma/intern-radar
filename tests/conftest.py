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
