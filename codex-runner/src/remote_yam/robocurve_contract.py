"""No-demo RoboCurve prompt/tools, adapted only for the YAM gateway contract.

Source: inspect-robots-agent 0.26.0 (MIT); see docs/refs/robocurve/INDEX.md.
No demonstration, recorded observation, or prior assistant response is included.
"""

SYSTEM_PROMPT = """Control 'yam_arms' using current camera images and measured state. Act promptly: choose the next useful, safe motion with brief deliberation. Check images, state, and motion constraints; never guess missing critical information. Return exactly one tool call per turn. Include a 1–2 sentence `note` stating what you observe and why you chose the motion. Follow operator feedback. Unsafe packets stop the session; never rely on clamping. Call done only when the goal is achieved, or give_up when it cannot be achieved. At completion, report useful rig/task lessons in hindsight, or 'none'. Budget: 100 model calls.

Embodiment notes:
Two identical 6-DoF arms, prefixed left_ and right_, each with a parallel-jaw
gripper, controlled by Cartesian end-effector targets. Each arm's targets are
in that arm's own base frame: +x points forward out of the base, +y left, +z
up; how the two bases are mounted relative to each other depends on the rig.
- left_x / right_x, left_y / right_y, left_z / right_z: grasp-point position
  in meters in the arm's base frame (the grasp point sits between the
  fingertips).
- left_yaw / right_yaw: tool rotation in radians about vertical, relative to
  the trial's start orientation; 0 keeps the start orientation and positive
  turns counterclockwise seen from above.
- left_pitch / right_pitch, left_roll / right_roll: tool tilt in radians,
  also relative to the trial's start orientation. Positive pitch tips the
  tool forward (+x at yaw 0), positive roll toward the arm's left (+y at
  yaw 0). An axis whose configured bounds are equal (typically 0) is pinned:
  targets on it must equal that value and it cannot be actuated.
- left_gripper / right_gripper: 0 is fully closed, 1 is fully open (about
  9.5 cm between the jaws).
Proportions: upper arm 0.26 m, forearm 0.25 m, wrist to grasp point 0.25 m
when straight; reach from the shoulder about 0.76 m.
An inverse-kinematics layer converts Cartesian paths into joint waypoints at
10 Hz. Unreachable targets are rejected before motion. Joint pacing can slow
a path. Prefer steps that get you to the goal; don't waste time."""

TOOLS = [{'type': 'function',
  'name': 'move_to',
  'description': 'Move to absolute Cartesian end-effector targets (meters for positions, radians for '
                 'rotations, per the dimension labels). The motion is a straight line interpolated at a '
                 'fixed safe speed and the result reports its step count. Unnamed dimensions hold their '
                 "current value. Coordinates are absolute in the embodiment's declared frame; on multi-arm "
                 'embodiments each arm uses its own base frame and axes may differ between arms depending on '
                 "mounting. Rotation dimensions are absolute targets measured relative to the trial's start "
                 'orientation (0 means the start orientation) and interpolate linearly without wrapping, so '
                 'prefer intermediate values for large rotations. Per-dimension bounds: left_x: [0.15, '
                 '0.48], left_y: [-0.3, 0.3], left_z: [0.03, 0.4], left_yaw: [-3.142, 3.142], left_pitch: '
                 '[0, 0], left_roll: [0, 0], left_gripper: [0, 1], right_x: [0.15, 0.48], right_y: [-0.3, '
                 '0.3], right_z: [0.03, 0.4], right_yaw: [-3.142, 3.142], right_pitch: [0, 0], right_roll: '
                 '[0, 0], right_gripper: [0, 1].',
  'parameters': {'type': 'object',
                 'properties': {'targets': {'type': 'object',
                                            'description': 'Map of dimension name to value. Valid names: '
                                                           'left_x, left_y, left_z, left_yaw, left_pitch, '
                                                           'left_roll, left_gripper, right_x, right_y, '
                                                           'right_z, right_yaw, right_pitch, right_roll, '
                                                           'right_gripper'},
                                'note': {'type': 'string',
                                         'description': 'In 1–2 plain sentences, state what you observe and why you chose this motion.'}},
                 'required': ['targets', 'note']},
  'strict': False},
 {'type': 'function',
  'name': 'done',
  'description': 'Declare the task finished. The session ends holding position. This is model-reported '
                 'completion; the operator confirms success.',
  'parameters': {'type': 'object',
                 'properties': {'summary': {'type': 'string'},
                                'hindsight': {'type': 'string',
                                              'description': "Useful rig/task facts you wish you had known initially (geometry, cameras, grippers, control); otherwise 'none'."}},
                 'required': ['summary', 'hindsight']},
  'strict': False},
 {'type': 'function',
  'name': 'give_up',
  'description': 'Stop trying; the task cannot be completed. The trial ends.',
  'parameters': {'type': 'object',
                 'properties': {'reason': {'type': 'string'},
                                'hindsight': {'type': 'string',
                                              'description': "Useful rig/task facts you wish you had known initially (geometry, cameras, grippers, control); otherwise 'none'."}},
                 'required': ['reason', 'hindsight']},
  'strict': False}]
