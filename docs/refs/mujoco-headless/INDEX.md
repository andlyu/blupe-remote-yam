# MuJoCo 3.12.0 headless rendering

Source: installed MuJoCo 3.12.0 (Apache-2.0), inspected 2026-09-23.
Upstream: https://github.com/google-deepmind/mujoco/tree/3.12.0/python/mujoco

The copied gl_context.py selects OSMesa only on Linux when MUJOCO_GL=osmesa.
The copied osmesa.py describes and implements software OpenGL, sets
PYOPENGL_PLATFORM=osmesa, and creates its own offscreen buffer/context.
It does not require a desktop CGL connection, X display or GPU. CI installs
Ubuntu libosmesa6 and runs the full rendering test rather than skipping it.
The macOS hosted VM failed CGL initialization with invalid pixel format;
local macOS desktop rendering passed. This selects a test renderer only,
not a fallback for model inference or a production camera pipeline.
