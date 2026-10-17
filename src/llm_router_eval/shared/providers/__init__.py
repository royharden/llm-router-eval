"""Public provider exports, loaded only when requested.

Starting the standard-library-only SDK worker must not first import its parent's
controller, accounting schemas or optional transports. Public objects still come
from their canonical modules; this changes import timing, not their identity.
"""

from importlib import import_module

_EXPORT_MODULES = {
    "FixtureIndex": "captures",
    "ContentValidator": "repair",
    "GenerationRepairPolicy": "repair",
    "ModelSupport": "adapters",
    "Provider": "controller",
    "ProviderConfig": "adapters",
    "ProviderError": "controller",
    "ProjectCredential": "sdk",
    "SDKTransport": "sdk",
    "TransportFailure": "controller",
    "TransportReply": "controller",
    "build_generation_repair_request": "repair",
    "models": "models",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(f".{module_name}", __name__)
    value = module if name == "models" else getattr(module, name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
