"""v5 plugin-owned delivery: identical bytes run RED then GREEN."""
import json
import os
import signal
import sqlite3
import threading
import multiprocessing
from dataclasses import replace

import pytest
from department_lib.delivery import Delivery, final_key
from department_lib.orchestrator import DepartmentService
from department_lib.board import Board
from department_lib.store import Store
from tests.test_m1_v4_regressions import _service, _add_authorized_rejection
from tests.test_m1_v2_regressions import claim, D


def test_real_submit_pass_accept_deliver_status(tmp_path):
    st, cli, svc, port = _service(tmp_path, produce_sha=None)
    artifact = tmp_path / 'report.md'; artifact.write_text('Final report: verified result ✓')
    submitted = svc.submit_artifact('work', str(artifact), caller_profile='worker')
    sha = submitted['artifact']['sha256']
    for run in cli.tasks['work']['runs']:
        run['metadata']['artifact_sha256'] = sha
    c = replace(claim(), artifact_sha256=sha)
    port.requires_upstream_fence = True
    assert svc.accept(c).status == 'releasable'
    first = svc.release(c)
    assert first.action == 'delivered', first
    assert svc.release(c).action == 'already_delivered'
    assert st.delivery_count() == 1 and port.appends == 0
    status = svc.status('dev')
    delivered = next(t['delivery'] for t in status['tasks'] if t['task_id'] == 'work')
    assert delivered['action'] == 'delivered'
    assert delivered['identity']['artifact_sha256'] == sha
    assert delivered['report']['verdict']['run_id'] == c.verdict_run_id
    assert delivered['report']['summary'] and delivered['report']['delivered_at']
    assert len(json.dumps(delivered['report'], ensure_ascii=False).encode()) <= 65536


@pytest.mark.parametrize('different', [False, True])
def test_two_connections_race(tmp_path, different):
    st, cli, svc, port = _service(tmp_path)
    assert svc.accept(claim()).status == 'releasable'
    other = Store(st._path)
    barrier = threading.Barrier(2); results = []; errors = []
    gate = svc.accept(claim()); snap = svc.board.get('work')
    # The valid identity owns the writer transaction while a different caller
    # enters. A different identity before any report exists is acceptance_stale,
    # not a conflict; after the winning commit it must expose the real identity.
    gates = [gate, replace(gate, verdict_run_id=99) if different else gate]
    writer_entered = threading.Event(); loser_started = threading.Event()
    def go(store, g):
        try:
            barrier.wait(5)
            if different and store is other:
                assert writer_entered.wait(5)
                loser_started.set()
            def authorize():
                if different and store is st:
                    writer_entered.set(); assert loser_started.wait(5)
                return None
            results.append(Delivery(store, port).release_final(D, snap, g, authorize=authorize))
        except BaseException as exc: errors.append(exc)
    threads = [threading.Thread(target=go, args=(s,g)) for s,g in zip((st,other), gates)]
    for t in threads: t.start()
    for t in threads: t.join(10)
    assert not errors and all(not t.is_alive() for t in threads), errors
    assert st.delivery_count() == 1 and port.appends == 0
    assert sorted(r.action for r in results) == (['delivered','held'] if different else ['already_delivered','delivered'])
    if different:
        conflict = next(r for r in results if r.action == 'held')
        assert conflict.reason == 'delivery_identity_conflict'
        body = st.get_delivery(final_key('dev','work')).body
        assert conflict.delivered_identity['verdict_run_id'] == body['verdict']['run_id']
    other.close()


@pytest.mark.parametrize('change', ['rejection','generation','inactive'])
def test_fence_after_advisory_acceptance_check(tmp_path, change):
    st, cli, svc, port = _service(tmp_path)
    assert svc.accept(claim()).status == 'releasable'
    entered = threading.Event(); resume = threading.Event(); results=[]; errors=[]
    class Paused(Delivery):
        def release_final(self, *args, **kw):
            assert self.store.active_acceptance('dev','work') is not None
            entered.set(); assert resume.wait(10)
            return super().release_final(*args, **kw)
    svc.delivery = Paused(st, port)
    def go():
        try: results.append(svc.release(claim()))
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=go); thread.start(); assert entered.wait(10)
    other = Store(st._path)
    try:
        if change == 'rejection':
            old = cli.tasks['work'].copy()
            import copy
            old = copy.deepcopy(cli.tasks['work'])
            _add_authorized_rejection(cli)
            DepartmentService(other, Board(cli), Delivery(other,port)).ingest('work')
            cli.tasks['work'] = old  # stale PASS must not resurrect it
        elif change == 'generation':
            with other.transaction():
                other.connection.execute('UPDATE review_generations SET request_run_id=99')
        else: other.register_department(replace(D,active=False))
    finally:
        other.close(); resume.set(); thread.join(10)
    assert not errors and not thread.is_alive(), errors
    assert results[0].action == 'held'
    assert st.delivery_count() == 0 and port.appends == 0


@pytest.mark.parametrize('phase', ['before','after'])
def test_sigkill_commit_boundary(tmp_path, phase):
    st, cli, svc, port = _service(tmp_path)
    assert svc.accept(claim()).status == 'releasable'
    path = str(st._path); st.close()
    def child():
        fresh = Store(path)
        service = DepartmentService(fresh, Board(cli), Delivery(fresh,port))
        if phase == 'before':
            def trace(sql):
                if sql == 'COMMIT': os.kill(os.getpid(),signal.SIGKILL)
            fresh.connection.set_trace_callback(trace)
        assert service.release(claim()).action == 'delivered'
        os.kill(os.getpid(), signal.SIGKILL)
    ctx = multiprocessing.get_context('fork')
    p = ctx.Process(target=child); p.start(); p.join(10)
    assert p.exitcode == -signal.SIGKILL, p.exitcode
    reopened = Store(path)
    service = DepartmentService(reopened, Board(cli), Delivery(reopened,port))
    assert reopened.delivery_count() == (0 if phase == 'before' else 1)
    if phase == 'after':
        assert service.status('dev')['tasks'][0]['delivery']['action'] == 'delivered'
    assert service.release(claim()).action == ('delivered' if phase == 'before' else 'already_delivered')
    assert reopened.delivery_count() == 1


def test_new_generation_conflicts_and_sql_immutable(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    assert svc.accept(claim()).status == 'releasable'
    assert svc.release(claim()).action == 'delivered'
    row = st.get_delivery(final_key('dev','work')); original = row.body
    gate = replace(svc.accept(claim()), revision=2, verdict_run_id=99)
    result = svc.delivery.release_final(D, svc.board.get('work'), gate)
    assert result.action == 'held' and result.reason == 'delivery_identity_conflict'
    assert result.delivered_identity['revision'] == 1
    assert st.get_delivery(row.delivery_key).body == original and st.delivery_count() == 1
    for sql in ('UPDATE deliveries SET body_json=\'{}\' WHERE kind=\'final\'',
                'DELETE FROM deliveries WHERE kind=\'final\''):
        with pytest.raises(sqlite3.IntegrityError): st.connection.execute(sql)


def test_held_does_not_write_any_local_state(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    before = st.connection.total_changes
    assert svc.release(claim()).reason == 'acceptance_missing'
    assert st.connection.total_changes == before
    assert st.delivery_count() == 0
