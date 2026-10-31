"""Script pre-check at Gate 1 (ROADMAP 2.22, D-118): before the owner approves, the final
narration (with the owner's Engineer's Note) is checked once more.

The script stage's rules only run while Gemini writes the draft, so a note typed at Gate 1 is
never checked, and a wrong claim with no number in it passes (#18/#19 v1: wrong turbine, wrong
temperature). Code checks a blueprint item's numbers against the drawing's verified facts; one
Gemini call checks every claim, the blocklist and (in a series) parts told too early.

Advice only: no status change. Results are kept per narration fingerprint in
script_precheck.json, so showing the same text again costs no Gemini call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from factory.blueprint.canvas import NUMBER
from factory.context import AppContext
from factory.errors import FactoryError
from factory.integrations.gemini import GeminiClient
from factory.prompts import render_prompt
from factory.stages import script as script_stage
from factory.stages import series as series_stage
from factory.stages.voice import asset_dir

log = logging.getLogger("factory.precheck")

PRECHECK_FILE = "script_precheck.json"


class PrecheckError(FactoryError):
    """Approving with open pre-check problems needs an explicit "approve anyway"."""

    def __init__(self, content_id: int, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(
            f"#{content_id} has {len(problems)} pre-check problem(s): " + "; ".join(problems)
        )


class ClaimCheck(BaseModel):
    unsupported_claims: list[str] = Field(
        description="each claim that is wrong or not supported, quoted, with why in a few words; "
        "[] if none"
    )
    blocklist_hits: list[str] = Field(
        description="each part of the narration that touches a blocklist item, and which; "
        "[] if none"
    )
    told_too_early: list[str] = Field(
        description="in a series: each part of the narration (not the last sentence) that explains "
        "a later part's topic; [] if none or not a series"
    )


@dataclass
class Precheck:
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # e.g. "claim check skipped: ..."

    @property
    def ok(self) -> bool:
        return not self.problems

    def report(self) -> str:
        if self.ok and not self.notes:
            return "PRE-CHECK: ok"
        lines = ["PRE-CHECK: ok" if self.ok else f"PRE-CHECK: {len(self.problems)} problem(s)"]
        lines += [f"  ! {p}" for p in self.problems]
        lines += [f"  ({n})" for n in self.notes]
        return "\n".join(lines)


def check(
    ctx: AppContext,
    content_id: int,
    narration: str | None = None,
    *,
    llm: GeminiClient | None = None,
) -> Precheck:
    """Check `narration` (default: the current script's) for item `content_id`."""
    row = ctx.conn.execute("SELECT * FROM contents WHERE id = ?", (content_id,)).fetchone()
    if row is None:
        raise FactoryError(f"content #{content_id} does not exist")
    script = ctx.conn.execute(
        "SELECT * FROM scripts WHERE content_id = ? AND is_current = 1", (content_id,)
    ).fetchone()
    if script is None:
        raise FactoryError(f"content #{content_id} has no script")
    narration = " ".join((narration or script["body_text"]).split())
    facts = script_stage.blueprint_facts(row)
    fact_texts = [f.text for f in facts] if facts is not None else None
    result = Precheck(problems=number_problems(narration, fact_texts))
    claims = _claims(ctx, row, script, narration, fact_texts, llm)
    if isinstance(claims, str):
        result.notes.append(claims)
    else:
        result.problems += [f"claim: {c}" for c in claims.unsupported_claims]
        result.problems += [f"blocklist: {c}" for c in claims.blocklist_hits]
        result.problems += [f"told too early: {c}" for c in claims.told_too_early]
    return result


def number_problems(narration: str, facts: list[str] | None) -> list[str]:
    """A blueprint narration may only say the drawing's verified numbers, as digits (D-111/D-112).
    A Flow item has no verified facts: its numbers are left to the claim check."""
    if facts is None:
        return []
    allowed = " ".join(facts)
    problems = [
        f"number {n} is not in the verified facts"
        for n in dict.fromkeys(m.rstrip(".,") for m in NUMBER.findall(narration))
        if n and n not in allowed
    ]
    spelled = dict.fromkeys(w.lower() for w in script_stage.NUMBER_WORDS.findall(narration))
    if spelled:
        problems.append(f"numbers written as words can't be checked: {', '.join(spelled)}")
    return problems


def claim_client(ctx: AppContext) -> GeminiClient:
    """The Gemini client for the claim check (unit tests replace it, see tests/conftest.py)."""
    return script_stage.gemini_client(ctx)


def _claims(
    ctx: AppContext,
    row: sqlite3.Row,
    script: sqlite3.Row,
    narration: str,
    facts: list[str] | None,
    llm: GeminiClient | None,
) -> ClaimCheck | str:
    """The Gemini claim check, from the cache when this exact text was checked before; a reason
    string when it can't run (budget used up, no key, Gemini failing), so code checks still show."""
    sources = ctx.conn.execute(
        "SELECT url, publisher, claim FROM sources WHERE content_id = ? ORDER BY id", (row["id"],)
    ).fetchall()
    part = series_stage.context(ctx.conn, row["id"])
    prompts_root = ctx.root / ctx.settings.paths.prompts_dir
    system = render_prompt("script_precheck_system", ctx.channel, prompts_root=prompts_root)
    user = render_prompt(
        "script_precheck",
        ctx.channel,
        prompts_root=prompts_root,
        object_name=row["object_name"],
        narration=narration,
        last_sentence=script["loop_line"] or "",
        facts=facts,
        sources=[dict(s) for s in sources],
        part=part,
    )
    key = hashlib.sha256(f"{system.text}\n{user.text}".encode()).hexdigest()
    path = asset_dir(ctx, row["id"]) / PRECHECK_FILE
    cache = json.loads(path.read_text()) if path.is_file() else {}
    if key in cache:
        return ClaimCheck.model_validate(cache[key]["result"])
    try:
        client = llm or claim_client(ctx)
        claims = client.generate_json(system=system.text, prompt=user.text, schema=ClaimCheck)
    except FactoryError as exc:  # budget, missing key, Gemini down: never blocks the review
        log.warning("#%s claim check skipped: %s", row["id"], exc,
                    extra={"content_id": row["id"], "event": "precheck_skipped"})  # fmt: skip
        return f"claim check skipped: {exc}"
    cache[key] = {
        "checked_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": client.model,
        "prompt_version": f"{system.version}+{user.version}",
        "result": claims.model_dump(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2) + "\n")
    return claims
