"""
Root test setup, applied to every module's tests.

Every test runs inside a fake AWS (moto), so nothing touches a real account.
Only AWS SDK credentials/region are set here. App settings are NOT set
through environment variables; each module's conftest injects what it needs.
"""
from pathlib import Path

import pytest
from moto import mock_aws

# App settings documented in .env.example: tests must not pick them up from your shell.
APP_SETTINGS = [line.split("=")[0] for line in (Path(__file__).parent / ".env.example").read_text().splitlines()
                if "=" in line and not line.startswith("#")]


@pytest.fixture(autouse=True)
def fake_aws(monkeypatch, tmp_path):
    for name in APP_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-southeast-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("POWERTOOLS_SERVICE_NAME", "saas-tests")
    # Run each test in an empty folder, so a local .env file can't change settings.
    monkeypatch.chdir(tmp_path)
    with mock_aws():
        yield
