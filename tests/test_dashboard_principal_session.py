"""Principal resolution: the dashboard's verified session must authorize reads.

Regression for the real Desktop 0.23.0 report (2026-11-06): the sign-in wall verifies a
bearer token (RFC 8252 native app, ``Authorization: Bearer``) or a cookie session and
stamps the verified ``Session`` on ``request.state.session``
(``hermes_cli/dashboard_auth/middleware.py`` L199 / L258 / L303). ``_principal()`` only
recognised ``native_http_principal`` and ``token_authenticated``, so every dashboard read
answered ``401 {"detail": "authentication required"}`` — while the page itself rendered.

Evidence levels, labelled per test:

* ``stamp-emulated`` — a TestClient middleware sets ``request.state.session`` exactly the
  way the real gate does. Covers every route plus the leak/canary matrix cheaply.
* ``real middleware`` — the actual ``gated_auth_middleware`` runs with a registered fake
  provider over a bearer header (the Desktop transport). Not a real portal login.

The rule under test is fail-closed: only the server-side stamp is trusted. Nothing a
caller can write into the request (header, cookie, query, body) may mint a principal.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from department_lib.dashboard import DashboardService
from department_lib.store import Store
from tests.support import SHA, FakeBoard, dept, event, produce, run, task

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "/api/plugins/department-hub"
CANARY = "SECRET-CANARY-a11f0c9d4e7b"


def _load_plugin_api():
    """Import ``dashboard/plugin_api.py`` exactly like the gateway does (by path)."""
    path = ROOT / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("department_hub_plugin_api_session_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Session:
    """Stand-in for ``hermes_cli.dashboard_auth.base.Session`` (the real dataclass is truthy).

    Carries the same sensitive fields the real object does, so the no-leak assertions are
    meaningful: nothing here may reach a response body or header.
    """

    def __init__(self, **over):
        self.user_id = "user-1"
        self.email = f"{CANARY}@example.test"
        self.display_name = "Operator"
        self.org_id = "org-1"
        self.provider = "stub"
        self.expires_at = 4102444800
        self.access_token = CANARY
        self.refresh_token = CANARY
        self.sub = CANARY
        self.issuer = CANARY
        self.__dict__.update(over)


_UNSET = object()


@pytest.fixture()
def api(monkeypatch, tmp_path):
    """The real plug-in router over a synthetic board + state store (no gateway)."""
    module = _load_plugin_api()
    store = Store(tmp_path / "state.db")
    d = dept()
    store.register_department(d)
    for tid, role in (("t1", "root"), ("t2", "work")):
        store.bind_task(d.dept_id, tid, role=role)
    produce(store, "t1")

    board = FakeBoard({
        "t1": task("t1", "done", events=[
            event("review_requested", 200, 5, reviewer="reviewer-a", implementer="worker-a"),
            event("claimed", 201, 6, source_status="review"),
            event("completed", 300, 6),
        ], runs=[run(5, "worker-a", "review_requested", outcome="review_requested", artifact_sha256=SHA),
                 run(6, "reviewer-a", "completed", outcome="completed",
                     summary="REVIEW: PASS", artifact_sha256=SHA)]),
        "t2": task("t2", "blocked", events=[
            event("blocked", 400, None, reason="waiting for the operator key")]),
    })
    service = DashboardService(store, board)
    monkeypatch.setattr(module, "build_service", lambda settings=None: service)
    return module, store, service


def _app(module, *, auth_required: bool, session=_UNSET, token: bool = False, native=None) -> FastAPI:
    """Bare app + a middleware that emulates ONLY the gateway's server-side stamping.

    This mirrors ``middleware.py`` L199/L258/L303 (session), ``token_auth.py`` L84-85
    (token) and ``native_http.py`` L311 (native). The stamp is chosen by the app, never
    by the request: nothing a caller sends can select it.
    """
    app = FastAPI()
    app.state.auth_required = auth_required

    @app.middleware("http")
    async def stamp(request, call_next):
        if session is not _UNSET:
            request.state.session = session
        if token:
            request.state.token_authenticated = True
        if native is not None:
            request.state.native_http_principal = native
        return await call_next(request)

    app.include_router(module.router, prefix=PREFIX)
    return app


def _session_app(module) -> FastAPI:
    return _app(module, auth_required=True, session=_Session())


# ── the fix: a verified session authorizes reads ────────────────────────────

@pytest.mark.parametrize("path", [
    f"{PREFIX}/health",
    f"{PREFIX}/departments",
    f"{PREFIX}/departments/dev",
    f"{PREFIX}/departments/dev/events",
])
def test_session_stamp_authorizes_every_get_route(api, path):
    """stamp-emulated: every read route accepts the gateway's verified session."""
    module, _store, _svc = api
    response = TestClient(_session_app(module)).get(path)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"


def test_health_caller_is_exactly_the_session_shape(api):
    """stamp-emulated: ``caller`` stays the documented two-key dict, nothing copied in."""
    module, _store, _svc = api
    body = TestClient(_session_app(module)).get(f"{PREFIX}/health").json()
    assert body["caller"] == {"kind": "session", "profile_bound": False}
    assert set(body["caller"]) == {"kind", "profile_bound"}


def test_session_does_not_unblock_mutations(api):
    """stamp-emulated: writes are still refused by design with 409, not passed and not 401."""
    module, store, _svc = api
    client = TestClient(_session_app(module))
    created = client.post(f"{PREFIX}/departments", json={"id": "ops"})
    patched = client.patch(f"{PREFIX}/departments/dev", json={"name": "X"})
    deleted = client.delete(f"{PREFIX}/departments/dev")
    for response in (created, patched, deleted):
        assert response.status_code == 409, response.text
        assert response.json()["blocked"] is True
        assert response.json()["code"] == "unsafe_mutation_blocked"
    assert "ops" not in store.departments()
    # A POST that only validates is still allowed; the session is not a write permit.
    plan = client.post(f"{PREFIX}/departments/plan", json={
        "id": "ops", "name": "Operations", "manager_profile": "manager-b",
        "members": ["worker-b"], "reviewers": ["reviewer-b"]})
    assert plan.status_code == 200, plan.text
    assert plan.json()["valid"] is True


# ── fail-closed: nothing else authorizes ────────────────────────────────────

@pytest.mark.parametrize("falsy", [None, False, 0, "", [], {}, ()])
def test_falsy_session_values_do_not_authorize(api, falsy):
    """stamp-emulated: ``session=None``/False/0/""/empty containers are not a principal."""
    module, _store, _svc = api
    app = _app(module, auth_required=True, session=falsy)
    response = TestClient(app).get(f"{PREFIX}/departments")
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "authentication required"


@pytest.mark.parametrize("headers", [
    {"Authorization": "Bearer SECRET-CANARY-bearer"},
    {"Authorization": "Bearer "},
    {"Cookie": f"hermes_session_at={CANARY}; hermes_session_rt={CANARY}"},
    {"X-Hermes-Session-Token": CANARY},
    {"X-Hermes-Session-Token": CANARY, "Cookie": f"hermes_session_at={CANARY}"},
])
def test_caller_supplied_credentials_never_authorize(api, headers):
    """stamp-emulated: an unverified header/cookie is not a principal (no self-declaration).

    The same header the Desktop uses (``Authorization: Bearer <token>``) only counts once
    the gate has verified it and stamped ``request.state.session``.
    """
    module, _store, _svc = api
    app = _app(module, auth_required=True)  # gate did NOT stamp anything
    response = TestClient(app).get(f"{PREFIX}/departments", headers=headers)
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "authentication required"


def test_query_and_body_cannot_name_a_session(api):
    """stamp-emulated: ``?session=1`` and a JSON body claiming a session authorize nothing."""
    module, _store, _svc = api
    client = TestClient(_app(module, auth_required=True))
    assert client.get(f"{PREFIX}/departments?session=1").status_code == 401
    assert client.get(f"{PREFIX}/departments?session=true&kind=session").status_code == 401
    assert client.post(f"{PREFIX}/departments/plan", json={
        "session": {"kind": "session", "profile_bound": False},
        "id": "ops", "name": "Operations", "manager_profile": "manager-b",
        "members": ["worker-b"], "reviewers": ["reviewer-b"]}).status_code == 401


def test_anonymous_request_is_still_401_on_an_auth_required_gateway(api):
    """stamp-emulated: no stamp at all on an auth-required gateway stays 401."""
    module, _store, _svc = api
    response = TestClient(_app(module, auth_required=True)).get(f"{PREFIX}/health")
    assert response.status_code == 401
    assert response.json() == {"detail": "authentication required"}


# ── no leakage from the session object ──────────────────────────────────────

def test_session_fields_never_leak_into_bodies_or_headers(api):
    """stamp-emulated: a session's provider/email/sub/token never reaches the client."""
    module, _store, _svc = api
    session = _Session()
    client = TestClient(_app(module, auth_required=True, session=session))
    responses = [
        client.get(f"{PREFIX}/health"),
        client.get(f"{PREFIX}/departments"),
        client.get(f"{PREFIX}/departments/dev"),
        client.get(f"{PREFIX}/departments/dev/events"),
        client.post(f"{PREFIX}/departments", json={"id": "ops"}),
    ]
    for response in responses:
        blob = response.text + json.dumps(dict(response.headers))
        assert CANARY not in blob, f"session field leaked on {response.request.url}"
        assert "user-1" not in blob
        assert "org-1" not in blob


# ── unchanged behavior ──────────────────────────────────────────────────────

def test_loopback_still_reports_loopback_without_a_stamp(api):
    """stamp-emulated: auth not required + no stamp keeps the old honest loopback answer."""
    module, _store, _svc = api
    body = TestClient(_app(module, auth_required=False)).get(f"{PREFIX}/health").json()
    assert body["caller"] == {"kind": "loopback", "profile_bound": False}


def test_existing_stamps_keep_their_kind_and_precedence(api):
    """stamp-emulated: native and token stamps are unchanged; native wins over session."""
    module, _store, _svc = api
    native = TestClient(_app(module, auth_required=True, session=_Session(),
                             native={"profile_id": "default"})).get(f"{PREFIX}/health")
    assert native.status_code == 200
    assert native.json()["caller"] == {"kind": "native", "profile_bound": True}
    token = TestClient(_app(module, auth_required=True, session=_Session(), token=True)
                       ).get(f"{PREFIX}/health")
    assert token.status_code == 200
    assert token.json()["caller"] == {"kind": "token", "profile_bound": True}


def test_auth_optional_gateway_reports_a_stamp_it_did_verify(api):
    """stamp-emulated: with auth off, a real verified stamp reports ``session``, not a lie."""
    module, _store, _svc = api
    body = TestClient(_app(module, auth_required=False, session=_Session())
                      ).get(f"{PREFIX}/health").json()
    assert body["caller"] == {"kind": "session", "profile_bound": False}


# ── real middleware + fake provider (the Desktop bearer transport) ──────────
# This exercises hermes_cli.dashboard_auth.middleware.gated_auth_middleware itself:
# it verifies the bearer through the provider registry and stamps request.state.session.
# It is NOT a real portal login and NOT a real Desktop request.

@pytest.fixture()
def gate(api):
    hermes_cli = pytest.importorskip("hermes_cli")
    auth = pytest.importorskip("hermes_cli.dashboard_auth")
    middleware = pytest.importorskip("hermes_cli.dashboard_auth.middleware")
    base = pytest.importorskip("hermes_cli.dashboard_auth.base")

    class FakeProvider(base.DashboardAuthProvider):
        name = "fake-session"
        display_name = "Fake session provider (test only)"

        def start_login(self, *, redirect_uri):  # pragma: no cover - bearer path only
            raise NotImplementedError

        def complete_login(self, *, code, state, code_verifier, redirect_uri):  # pragma: no cover
            raise NotImplementedError

        def verify_session(self, *, access_token):
            if access_token != GOOD_TOKEN:
                return None
            return base.Session(
                user_id="user-1", email=f"{CANARY}@example.test", display_name="Operator",
                org_id="org-1", provider=self.name, expires_at=4102444800,
                access_token=access_token, refresh_token="")

        def refresh_session(self, *, refresh_token):  # pragma: no cover
            raise base.RefreshExpiredError("no refresh in this fake")

        def revoke_session(self, *, refresh_token):  # pragma: no cover
            return None

    GOOD_TOKEN = "good-bearer-token"
    auth.clear_providers()
    auth.register_provider(FakeProvider())
    module, _store, _svc = api
    app = FastAPI()
    app.state.auth_required = True
    # A private gateway listener: the real gate decides loopback/Webapp policy from these.
    app.state.bound_host = "gateway.example.test"
    app.state.bound_port = 9119

    @app.middleware("http")
    async def gate_middleware(request, call_next):
        return await middleware.gated_auth_middleware(request, call_next)

    app.include_router(module.router, prefix=PREFIX)
    client = TestClient(app, base_url="https://gateway.example.test")
    try:
        yield client, GOOD_TOKEN
    finally:
        auth.clear_providers()


def test_real_middleware_bearer_session_authorizes(api, gate):
    """real middleware: valid bearer -> gate stamps Session -> 200 + caller.kind=session."""
    _module, _store, _svc = api
    client, token = gate
    response = client.get(f"{PREFIX}/health", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    assert response.json()["caller"] == {"kind": "session", "profile_bound": False}
    assert CANARY not in response.text


def test_real_middleware_rejects_an_invalid_bearer_before_the_plugin(api, gate):
    """real middleware: a bad bearer is refused by the GATE (its envelope), never the plugin."""
    _module, _store, _svc = api
    client, _token = gate
    response = client.get(f"{PREFIX}/departments",
                          headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401, response.text
    body = response.json()
    assert body["detail"] == "Unauthorized"
    assert body["reason"] == "invalid_or_expired_session"
    # The gate's own envelope for an expired/invalid session (see _unauth_response).
    assert body["error"] == "session_expired"
    assert "authentication required" not in response.text


def test_real_middleware_refuses_a_request_without_any_credential(api, gate):
    """real middleware: no bearer -> the gate's 401 (reason=no_cookie), not a plugin 401."""
    _module, _store, _svc = api
    client, _token = gate
    response = client.get(f"{PREFIX}/departments")
    assert response.status_code == 401, response.text
    body = response.json()
    assert body["detail"] == "Unauthorized"
    assert body.get("reason") == "no_cookie"


def test_real_middleware_bearer_leaks_no_session_field(api, gate):
    """real middleware: even a verified session's email/token never reaches the response."""
    _module, _store, _svc = api
    client, token = gate
    for path in (f"{PREFIX}/health", f"{PREFIX}/departments", f"{PREFIX}/departments/dev"):
        response = client.get(path, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        blob = response.text + json.dumps(dict(response.headers))
        assert CANARY not in blob
