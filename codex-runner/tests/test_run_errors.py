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


def test_planned_path_error_is_visible_in_live_and_saved_results(tmp_path):
    from remote_yam.past_runs import RunNames, merge_run_result
    message = 'Planned path error: 0.168 m > 0.150 m maximum (carry).'
    state = dict(status='stopped', error='RuntimeError: ' + message)
    assert public_run_error(state) == message
    names = RunNames(tmp_path/'names.sqlite3')
    names.remember_result('ep_path', state)
    saved = names.results()['ep_path']
    assert saved['result'] == 'Failure'
    assert saved['errors'] == saved['result_reason'] == message
    assert merge_run_result({}, dict(saved, result_reason='Runner or model error'))['result_reason'] == message


def test_planned_path_diagnostic_does_not_expose_arbitrary_exception_text():
    from remote_yam.past_runs import public_runner_diagnostic
    message = 'Planned path error: 0.168 m > 0.150 m maximum (carry).'
    for error in (message+' private-key', message.replace('carry', 'private-key'),
                  message.replace('0.168', 'nan'), message.replace('0.150', 'inf'),
                  {'token': 'private-key'}, None):
        assert public_runner_diagnostic({'error': error}) is None


def test_fresh_depth_failure_is_visible_and_does_not_echo_provider_details(tmp_path):
    from remote_yam.past_runs import RunNames, public_runner_diagnostic
    expected = 'No fresh RGB-D capture available'
    for error in (expected, 'RuntimeError: '+expected):
        assert public_run_error({'error': error}) == expected
    assert public_runner_diagnostic({'error': 'No fresh RGB-D capture available secret'}) is None
    names=RunNames(tmp_path/'names.sqlite3')
    names.remember_result('ep_depth', dict(status='stopped', error='RuntimeError: '+expected))
    assert names.results()['ep_depth']['result_reason']==expected


def test_camera_failure_is_exact_in_live_and_saved_results(tmp_path):
    from remote_yam.past_runs import RunNames, merge_run_result
    message = 'Camera failure: right camera frame unavailable after 3 attempt(s) (HTTP 503)'
    state = {'status': 'stopped', 'error': 'RuntimeError: ' + message}
    assert public_run_error(state) == message
    names = RunNames(tmp_path / 'camera.sqlite3')
    names.remember_result('ep_camera', state)
    saved = names.results()['ep_camera']
    assert saved['result_reason'] == saved['errors'] == message
    assert merge_run_result({}, dict(saved, result_reason='Runner or model error'))['result_reason'] == message


def test_camera_failure_never_echoes_unrecognized_payloads():
    from remote_yam.past_runs import public_runner_diagnostic
    for cause in ('HTTP 503', 'TimeoutError', 'URLError', 'ValueError'):
        message = f'Camera failure: left camera frame unavailable after 12 attempt(s) ({cause})'
        assert public_run_error({'error': message}) == message
        assert public_runner_diagnostic({'error': message + ' private-key'}) is None
    for cause in ('https://private.example/token', 'private-key', 'HTTP 503 private-key'):
        message = f'Camera failure: left camera frame unavailable after 3 attempt(s) ({cause})'
        assert public_runner_diagnostic({'error': message}) is None
