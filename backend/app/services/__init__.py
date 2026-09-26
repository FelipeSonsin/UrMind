__all__ = ["CoreService"]


def __getattr__(name: str):
    if name == "CoreService":
        from .core import CoreService

        return CoreService
    raise AttributeError(name)
