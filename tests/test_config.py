"""Unit tests for department_lib.config."""
from __future__ import annotations

import pytest

from department_lib.config import ConfigError, load_departments


def _raw(dept_id="dev", **over):
    entry = {
        "id": dept_id,
        "name": "Development",
        "manager": {"profile": f"{dept_id}-manager", "platform": "api_server",
                    "chat_id": f"api-{dept_id}-mgr"},
        "members": ["worker-a"],
        "reviewers": ["reviewer-a"],
        "max_repair_rounds": 2,
    }
    entry.update(over)
    return entry


def test_empty_config_is_empty_mapping():
    assert load_departments(None) == {}
    assert load_departments({}) == {}
    assert load_departments({"departments": []}) == {}


def test_loads_multiple_departments_keyed_by_id():
    depts = load_departments({"departments": [_raw("dev"), _raw("ops")]})
    assert set(depts) == {"dev", "ops"}
    assert depts["ops"].manager_chat_id == "api-ops-mgr"


def test_duplicate_department_id_is_rejected():
    with pytest.raises(ConfigError) as exc:
        load_departments({"departments": [_raw("dev"), _raw("dev")]})
    assert "duplicate" in str(exc.value)


def test_same_manager_profile_may_own_one_department_only():
    shared = {"profile": "dept-manager", "platform": "api_server", "chat_id": "api-dev-mgr"}
    other = {"profile": "dept-manager", "platform": "api_server", "chat_id": "api-ops-mgr"}
    with pytest.raises(ConfigError) as exc:
        load_departments({"departments": [_raw("dev", manager=shared), _raw("ops", manager=other)]})
    assert "manager" in str(exc.value)


def test_duplicate_manager_session_is_rejected():
    with pytest.raises(ConfigError):
        load_departments({"departments": [_raw("dev"), _raw("ops", manager={
            "profile": "other-manager", "platform": "api_server", "chat_id": "api-dev-mgr"})]})


def test_bad_entry_is_rejected_with_index_context():
    with pytest.raises(ConfigError) as exc:
        load_departments({"departments": [_raw("dev"), {"id": "ops"}]})
    assert "departments[1]" in str(exc.value)


def test_departments_must_be_a_list():
    with pytest.raises(ConfigError):
        load_departments({"departments": {"dev": _raw("dev")}})


def test_inactive_department_is_parsed_but_flagged():
    depts = load_departments({"departments": [_raw("dev", active=False)]})
    assert depts["dev"].active is False


def test_single_department_may_be_given_as_mapping_form():
    depts = load_departments({"departments": _raw("dev")})
    assert list(depts) == ["dev"]
