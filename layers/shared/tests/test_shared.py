import pytest
from pydantic import ValidationError

from shared.errors import Forbidden
from shared.auth import CurrentUser
from shared.plans import get_limit
from shared.settings import BaseAppSettings


class ExampleSettings(BaseAppSettings):
    some_table: str


def test_missing_setting_fails_fast(monkeypatch):
    monkeypatch.delenv("SOME_TABLE", raising=False)
    with pytest.raises(ValidationError, match="some_table"):
        ExampleSettings(_env_file=None)


def test_settings_read_environment(monkeypatch):
    monkeypatch.setenv("SOME_TABLE", "orders-dev")
    assert ExampleSettings(_env_file=None).some_table == "orders-dev"


def test_role_check():
    member = CurrentUser("u1", "u1@x.com", "t1", "member", "free")
    with pytest.raises(Forbidden):
        member.require_role("owner", "admin")
    assert member.require_role("member") == "t1"


def test_unknown_plan_falls_back_to_free():
    assert get_limit("nonexistent", "max_users") == get_limit("free", "max_users")
    assert get_limit("business", "max_orders") is None
