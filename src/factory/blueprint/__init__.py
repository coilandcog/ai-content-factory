"""Blueprint films (D-111): one drawing module per machine, looked up by the item's object name."""

from __future__ import annotations

from types import ModuleType

from factory.blueprint import cvt_gearbox, jet_engine
from factory.errors import FactoryError

MODULES: dict[str, ModuleType] = {m.MACHINE: m for m in (jet_engine, cvt_gearbox)}


def missing_message(object_name: str) -> str:
    return (
        f"no blueprint drawing for '{object_name}' yet (drawn: {', '.join(sorted(MODULES))}); "
        "ask Claude to draw one, or set the item to visual_style flow"
    )


def module_for(object_name: str) -> ModuleType:
    try:
        return MODULES[object_name]
    except KeyError:
        raise FactoryError(missing_message(object_name)) from None
