"""Placeholder smoke test — ensures pytest discovers this directory and passes."""
import pytest


@pytest.mark.skipif(True, reason="Smoke tests require production environment")
def test_production_health():
    """Will test production endpoint health once deploy pipeline is green."""
    pass
