"""Launch the existing Codex UI with a portable local ASPIRE station config."""
import argparse
import os
from pathlib import Path
import sys

RUNNER=Path(__file__).resolve().parent
sys.path.insert(0,str(RUNNER/'src'))
from remote_yam.aspire_station import load_station_config, recording_environment


def main():
    parser=argparse.ArgumentParser(description=__doc__,add_help=False)
    parser.add_argument('--config',type=Path,default=Path(os.environ.get('YAM_ASPIRE_CONFIG',Path.home()/'.config/blupe/aspire.json')))
    args,extra=parser.parse_known_args()
    if not args.config.expanduser().is_file():
        raise SystemExit('ASPIRE configuration is missing. Run ./setup-aspire.sh first, or provide --config.')
    config=load_station_config(args.config)
    if not Path(config['python']).is_file() or not (Path(config['aspire'])/'aspire/real/run_script.py').is_file():
        raise SystemExit('ASPIRE native runtime is missing. Run ./setup-aspire.sh or fix the selected config.')
    os.environ.update(recording_environment(config['python']))
    os.environ['YAM_RUNNER_PYTHON']=config['python']
    command=[str(RUNNER.parent/'run-codex.sh'),'--aspire-config',str(args.config.expanduser().absolute()),
        '--robot-id',config['robot_id'],'--session-api',config['origin'],*extra]
    os.execv(command[0],command)


if __name__=='__main__':main()
