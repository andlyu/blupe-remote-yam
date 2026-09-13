from remote_yam.run_errors import public_run_error


def test_common_errors_have_public_descriptions():
    for error, expected in [
        ('HTTPError 401: secret', 'credentials'), ('HTTPError 429: secret', 'quota'),
        ('left camera unavailable /private/file', 'camera frame'),
        ('Policy runtime limit exceeded; station parking at zero', 'time limit'),
        ('ConnectionError: secret', 'lost its connection'),
    ]:
        result = public_run_error({'error': error})
        assert expected in result
        assert 'secret' not in result and '/private' not in result


def test_retry_clears_on_recovery():
    assert 'retrying' in public_run_error({'status':'running', 'server_contact_issue':{'state':'retrying'}})
    assert public_run_error({'status':'running', 'server_contact_issue':None}) is None
    assert public_run_error({'status':'stopped', 'server_contact_issue':{'state':'retrying'}}) is None


def test_specific_joint_diagnostic_is_preserved():
    message='within 2s: left_joint_5 residual_rad=0.23 measured_rad=0.35 target_rad=0.58; allowed_rad=0.1'
    assert 'Left arm joint 5' in public_run_error({'error':message})


def test_timeout_with_error_takes_priority_and_history_retains_it(tmp_path):
    from remote_yam.past_runs import RunNames
    state = {'status':'timed_out','error':'Session is not active'}
    assert public_run_error(state) == 'Run reached time limit'
    names = RunNames(tmp_path/'names.sqlite3')
    names.remember_result('ep_timeout', state)
    names.remember_result('ep_timeout', {'status':'error','error':'Session is not active'})
    import sqlite3
    with sqlite3.connect(names.path) as db:
        assert db.execute('SELECT result,reason FROM run_results').fetchone() == ('Failure','Run reached time limit')


def test_timeout_safety_code_survives_generic_error():
    assert public_run_error({'status':'stopped','error':'Session is not active',
                             'safety_error':{'code':'policy_runtime_timeout'}}) == 'Run reached time limit'


def test_robot_catalog_timeout_wins_over_stale_generic_result():
    from remote_yam.past_runs import merge_run_result, run_result
    row = run_result({'outcome':'session_timeout'})
    merge_run_result(row, {'result':'Failure','result_reason':'Runner or model error','errors':'Runner or model error'})
    assert row['result_reason'] == row['errors'] == 'Run reached time limit'
    assert row['result'] == 'Failure'


def test_dispatch_error_reconciles_authoritative_timeout():
    from unittest.mock import Mock
    from remote_yam.controller import RunnerController
    from remote_yam.session import MockSessionAPI
    controller = RunnerController(MockSessionAPI())
    controller._session_id = 'test-timeout'
    controller._session_api.get_session = Mock(return_value={'status':'timed_out'})
    controller.process_next_event = Mock(side_effect=RuntimeError('Session is not active'))
    controller._run_loop(None)
    state = controller.status()
    assert state['status'] == 'timed_out'
    assert public_run_error(state) == 'Run reached time limit'


def test_station_unsafe_displays_recorded_diagnostic_without_outcome_change(tmp_path):
    from remote_yam.past_runs import RunNames
    error='RuntimeError: Hardware feedback blocked: station_unsafe'
    state={'status':'stopped','error':error}
    assert public_run_error(state)==error
    names=RunNames(tmp_path/'names.sqlite3');names.remember_result('ep_guard',state)
    result=names.results()['ep_guard']
    assert result['errors']==error
    assert result['result']=='Failure' and result['result_reason']==error
    assert 'private' not in public_run_error({'error':error+' private-key'})


def test_existing_replay_generic_label_uses_saved_exact_error():
    from remote_yam.past_runs import merge_run_result
    error = 'RuntimeError: Hardware feedback blocked: station_unsafe'
    row = merge_run_result({}, {'result':'Failure', 'result_reason':'Runner or model error', 'errors':error})
    assert row['result_reason'] == error


def test_feedback_error_text_is_preserved_without_rewording():
    error = 'RuntimeError: Hardware feedback blocked: station_not_explicitly_settled'
    assert public_run_error({'error':error}) == error
