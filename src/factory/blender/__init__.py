"""Blender films (D-121, D-123): one 3D model per machine, looked up by the item's object name.

Each model has a laptop part here (verified facts, the film's scenes in order) and a Blender part
in `job_files/` that only runs inside Blender on the render PC. `jobs.py` packs a render job.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from factory.errors import FactoryError


@dataclass(frozen=True)
class Template:
    """One view a machine model can show in a long video (D-129): which of the model's parts are
    on screen, and what the narration over it explains. Blender draws each template (2.30)."""

    shows: tuple[str, ...]  # keys of the model's PARTS; "*" = every part
    explains: str


from factory.blender import alternator, starter_motor  # noqa: E402  (they use Template)

MODELS: dict[str, ModuleType] = {m.MACHINE: m for m in (alternator, starter_motor)}


def missing_message(object_name: str) -> str:
    return (
        f"no Blender model for '{object_name}' yet (built: {', '.join(sorted(MODELS))}); "
        "a model is built in its own session (ROADMAP 2.35), or set the item to another style"
    )


def film_message(object_name: str, format_id: str) -> str | None:
    """Why this machine can't have a Blender film in this format yet, or None when it can: a
    model may be built for long videos first and get its 6-scene Short film later (D-131)."""
    model = MODELS.get(object_name)
    if model is None:
        return missing_message(object_name)
    if format_id != "episode" and not model.SHORTS:
        return (f"the Blender model of '{object_name}' has no Short film yet (long videos only); "
                "make it a long video (--format episode) or use another style")  # fmt: skip
    return None


def model_for(object_name: str) -> ModuleType:
    try:
        return MODELS[object_name]
    except KeyError:
        raise FactoryError(missing_message(object_name)) from None
