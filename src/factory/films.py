"""Picture styles (D-111, D-123): Flow clips, or a film made by code from a blueprint drawing or
a Blender 3D model. A film made by code is the item's whole picture, and its narration may only
use the machine's verified facts."""

from __future__ import annotations

import sqlite3
from types import ModuleType

from factory import blender, blueprint
from factory.blueprint.canvas import Fact

STYLES = ("flow", "blueprint", "blender")
CODE_FILM_STYLES = frozenset({"blueprint", "blender"})
FILM_FILES = {"blueprint": "blueprint.mp4", "blender": "blender.mp4"}  # in the asset folder


def module_for(row: sqlite3.Row) -> ModuleType | None:
    """The drawing or 3D model that makes this item's film; None for Flow or a missing one."""
    registry = {"blueprint": blueprint.MODULES, "blender": blender.MODELS}.get(row["visual_style"])
    return registry.get(row["object_name"]) if registry is not None else None


def facts(row: sqlite3.Row) -> list[Fact] | None:
    """The machine's verified facts for a film made by code, None for a Flow item."""
    module = module_for(row)
    return list(module.FACTS.values()) if module is not None else None


def outline(row: sqlite3.Row) -> tuple[str, ...]:
    """What a Blender film shows, scene by scene, so the script follows the film (D-123)."""
    module = module_for(row) if row["visual_style"] == "blender" else None
    return module.OUTLINE if module is not None else ()
