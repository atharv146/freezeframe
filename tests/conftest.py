from pathlib import Path

import pytest

from freezeframe.demo import build_demo
from freezeframe.store import Store

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Never touch the real recording."""
    monkeypatch.setenv("FREEZEFRAME_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory) -> Path:
    return build_demo(tmp_path_factory.mktemp("demo") / "demo.db", end=1_800_000_000.0)


@pytest.fixture
def demo_store(demo_db) -> Store:
    s = Store(demo_db)
    yield s
    s.close()
