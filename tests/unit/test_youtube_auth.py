from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from httplib2 import Response

from factory.budgets import usage_today
from factory.config import EnvSettings, load_channel, load_settings
from factory.context import AppContext
from factory.db import connect, migrate, upsert_channel
from factory.errors import BudgetExceededError, ConfigError, FactoryError
from factory.integrations import youtube, youtube_auth
from factory.stages import publish


def creds(expired: bool = False, scopes: tuple[str, ...] = youtube_auth.SCOPES) -> Credentials:
    return Credentials(
        "access", refresh_token="refresh", client_id="id.apps.googleusercontent.com",
        client_secret="client-secret", token_uri="https://oauth2.googleapis.com/token",
        scopes=list(scopes),
        expiry=(datetime.now(UTC) - timedelta(hours=1) if expired
                else datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None),
    )  # fmt: skip


class FakeRequest:
    def __init__(self, answer: Any) -> None:
        self.answer = answer

    def execute(self) -> Any:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def next_chunk(self) -> Any:
        return None, self.execute()


class FakeYouTube:
    """Stands in for googleapiclient's YouTube client; records what was sent."""

    def __init__(self, handle: str = "@CoilAndCog", upload_answer: Any = None) -> None:
        self.handle, self.inserted = handle, []
        self.upload_answer = upload_answer or {"id": "abcdefghijk"}

    def channels(self) -> FakeYouTube:
        return self

    def videos(self) -> FakeYouTube:
        return self

    def list(self, **kwargs: Any) -> FakeRequest:
        assert kwargs == {"part": "snippet", "mine": True}
        item = {"id": "UC123", "snippet": {"title": "Coil & Cog", "customUrl": self.handle}}
        return FakeRequest({"items": [item]})

    def insert(self, **kwargs: Any) -> FakeRequest:
        self.inserted.append(kwargs)
        return FakeRequest(self.upload_answer)


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    channel = load_channel("coil-and-cog")
    upsert_channel(conn, channel)
    (tmp_path / "secrets" / "coil-and-cog").mkdir(parents=True)
    sting = tmp_path / "assets_static" / publish.TEST_VIDEO
    sting.parent.mkdir(parents=True)
    sting.write_bytes(b"fake mp4")
    yield AppContext(load_settings(), EnvSettings(dry_run=False, factory_channel="coil-and-cog"),
                     channel, conn, tmp_path)  # fmt: skip
    conn.close()


def token_file(ctx: AppContext) -> Path:
    return youtube_auth.token_path(ctx.root / "secrets", "coil-and-cog")


def builder_for(api: FakeYouTube) -> Any:
    def build(name: str, version: str, **kwargs: Any) -> FakeYouTube:
        assert (name, version, kwargs["cache_discovery"]) == ("youtube", "v3", False)
        return api

    return build


def test_sign_in_saves_an_owner_only_token_for_the_channel(ctx: AppContext) -> None:
    asked: list[Path] = []

    channel = publish.connect_youtube(
        ctx, sign_in=lambda p: asked.append(p) or creds(), builder=builder_for(FakeYouTube())
    )

    assert channel.handle == "@coilandcog"  # YouTube's capitalisation doesn't matter
    assert asked == [ctx.root / "secrets" / "coil-and-cog" / "google_client_secret.json"]
    saved = token_file(ctx)
    assert json.loads(saved.read_text())["refresh_token"] == "refresh"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert usage_today(ctx.conn, ctx.channel)["youtube_data"][0] == 1


def test_another_channel_is_refused_and_its_token_never_saved(ctx: AppContext) -> None:
    other = builder_for(FakeYouTube(handle="@farrukhprivate"))

    with pytest.raises(FactoryError, match="not @coilandcog: the token was not saved"):
        publish.connect_youtube(ctx, sign_in=lambda p: creds(), builder=other)
    assert not token_file(ctx).exists()


def test_missing_client_file_explains_where_it_comes_from(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="download the Desktop OAuth client"):
        youtube_auth.sign_in(tmp_path / "google_client_secret.json")


def test_test_upload_is_unlisted_ai_labelled_and_counted(ctx: AppContext) -> None:
    api = FakeYouTube()
    youtube_auth.save_token(creds(), token_file(ctx))

    link = publish.test_upload(ctx, builder=builder_for(api))

    assert link == "https://youtu.be/abcdefghijk"
    sent = api.inserted[0]
    assert sent["part"] == "snippet,status"
    assert sent["body"]["status"] == {
        "privacyStatus": "unlisted", "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": True,
    }  # fmt: skip
    assert sent["body"]["snippet"]["title"] == "Coil & Cog API test - delete me"
    assert sent["body"]["snippet"]["categoryId"] == "28"
    assert usage_today(ctx.conn, ctx.channel)["youtube_data"][0] == 2  # channel check + upload


def test_test_upload_needs_dry_run_off(ctx: AppContext) -> None:
    dry = AppContext(ctx.settings, EnvSettings(dry_run=True, factory_channel="coil-and-cog"),
                     ctx.channel, ctx.conn, ctx.root)  # fmt: skip
    api = FakeYouTube()

    with pytest.raises(FactoryError, match="DRY_RUN=false uv run factory auth test-upload"):
        publish.test_upload(dry, builder=builder_for(api))
    assert api.inserted == []


def test_quota_stop_is_a_budget_stop(ctx: AppContext) -> None:
    content = json.dumps({"error": {"code": 403, "message": "quota", "errors": [
        {"reason": "uploadLimitExceeded"}]}}).encode()  # fmt: skip
    api = FakeYouTube(upload_answer=HttpError(Response({"status": 403}), content))
    youtube_auth.save_token(creds(), token_file(ctx))

    with pytest.raises(BudgetExceededError, match="uploadLimitExceeded"):
        publish.test_upload(ctx, builder=builder_for(api))


def test_upload_refuses_an_unknown_privacy() -> None:
    with pytest.raises(ValueError, match="privacy must be one of"):
        youtube.upload(FakeYouTube(), "x.mp4", title="t", description="d", category_id=28,
                       privacy="friends", before_call=lambda u: None)  # fmt: skip


def test_saved_token_is_renewed_when_expired(tmp_path: Path) -> None:
    path = tmp_path / "google_token.json"
    youtube_auth.save_token(creds(expired=True), path)
    renewed: list[Credentials] = []

    def refresh(c: Credentials) -> None:
        renewed.append(c)
        c.token, c.expiry = "fresh", datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    loaded = youtube_auth.load_credentials(path, refresh=refresh)

    assert len(renewed) == 1 and loaded.token == "fresh"
    assert json.loads(path.read_text())["token"] == "fresh"  # saved again


@pytest.mark.parametrize(
    ("saved", "refresh", "problem"),
    [
        (None, None, "no YouTube sign-in yet"),
        (creds(scopes=youtube_auth.SCOPES[:1]), None, "lacks permissions"),
        (creds(expired=True), RefreshError("revoked"), "Google refused to renew"),
    ],
)
def test_unusable_tokens_say_to_sign_in_again(
    tmp_path: Path, saved: Credentials | None, refresh: Exception | None, problem: str
) -> None:
    path = tmp_path / "google_token.json"
    if saved is not None:
        youtube_auth.save_token(saved, path)

    def fail(c: Credentials) -> None:
        raise refresh

    with pytest.raises(ConfigError, match=problem):
        youtube_auth.load_credentials(path, refresh=fail)
