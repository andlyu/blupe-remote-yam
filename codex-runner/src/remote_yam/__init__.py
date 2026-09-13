"""Separable local Public YAM runner."""

from importlib import import_module

__all__ = [
    "AstraAdapter", "CredentialVault", "HttpSessionAPI", "IKCommand", "IKObservation", "IKSolver",
    "MockSessionAPI", "OpenAIAdapter", "ProviderAdapter", "RunnerController", "SessionAPI",
]


def __getattr__(name):
    # Public transport/diagnostics must not require MuJoCo or load model code.
    modules = {
        "RunnerController": "controller", "CredentialVault": "credentials",
        "IKCommand": "ik", "IKObservation": "ik", "IKSolver": "ik",
        "AstraAdapter": "providers", "OpenAIAdapter": "providers", "ProviderAdapter": "providers",
        "HttpSessionAPI": "session", "MockSessionAPI": "session", "SessionAPI": "session",
    }
    if name not in modules:
        raise AttributeError(name)
    value = getattr(import_module(f".{modules[name]}", __name__), name)
    globals()[name] = value
    return value
