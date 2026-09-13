"""Reconcile action completion with independent station feedback without motor access."""

import math
from collections.abc import Mapping


def timestamp(value):
    return (
        float(value) if isinstance(value, (int, float))
        and not isinstance(value, bool) and math.isfinite(value) else None
    )


def feedback_decision(completion, station, *, now, require_home=False):
    """Return ready/wait/reject and a stable reason. Never infer missing booleans.

    Both observed_at values must originate at the same robot gateway. Their
    ordering is meaningful; local wall time is used only for the existing age gate.
    This function does not establish lease or step identity: the controller does.
    """
    ct = timestamp(completion.get("observed_at"))
    st = timestamp(station.get("observed_at"))
    if ct is None or not -2 <= now - ct <= 10:
        return "reject", "completion_timestamp_invalid_or_stale"
    if completion.get("settled") is not True:
        return "reject", "completion_not_explicitly_settled"
    if require_home and completion.get("homed") is not True:
        return "reject", "completion_not_explicitly_homed"
    if station.get("source") != "hardware":
        return "reject", "station_source_not_hardware"
    safety = station.get("safety")
    if not isinstance(safety, Mapping):
        return "wait", "station_safety_status_missing"
    if safety.get("estop_engaged") is True:
        return "reject", "station_emergency_stop_engaged"
    if safety.get("contact_count") not in (None, 0) or safety.get("active_contacts") not in (None, 0, []):
        return "reject", "station_contact_reported"
    if station.get("mode") == "FAULT":
        return "reject", "station_controller_fault"
    if safety.get("estop_engaged") is not False:
        return "wait", "station_emergency_stop_status_unknown"
    if station.get("mode") == "DISABLED":
        return "wait", "station_disabled"
    if safety.get("ok") is not True:
        reason = safety.get("reason")
        if reason in {"physical_controller_fault", "joint_limit"}:
            return "reject", "station_" + reason
        if reason == "hardware_not_initialized":
            return "wait", "station_hardware_not_initialized"
        return "wait", "station_safety_not_confirmed"
    if st is None or now - st < -2:
        return "reject", "station_timestamp_invalid"
    if now - st > 10 or st < ct:
        return "wait", "station_precedes_completion_or_is_stale"
    if station.get("settled") is not True:
        return "wait", "station_not_explicitly_settled"
    if require_home and station.get("homed") is not True:
        return "wait", "station_not_explicitly_homed"
    return "ready", "consistent_settled_feedback"


def feedback_fields(value):
    """Allowlist diagnostic data; never retain capabilities, URLs, or prompts."""
    result = {}
    for key in ("observed_at", "step_id", "homed", "settled"):
        item = value.get(key)
        result[key] = item if item is None or isinstance(item, (bool, int, float)) else None
    result["source"] = value.get("source") if value.get("source") in {"hardware", "simulation"} else None
    result["mode"] = value.get("mode") if value.get("mode") in {
        "FAULT", "DISABLED", "READY", "STOPPED", "API_ACTIVE", "EXECUTING", "HOMING",
    } else None
    safety = value.get("safety")
    result["safety"] = {
        key: safety.get(key) if isinstance(safety.get(key), bool) else None
        for key in ("ok", "estop_engaged")
    } if isinstance(safety, Mapping) else None
    return result
