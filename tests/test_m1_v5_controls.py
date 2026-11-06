import pytest
from tests.test_m1_v5_delivery import _service, claim, D, Delivery, final_key, sqlite3


def test_direct_fabricated_gate_is_not_authorization(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    from department_lib.acceptance import evaluate
    snap = svc.board.get('work')
    gate = evaluate(dept=D, task=snap, claim=claim(), artifact_lookup=st.artifact_identity)
    assert gate.status == 'releasable'
    assert svc.delivery.release_final(D,snap,gate).reason == 'acceptance_missing'
    assert st.delivery_count() == 0
    from dataclasses import replace
    assert svc.accept(claim()).status == 'releasable'
    assert svc.delivery.release_final(D,snap,replace(gate,reviewer_profile='foreign')).reason == 'acceptance_stale'
    st.put_verdict(verdict_id='unused',dept_id='dev',task_id='work',run_id=99,
                   reviewer_profile='reviewer',revision=1,artifact_sha256=gate.artifact_sha256,
                   verdict='PASS',note='UNACCEPTED SUMMARY',event_id='unused',request_run_id=5)
    assert svc.release(claim()).action == 'delivered'
    report = st.get_delivery(final_key('dev','work')).body
    assert report['summary'] == 'Accepted report' and report['verdict']['run_id'] == 6


def test_reports_readable_offline_and_status_is_pure(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    assert svc.accept(claim()).status == 'releasable'
    assert svc.release(claim()).action == 'delivered'
    original = svc.status('dev')['tasks'][0]['delivery']['report']
    cli.fail.add('work')
    before = st.connection.total_changes
    assert svc.status('dev')['tasks'][0]['delivery']['report'] == original
    assert svc.release(claim()).action == 'already_delivered'
    assert st.connection.total_changes == before and port.appends == 0


def test_status_held_unknown_legacy_and_dead_letter(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    assert svc.status('dev')['tasks'][0]['delivery']['reason'] == 'acceptance_missing'
    assert svc.accept(claim()).status == 'releasable'
    assert svc.status('dev')['tasks'][0]['delivery']['reason'] == 'not_delivered'
    from copy import deepcopy
    collision = deepcopy(cli.tasks['work']['events'][-1])
    collision['payload'] = {'changed': True}
    cli.tasks['work']['events'].append(collision)
    before = st.connection.total_changes
    assert svc.status('dev')['tasks'][0]['delivery']['reason'] == 'event_quarantined'
    assert st.connection.total_changes == before
    cli.tasks['work']['events'].pop()
    cli.fail.add('work')
    assert svc.status('dev')['tasks'][0]['delivery']['action'] == 'unknown'
    key=final_key('dev','work')
    st.put_delivery(key,'dev','work','final',{'task_id':'work'})
    assert svc.status('dev')['tasks'][0]['delivery']['reason'] == 'prior_effect_unknown'
    st.set_delivery_state(key,'dead_letter','operator decision needed')
    result=svc.status('dev')['tasks'][0]['delivery']
    assert result['action']=='dead_letter' and result['report'] is None


def test_report_limit_and_replacement_insert(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    assert svc.accept(claim()).status == 'releasable'
    original=svc.delivery._body_final
    def huge(*args,**kwargs):
        body=original(*args,**kwargs); body['unresolved_risks']=['x'*65536]; return body
    svc.delivery._body_final=huge
    assert svc.release(claim()).reason == 'report_too_large'
    assert st.delivery_count()==0
    svc.delivery._body_final=original
    assert svc.release(claim()).action=='delivered'
    row=st.get_delivery(final_key('dev','work'))
    with pytest.raises(sqlite3.IntegrityError,match='immutable_delivery'):
        st.connection.execute('INSERT OR REPLACE INTO deliveries SELECT * FROM deliveries WHERE kind=\'final\'')
    with pytest.raises(sqlite3.IntegrityError,match='immutable_delivery'):
        st.connection.execute("INSERT OR REPLACE INTO deliveries SELECT delivery_key,dept_id,task_id,"
                              "'escalation',body_json,state,detail,attempts,created_at,updated_at "
                              "FROM deliveries WHERE kind='final'")
    assert st.get_delivery(row.delivery_key).body==row.body


def test_final_never_calls_port_including_readback(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    class Forbidden:
        requires_upstream_fence=True
        def append(self,*args): raise AssertionError('no final append')
        def find(self,*args): raise AssertionError('no final remote readback')
    svc.delivery=Delivery(st,Forbidden())
    assert svc.accept(claim()).status=='releasable'
    assert svc.release(claim()).action=='delivered'
    assert svc.release(claim()).action=='already_delivered'
    assert svc.delivery.reconcile('dev','work').action=='already_delivered'


def test_direct_release_holds_on_durable_event_conflict_only(tmp_path):
    """P1-1: the direct Delivery boundary enforces the durable-conflict fence.

    Only an ``events.state='conflict'`` row exists: ``ingest`` never writes a
    ``review_quarantines`` row for a payload conflict, so this is exactly the
    case the service folds in via ``authorize()``/``_snapshot_quarantined``.

    Load-bearing: delete the ``events.state='conflict'`` check from
    ``Delivery._local_hold`` and this test fails — release_final returns
    ``('delivered', '')`` and leaves one immutable report row.
    """
    import copy
    st, cli, svc, port = _service(tmp_path)
    gate = svc.accept(claim())
    assert gate.status == 'releasable'
    clean = copy.deepcopy(cli.tasks['work'])
    baseline = svc.ingest('work')                    # baseline observation
    assert baseline.applied == 2 and all(d.status != 'conflict' for d in baseline.decisions)
    tampered = copy.deepcopy(clean)
    tampered['events'][1]['payload'] = {'source_status': 'review', 'tampered': True}
    cli.tasks['work'] = tampered
    assert any(d.status == 'conflict' for d in svc.ingest('work').decisions)
    states = {state: count for state, count in st.connection.execute(
        'SELECT state, COUNT(*) FROM events GROUP BY state')}
    assert states.get('conflict') == 1
    assert st.connection.execute(
        'SELECT COUNT(*) FROM review_quarantines').fetchone()[0] == 0
    cli.tasks['work'] = clean                        # the board looks healthy again
    before = st.connection.total_changes
    result = Delivery(st, port).release_final(D, svc.board.get('work'), gate)
    assert (result.action, result.reason) == ('held', 'event_quarantined')
    assert st.delivery_count() == 0 and port.appends == 0
    assert st.connection.total_changes == before     # a held direct call writes nothing
    # The fence is the durable event, not a quarantine row created on the way.
    assert st.connection.execute(
        'SELECT COUNT(*) FROM review_quarantines').fetchone()[0] == 0
    assert svc.release(claim()).reason == 'event_quarantined'   # service path agrees
    assert st.delivery_count() == 0


def test_status_and_release_agree_on_a_noncanonical_final_key(tmp_path):
    """P3: status reads the durable final row, not only the canonical key."""
    st, cli, svc, port = _service(tmp_path)
    gate = svc.accept(claim())
    body = svc.delivery._body_final(D, svc.board.get('work'), gate)
    st.put_delivery('hand-written-key', 'dev', 'work', 'final', body)
    delivery = svc.status('dev')['tasks'][0]['delivery']
    assert (delivery['action'], delivery['reason']) == ('unknown', 'prior_effect_unknown')
    assert svc.release(claim()).reason == 'prior_effect_unknown'
    assert st.delivery_count() == 1


def test_rejected_authorize_rolls_back_observations(tmp_path):
    st, cli, svc, port = _service(tmp_path)
    assert svc.accept(claim()).status=='releasable'
    before=[tuple(r) for r in st.connection.execute('SELECT * FROM review_generations')]
    def authorize():
        from department_lib.delivery import DeliveryResult
        st.connection.execute('UPDATE review_generations SET request_run_id=99')
        return DeliveryResult('held','review_generation_stale')
    result=svc.delivery.release_final(D,svc.board.get('work'),svc.accept(claim()),authorize=authorize)
    assert result.reason=='review_generation_stale'
    assert [tuple(r) for r in st.connection.execute('SELECT * FROM review_generations')]==before
    assert st.delivery_count()==0
