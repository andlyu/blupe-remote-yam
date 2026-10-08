"""Portable local ASPIRE station defaults; no private checkout is required."""
import json
import os
from pathlib import Path

PIN = 'f4c8939aab0af9b97690c561bd80e282940f7886'
RUNNER = Path(__file__).resolve().parents[2]
STATION = RUNNER/'aspire'

def station_config(config, *, base=None):
    base = Path(base or Path.cwd()).resolve()
    data = Path.home()/'.local/share/blupe/aspire'
    result = dict(config)
    defaults = dict(aspire=str(data/('source-'+PIN[:7])), python=str(data/'venv/bin/python'),
        harness_module=str(STATION/'agent_harness.py'), skill_directory=str(data/'skills/programs'),
        run_directory=str(data/'runs'), published_skills=True, original_upstream_skills=True,
        segmentation_backend='astra', gripper_geometry='robohouse-linear4310')
    for key, value in defaults.items(): result.setdefault(key, value)
    def path(value):
        raw=Path(value).expanduser()
        return str((base/raw).absolute() if not raw.is_absolute() else raw.absolute())
    for key in ('aspire','python','harness_module','skill_directory','run_directory'):
        result[key]=path(result[key])
    learning=dict(result.get('skill_learning') or {})
    learning.setdefault('enabled', True)
    learning.setdefault('root', str(data/'skills/topics'))
    learning.setdefault('suite', 'robohouse')
    learning.setdefault('review_timing', 'after_run')
    learning['root']=path(learning['root'])
    result['skill_learning']=learning
    executable=dict(result.get('executable_skills') or {})
    executable.setdefault('enabled', True)
    if executable.get('manifest'): executable['manifest']=path(executable['manifest'])
    result['executable_skills']=executable
    return result

def load_station_config(filename):
    filename=Path(filename).expanduser().absolute()
    return station_config(json.loads(filename.read_text()), base=filename.parent)


def recording_environment(python, environ=None):
    """Expose the runtime's bundled ffmpeg to unmodified upstream recorders."""
    env=dict(os.environ if environ is None else environ)
    env['ASPIRE_RUNTIME_PYTHON']=str(python)
    env['PATH']=str(STATION)+os.pathsep+env.get('PATH',os.defpath)
    return env
