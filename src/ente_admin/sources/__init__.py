"""Data sources. Each turns one Ente instance into a Snapshot (see model.py).

A source is any object with `name`, `kind` and `snapshot() -> Snapshot`. New sources
register themselves in SOURCES; `build()` picks one per configured instance.
"""
from __future__ import annotations

from collections.abc import Callable

from ..config import Instance
from .model import Snapshot

SOURCES: dict[str, Callable[[Instance], object]] = {}


def register(kind: str):
    def deco(factory):
        SOURCES[kind] = factory
        return factory
    return deco


def build(instance: Instance):
    from . import demo, museum, postgres  # noqa: F401  (registers the built-in sources)
    try:
        factory = SOURCES[instance.source]
    except KeyError:
        raise ValueError(f"unknown source {instance.source!r}") from None
    return factory(instance)


__all__ = ["SOURCES", "Snapshot", "build", "register"]
