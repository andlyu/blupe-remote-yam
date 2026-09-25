from remote_yam.run_changes import changes, snapshot
from remote_yam.past_runs import RunNames


def test_human_prompt_and_code_changes():
    before = dict(model='astra', reasoning='low', trajectory_speed=1, task_prompt='Move slowly.', system_prompt='Small motions.', code_revision='old')
    after = {**before, 'trajectory_speed':2, 'task_prompt':'Move quickly.', 'system_prompt':'Prefer efficient motions.', 'code_revision':'new', 'code_history':[dict(revision='new',description='Double YAM trajectory speed'),dict(revision='old',description='Previous version')]}
    text='\n'.join(changes(before,after))
    assert 'Trajectory speed: 1 → 2.' in text
    assert 'Move slowly.' in text and 'Move quickly.' in text
    assert 'Small motions.' in text and 'Prefer efficient motions.' in text
    assert 'Code: Double YAM trajectory speed.' in text
    assert changes(after,after)==['No recorded prompt, model, speed, time-limit or runner-code changes.']


def test_configuration_capture_excludes_credentials():
    class Provider:
        def public_config(self): return dict(model='astra',reasoning_effort='low',api_key='SECRET',endpoint='PRIVATE')
    config=snapshot(Provider(),'move',300)
    assert 'SECRET' not in str(config) and 'PRIVATE' not in str(config)


def test_run_changes_persist_and_compare_by_start_time(tmp_path):
    names=RunNames(tmp_path/'history.db')
    for eid,started,prompt in [('ep_second',20,'New prompt'),('ep_first',10,'Old prompt')]:
        names.remember_metadata(eid,'yam-1',prompt,started,dict(task_prompt=prompt))
        names.remember_result(eid,dict(error='failed',run_metrics=dict(model_s=1,model_calls=1)))
    runs=RunNames(names.path).comparisons('yam-1')
    assert runs[0]['episode_id']=='ep_first'
    assert 'Old prompt' in runs[1]['change_notes'][0] and 'New prompt' in runs[1]['change_notes'][0]
