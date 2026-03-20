"""Placeholder integration test — ensures pytest discovers this directory and passes."""
import pytest


@pytest.mark.skipif(True, reason="Integration tests require deployed staging environment")
def test_staging_health():
    """Will test staging endpoint health once deploy pipeline is green."""
    pass
