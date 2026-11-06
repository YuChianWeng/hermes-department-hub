"""Routing regressions: subscription evidence is NOT runtime wake evidence."""
from department_lib.kanban_cli import CliKanban, CommandResult
from department_lib.models import Department
from department_lib.routing import Router, RoutingIssue
from tests.test_board import FakeRunner, ok


def _dept():
    return Department.from_mapping({'id':'dev','name':'Development',
        'manager':{'profile':'dept-manager','platform':'api_server','chat_id':'api-dev-mgr'},
        'members':['worker-a'],'reviewers':['reviewer-a']})


def _row(chat='api-dev-mgr', mode='wake', profile='dept-manager'):
    return {'task_id':'t_w1','platform':'api_server','chat_id':chat,
            'notifier_profile':profile,'delivery_mode':mode, 'thread_id':'',
            'user_id':None, 'user_id_alt':None, 'chat_type':'dm',
            'delivery_metadata':{}, 'created_at':1, 'last_event_id':1, 'last_ping_event_id':0}


def _router(*results):
    return Router(CliKanban(runner=FakeRunner(list(results))))


def ensure(router, repair=False):
    return router.ensure(_dept(), ['t_w1'], repair=repair, task_departments={'t_w1':'dev'})


def codes(report):
    return {i.code for i in report.issues}


def test_ensure_subscribes_missing_manager_wake_and_verifies_by_readback():
    router = _router(ok([]), CommandResult(0,'Subscribed',''), ok([_row()]))
    report = ensure(router)
    assert not report.ok and report.ensured == ('t_w1',)
    assert codes(report) == {'session_owner_unverified'}
    calls = router.cli._runner.calls
    subscribe = next(c for c in calls if 'notify-subscribe' in c)
    assert subscribe[subscribe.index('--notifier-profile')+1] == 'dept-manager'
    assert sum('notify-list' in c for c in calls) == 2


def test_existing_correct_subscription_is_left_alone():
    router = _router(ok([_row()]))
    report = ensure(router)
    assert not report.ok and codes(report) == {'session_owner_unverified'}
    assert report.ensured == ()
    assert not any('notify-subscribe' in c for c in router.cli._runner.calls)


def test_existing_subscription_with_wrong_delivery_mode_is_reported_not_silently_fixed():
    router = _router(ok([_row(mode='notify')]))
    report = ensure(router)
    assert not report.ok and report.ensured == ()
    assert codes(report) == {'session_owner_unverified', 'mode_mismatch'}
    assert not any('notify-unsubscribe' in c for c in router.cli._runner.calls)
    assert not any('notify-subscribe' in c for c in router.cli._runner.calls)


def test_wrong_delivery_mode_is_repaired_only_when_explicitly_requested():
    router = _router(ok([_row(mode='notify')]), CommandResult(0,'',''),
                     CommandResult(0,'',''), ok([_row()]))
    report = ensure(router, repair=True)
    assert not report.ok and report.ensured == ('t_w1',)
    assert codes(report) == {'session_owner_unverified'}
    assert any('notify-unsubscribe' in c for c in router.cli._runner.calls)


def test_foreign_subscription_is_surfaced_and_never_silently_removed():
    router = _router(ok([_row(), _row(chat='api-default-user', profile='default')]))
    report = ensure(router)
    assert not report.ok and report.ensured == ()
    assert codes(report) == {'session_owner_unverified', 'foreign_subscription'}
    issue = next(i for i in report.issues if i.code == 'foreign_subscription')
    assert isinstance(issue, RoutingIssue) and issue.task_id == 't_w1'
    assert 'api-default-user' in issue.detail
    assert not any('notify-unsubscribe' in c for c in router.cli._runner.calls)


def test_subscribe_without_visible_readback_is_failed_closed():
    report = ensure(_router(ok([]), CommandResult(0,'Subscribed',''), ok([])))
    assert not report.ok and report.ensured == ()
    assert codes(report) == {'session_owner_unverified', 'subscribe_unverified'}


def test_read_error_is_reported_as_unknown_not_ok():
    report = ensure(_router(CommandResult(2,'','board locked')))
    assert not report.ok and codes(report) == {'board_unknown'}
    assert report.ensured == () and 'board locked' in report.issues[0].detail


def test_verify_makes_no_writes():
    router = _router(ok([_row()]))
    report = router.verify(_dept(), ['t_w1'])
    assert not report.ok and codes(report) == {'session_owner_unverified'}
    assert report.ensured == () and len(router.cli._runner.calls) == 1
    assert all('notify-list' in c for c in router.cli._runner.calls)


def test_cross_department_task_is_refused():
    router = _router()
    report = router.ensure(_dept(), ['t_ops_1'], task_departments={'t_ops_1':'ops'})
    assert not report.ok and codes(report) == {'cross_department'}
    assert router.cli._runner.calls == []


def test_ensure_is_idempotent_across_two_passes():
    router = _router(ok([]), CommandResult(0,'',''), ok([_row()]), ok([_row()]))
    first, second = ensure(router), ensure(router)
    assert first.ensured == ('t_w1',) and second.ensured == ()
    assert not second.ok and codes(second) == {'session_owner_unverified'}
    assert sum('notify-subscribe' in c for c in router.cli._runner.calls) == 1
    assert not any('notify-unsubscribe' in c for c in router.cli._runner.calls)


def test_unknown_membership_never_subscribes():
    router = _router()
    report = router.ensure(_dept(), ['t_w1'])
    assert codes(report) == {'ownership_unknown'} and not router.cli._runner.calls
