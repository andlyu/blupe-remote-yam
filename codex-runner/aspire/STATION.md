# Shared Robo-house ASPIRE runtime

Use the clean pinned NVIDIA ASPIRE checkout and unmodified aspire/real/run_script.py. This directory supplies the BluPe API station bridge and program contract. Read CODE-GENERATION.md for tools, frames, gripper assumptions and execution ownership. The current prompt defines the task; no saved experiment authorizes a new physical run.

Perception defaults to Astra through the existing Codex subscription transport. Optional RunPod SAM3 needs the operator's own endpoint and credentials. No silent perception fallback occurs. The hardware is accessed through the Session API, never local motor or camera drivers.
