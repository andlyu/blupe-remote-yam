# YAM Cartesian workspace

Before queue admission, the runner selects the saved controller workspace by
robot ID. The prompt, move_to tool description/schema, and local target
validation use the same per-instance bounds. Original RoboCurve and With Haste
retain their audited behavioral text, followed by an active workspace appendix.
The complete effective prompt is hashed in the run record along with the profile.
The nominal 0.76 m shoulder reach is link geometry, not a workspace permission
or a guarantee that IK can reach a target.

The snapshots in `config/controller-workspaces/` were copied byte-for-byte from
`blupe-evals/config/yam_calibration/` on October 8, 2026. Their hashes and guard
model hashes are pinned in `controller_workspace.py`. This is saved configuration,
not live configuration verification; the run provenance explicitly records that.
Published safety/model provenance, when available in a depth report, must match
a recognized profile. YAM-1's two known safety revisions share the same Cartesian
envelope and differ in motion pacing settings; runner pacing is independent.

| Selected station | X, both arms (m) | Left Y (m) | Right Y (m) | Z, both arms (m) |
| --- | --- | --- | --- | --- |
| `yam-1` | [0.046831991, 0.746831991] | [-0.693624194, 0.406375806] | [-0.393624194, 0.706375806] | [-0.032671465, 0.747328535] |
| `robot-ba8413962083809c` (Robo-house) | [0.146831991, 0.746831991] | [-0.493624194, 0.206375806] | [-0.193624194, 0.506375806] | [-0.012671465, 0.547328535] |

The controller computes X as `rest_x - backward <= x <= rest_x + forward`.
Both profiles have rest X 0.246831991 m and forward allowance 0.5 m: maximum
absolute grasp-point X is 0.746831991 m (50 cm beyond configured rest).
Lateral inward/outward allowances swap for the right arm. Minimum Z is the
greater of `rest_z - down` and `table_z + minimum_table_clearance`.

Controller common-frame boxes are converted into each arm base using the guard
model's bases: YAM-1 at +/-0.35 m, Robo-house at +/-0.31 m. YAM-1's camera/IK
common-frame bases at +/-0.25 m are distinct; changing camera spacing must not
translate the per-arm workspace.

Unknown station IDs and direct adapters that have not been bound to a station
keep the legacy XYZ envelope: X [0.15, 0.48], Y [-0.5, 0.5], Z [0.03, 0.4] m.
Other embodiments keep their own geometry and tools.

Cartesian increments remain 13.2 mm X, 20 mm Y, and 14.8 mm Z per 10 Hz waypoint;
rotation, fixed pitch/roll, gripper pacing, joint pacing, waypoint budgets and IK
checks are unchanged. The gateway remains authoritative for every packet,
including joint limits, swept-pose workspace checks, table clearance and collisions.
Refreshing a controller profile requires reviewing its safety/model sources,
updating the snapshot and pinned hashes, and running the workspace tests. This
change does not update a controller, deploy, or issue robot commands.
