"""Explicit web-chart behavior on the station's existing ASPIRE policy.

Only a genuine native path failure before any task dispatch can branch to
ordinary Astra. Queue, Home, dispatch, Stop and completion stay with the runner.
The web attempt never generates/repairs ASPIRE Python or physically retries.
"""
from .providers import PolicyComplete


def web_runtime_config(config):
    return dict(config, execution_environment='web', code_revision_loop=False, automatic_recovery=False)


def can_fallback_before_motion(policy):
    result = policy._outcome or {}
    return (policy._saved_executable_only
            and result.get('status') == 'PLAN_FAILED'
            and result.get('planning_success') is False
            and type(result.get('physical_motion_calls')) is int
            and result['physical_motion_calls'] == 0
            and not result.get('failure_kind')
            and not policy._saved_motion_requests
            and not policy._controller_failure
            and not policy._failure
            and not policy._recovery_attempts
            and not policy.cancelled())


def configure_web_policy(policy, fallback_factory):
    """Keep local iteration defaults independent from the approved web flow."""
    policy.code_revision_loop = False
    policy.automatic_recovery = False
    policy.repair_outcomes = False
    # Disable the native policy's broader legacy fallback. Reuse its linked
    # attempt and ordinary Astra execution only after the explicit gate below.
    policy.recovery_factory = None
    native_build = policy.build_trajectory
    def build_trajectory(prompt, observation, first_step_id):
        try:
            return native_build(prompt, observation, first_step_id)
        except PolicyComplete:
            if policy._recovery is not None or not can_fallback_before_motion(policy):
                raise
            policy.recovery_factory = fallback_factory
            try:
                policy._start_recovery(policy._outcome)
            finally:
                policy.recovery_factory = None
            return policy._build_recovery(prompt, observation, first_step_id)
    policy.build_trajectory = build_trajectory
    return policy
