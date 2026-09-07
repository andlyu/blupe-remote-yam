# BluPe Remote YAM

Run Astra on BluPe’s shared robot arms from your computer. No robot required.
Your model API key stays in the local process and is sent only to your selected model provider.

## Download, launch, run

Requires Git and Python 3.10+ on macOS or Linux (Windows users can use WSL).

```bash
git clone https://github.com/andlyu/blupe-remote-yam.git
cd blupe-remote-yam
export OPENAI_API_KEY="xyz"
./run.sh
```

Replace `xyz` with your OpenAI API key. The launcher installs dependencies into
`.venv` and opens the UI at http://127.0.0.1:8787. With that key configured,
OpenAI / Astra (`gpt-6-astra`) is selected automatically; your account must have
access to the selected model. Model calls use your provider account and credits.

Enter a task, such as **place green block on plate**, then click **Join Queue**.
Keep the terminal open. Opening the UI only monitors the station; joining the
queue requests a turn. The station operator must ready the arms before motion.
Use **Leave Queue / Stop** to end your run. **Call Operator** displays our
contact details: text/call [7033443837](tel:+17033443837) or email
[andrew@blupe.io](mailto:andrew@blupe.io). Use **Watch replay**, **Save video**,
and **Save log** to review it afterward.

For future launches, run `./run.sh` from this directory. If the key is absent,
select OpenAI and the terminal requests it privately when joining. A built-in
raise/lower policy is also available without a model API key.

## Camera access from another computer

All three physical cameras now come through the public Session API over HTTPS.
Off-site runners need no route to the robot's private network and no camera SSH
tunnel. The runner trusts the configured Session API origin by default and rejects
camera redirects or URLs on other origins. `--camera-origin` is an explicit override
for legacy or local setups.

The API returns only fresh frames; a disconnected or stale camera produces an
error and prevents image-free inference. The local monitor refreshes JPEG snapshots.
After updating with `git pull`, restart an already-running runner to load these changes.
On a headless Ubuntu/AWS server, use `MUJOCO_GL=egl ./run.sh --no-browser`.
An SSH tunnel to the runner's local web UI is still needed to open that UI remotely.

## What runs where

Your computer runs the UI, model requests, and inverse kinematics. BluPe manages
the queue, robot observations, and exclusive command sessions. The robot gateway
enforces motion limits, operator readiness, stops, and the run deadline.

Keys remain in local process memory; they are not sent to BluPe or saved in logs.
Model conversations and interaction logs are saved under `recordings/` locally.
Physical robot episodes are recorded and published to
[Public-YAM-runs](https://huggingface.co/datasets/andlyu/Public-YAM-runs).

## Runner conversation

Click **Open runner conversation** below **Join Queue** to inspect what was
sent to the model and what it returned in a chat transcript. Runner messages
appear on the right and model replies on the left, with camera images and tool
results inline. Repeated history is shown once. View the full conversation or
filter to one call; expand **Call details** for exact request/response JSON,
including the full history and tool definitions. The view updates every two seconds while open.
Built-in policies have no model conversation; a call without a recorded response
is shown as such. The conversation stays local; **Save log** exports it.

## API documentation

Building your own client or policy? See [API.md](API.md) for endpoints, session
authentication, queue and observation events, trajectory commands, limits,
errors, and examples. The normal `./run.sh` flow handles these for you.

## Development options

`./run.sh --help` lists options. Defaults connect to BluPe’s hosted Session API
and enable its waypoint transport. `--session-api URL` selects another service;
`--camera-origin URL` selects its exact trusted camera origin.
`--no-allow-hardware-control` restricts execution to simulation observations.
`--mock` uses an in-process mock; it is not a physical robot or full camera simulation.
`--no-browser` leaves the browser closed. `--check-provider` checks model access
without connecting to a robot. None of these options is needed for the normal launch.

Run the offline Python suite after installing dependencies:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

## Credits

- **OpenAI** — Astra and the API used for model inference.
- **RoboCurve** — the no-demo policy prompt/tool contract and reference control
  approach, adapted from [inspect-robots](https://github.com/robocurve/inspect-robots).
  The MIT notice is retained in [ROBOCURVE_LICENSE](src/remote_yam/ROBOCURVE_LICENSE).
- **I2RT Robotics** — YAM robot model assets, used in BluPe’s bimanual scene.
  See [I2RT_LICENSE](models/I2RT_LICENSE).
- **MuJoCo**, **NumPy**, and **websocket-client** — simulation, numerical computing,
  and session transport.

BluPe Remote YAM is an independent BluPe project.

### Replay fetching progress

**Watch replay** shows a fetching bar immediately. Local browser recordings
open directly. Archived videos are fetched completely before playback: the bar
shows server preparation first, then received MB and percentage when the response
provides a total size. Closing the replay cancels the request.
