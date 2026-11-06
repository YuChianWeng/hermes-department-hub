"""Unit tests for department_lib.models."""
from __future__ import annotations

import pytest

from department_lib.models import BoardEvent, ConfigError, Department, canonical_json, digest


def test_canonical_json_is_key_order_stable():
    a = canonical_json({"b": 1, "a": {"y": 2, "x": [3, 4]}})
    b = canonical_json({"a": {"x": [3, 4], "y": 2}, "b": 1})
    assert a == b
    assert a == '{"a":{"x":[3,4],"y":2},"b":1}'


def test_canonical_json_preserves_unicode_and_rejects_non_finite():
    assert "部門" in canonical_json({"name": "部門"})
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_digest_is_sha256_hex_and_content_bound():
    d = digest({"a": 1})
    assert len(d) == 64 and all(c in "0123456789abcdef" for c in d)
    assert d == digest({"a": 1})
    assert d != digest({"a": 2})


# ── Department ──────────────────────────────────────────────────────────────

VALID = {
    "id": "dev",
    "name": "Development",
    "manager": {"profile": "dept-manager", "platform": "api_server", "chat_id": "api-mgr001"},
    "members": ["worker-a"],
    "reviewers": ["reviewer-a"],
    "max_repair_rounds": 2,
}


def test_department_from_mapping_roundtrips():
    dept = Department.from_mapping(VALID)
    assert dept.dept_id == "dev"
    assert dept.manager_profile == "dept-manager"
    assert dept.manager_platform == "api_server"
    assert dept.manager_chat_id == "api-mgr001"
    assert dept.members == ("worker-a",)
    assert dept.reviewers == ("reviewer-a",)
    assert dept.max_repair_rounds == 2
    assert dept.active is True
    again = Department.from_mapping(dept.as_dict())
    assert again == dept


def test_department_defaults_max_repair_rounds_and_active():
    raw = dict(VALID)
    raw.pop("max_repair_rounds")
    dept = Department.from_mapping(raw)
    assert dept.max_repair_rounds == 2
    assert Department.from_mapping({**VALID, "active": False}).active is False


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda r: r.pop("id"), "id"),
        (lambda r: r.update(id="Bad ID!"), "id"),
        (lambda r: r.update(name=""), "name"),
        (lambda r: r.pop("manager"), "manager"),
        (lambda r: r.update(manager={"profile": ""}), "manager.profile"),
        (lambda r: r["manager"].pop("chat_id"), "manager.chat_id"),
        (lambda r: r.update(members=[]), "members"),
        (lambda r: r.update(reviewers=[]), "reviewers"),
        (lambda r: r.update(max_repair_rounds=-1), "max_repair_rounds"),
        (lambda r: r.update(max_repair_rounds="two"), "max_repair_rounds"),
    ],
)
def test_department_rejects_invalid_shapes(mutate, match):
    raw = {"manager": dict(VALID["manager"]), **{k: v for k, v in VALID.items() if k != "manager"}}
    raw["members"] = list(VALID["members"])
    raw["reviewers"] = list(VALID["reviewers"])
    mutate(raw)
    with pytest.raises(ConfigError) as exc:
        Department.from_mapping(raw)
    assert match in str(exc.value)


def test_department_rejects_manager_that_is_also_member_or_reviewer():
    for field in ("members", "reviewers"):
        raw = {**VALID, "manager": dict(VALID["manager"]), "members": list(VALID["members"]),
               "reviewers": list(VALID["reviewers"])}
        raw[field] = list(raw[field]) + ["dept-manager"]
        with pytest.raises(ConfigError):
            Department.from_mapping(raw)


def test_department_rejects_reviewer_who_is_also_an_implementer():
    raw = {**VALID, "manager": dict(VALID["manager"]),
           "members": ["worker-a", "reviewer-a"], "reviewers": ["reviewer-a"]}
    with pytest.raises(ConfigError) as exc:
        Department.from_mapping(raw)
    assert "independent" in str(exc.value)


def test_department_rejects_duplicate_members_and_reviewers():
    with pytest.raises(ConfigError):
        Department.from_mapping({**VALID, "manager": dict(VALID["manager"]),
                                 "members": ["worker-a", "worker-a"],
                                 "reviewers": ["reviewer-a"]})
    with pytest.raises(ConfigError):
        Department.from_mapping({**VALID, "manager": dict(VALID["manager"]),
                                 "members": ["worker-a"],
                                 "reviewers": ["reviewer-a", "reviewer-a"]})


# ── BoardEvent identity ─────────────────────────────────────────────────────

def _ev(**over):
    base = dict(task_id="t_1", kind="completed", run_id=7, created_at=100,
                payload={"summary": "ok"})
    base.update(over)
    return BoardEvent(**base)


def test_event_id_is_stable_and_payload_independent():
    ev = _ev()
    assert ev.event_id == _ev(payload={"summary": "ok"}).event_id
    # identity is built from board coordinates, not from the payload body:
    # a changed payload must keep the same event identity so it can be
    # detected as a conflict instead of silently becoming a new event.
    assert ev.event_id == _ev(payload={"summary": "different"}).event_id
    assert ev.payload_digest != _ev(payload={"summary": "different"}).payload_digest


def test_event_id_changes_with_board_coordinates():
    base = _ev().event_id
    assert _ev(task_id="t_2").event_id != base
    assert _ev(kind="blocked").event_id != base
    assert _ev(run_id=8).event_id != base
    assert _ev(created_at=101).event_id != base
    assert _ev(run_id=None).event_id != base


def test_payload_digest_detects_payload_change():
    assert _ev().payload_digest != _ev(payload={"summary": "changed"}).payload_digest
    assert _ev().payload_digest == _ev(payload={"summary": "ok"}).payload_digest
