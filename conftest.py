"""
Root test setup, applied to every module's tests.

Every test runs inside a fake AWS (moto), so nothing touches a real account.
Only AWS SDK credentials/region are set here. App settings are NOT set
through environment variables; each module's conftest injects what it needs.
"""
import pytest
from moto import mock_aws


@pytest.fixture(autouse=True)
def fake_aws(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-southeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("POWERTOOLS_SERVICE_NAME", "saas-tests")
    with mock_aws():
        yield
