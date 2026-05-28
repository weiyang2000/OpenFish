import asyncio
import json
import signal
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.schemas import CreateCrawlerAccountLoginSessionRequest
from apps.api.services import accounts as accounts_module
from apps.api.services.accounts import AccountService
from apps.api.services.tasks import TaskService


WORKSPACE_HEADERS = {"X-Workspace-Id": "workspace_demo"}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(
        db_path=tmp_path / "saas_api.sqlite3",
        artifact_dir=tmp_path / "artifacts",
        repo_root=Path.cwd(),
        run_workers=False,
    )
    return TestClient(app)


def test_system_components_do_not_expose_legacy_ui_ports(client: TestClient):
    response = client.get("/api/v1/system/components", headers=WORKSPACE_HEADERS)
    assert response.status_code == 200

    components = {component["id"]: component for component in response.json()["components"]}
    for component_id in ("query", "media", "insight"):
        assert "port" not in components[component_id]
        assert components[component_id]["status"] == "running"
    assert components["forum"]["status"] == "running"
    assert components["report"]["status"] == "running"


def test_cors_preflight_allows_local_console_origin(client: TestClient):
    response = client.options(
        "/api/v1/system/components",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type,x-workspace-id",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_system_config_masks_secrets_and_ignores_mask_placeholder(client: TestClient):
    response = client.patch(
        "/api/v1/system/config",
        headers=WORKSPACE_HEADERS,
        json={
            "values": {
                "REPORT_ENGINE_API_KEY": "sk-real-secret",
                "SEARCH_TOOL_TYPE": "BochaAPI",
                "INSIGHT_MODE": "deep",
                "MAX_REFLECTIONS": 2,
            }
        },
    )
    assert response.status_code == 200

    fields = _config_fields(client)
    assert fields["REPORT_ENGINE_API_KEY"]["value"] == "********"
    assert fields["REPORT_ENGINE_API_KEY"]["sensitive"] is True
    assert fields["SEARCH_TOOL_TYPE"]["value"] == "BochaAPI"
    assert fields["INSIGHT_MODE"]["value"] == "deep"
    assert fields["INSIGHT_MODE"]["group"] == "llm"
    assert fields["INSIGHT_MODE"]["type"] == "enum"
    assert fields["INSIGHT_MODE"]["options"] == ["fast", "normal", "deep"]
    assert fields["MAX_REFLECTIONS"]["value"] == "2"
    assert fields["MAX_REFLECTIONS"]["group"] == "engine"
    assert fields["MAX_REFLECTIONS"]["type"] == "number"

    response = client.patch(
        "/api/v1/system/config",
        headers=WORKSPACE_HEADERS,
        json={"values": {"REPORT_ENGINE_API_KEY": "********"}},
    )
    assert response.status_code == 200
    assert _config_fields(client)["REPORT_ENGINE_API_KEY"]["value"] == "********"


def test_shared_engine_settings_include_reflection_fields():
    from config import reload_settings

    settings = reload_settings()
    for key in (
        "MAX_REFLECTIONS",
        "INSIGHT_MODE",
        "MAX_SEARCH_RESULTS_FOR_LLM",
        "DEFAULT_SEARCH_HOT_CONTENT_LIMIT",
        "DEFAULT_SEARCH_TOPIC_GLOBALLY_LIMIT_PER_TABLE",
        "DEFAULT_SEARCH_TOPIC_BY_DATE_LIMIT_PER_TABLE",
        "DEFAULT_GET_COMMENTS_FOR_TOPIC_LIMIT",
        "DEFAULT_SEARCH_TOPIC_ON_PLATFORM_LIMIT",
    ):
        assert hasattr(settings, key)


def test_identity_allow_block_conflict_and_delete(client: TestClient):
    allow_response = client.post(
        "/api/v1/platforms/wb/identity-lists",
        headers=WORKSPACE_HEADERS,
        json={"listType": "allow", "userId": "user-001", "label": "trusted"},
    )
    assert allow_response.status_code == 201
    rule_id = allow_response.json()["rule"]["id"]

    block_response = client.post(
        "/api/v1/platforms/wb/identity-lists",
        headers=WORKSPACE_HEADERS,
        json={"listType": "block", "userId": "user-001"},
    )
    assert block_response.status_code == 409
    assert block_response.json()["error"]["code"] == "CONFLICT"

    list_response = client.get(
        "/api/v1/platforms/wb/identity-lists?listType=allow",
        headers=WORKSPACE_HEADERS,
    )
    assert list_response.status_code == 200
    assert [rule["userId"] for rule in list_response.json()["rules"]] == ["user-001"]

    delete_response = client.delete(
        f"/api/v1/platforms/wb/identity-lists/{rule_id}",
        headers=WORKSPACE_HEADERS,
    )
    assert delete_response.status_code == 204


def test_platform_policy_update_round_trip(client: TestClient):
    payload = {
        "enabled": True,
        "crawlDepth": 4,
        "maxKeywords": 20,
        "maxNotesPerKeyword": 10,
        "maxCommentsPerNote": 50,
        "keywords": ["养老服务", "养老服务", "医保"],
        "keywordSource": "manual",
        "frequency": {"mode": "daily", "timezone": "Asia/Shanghai"},
        "loginType": "qrcode",
        "headless": True,
    }
    response = client.put(
        "/api/v1/platforms/xhs/policy",
        headers=WORKSPACE_HEADERS,
        json=payload,
    )
    assert response.status_code == 200
    policy = response.json()["policy"]
    assert policy["platformId"] == "xhs"
    assert policy["crawlDepth"] == 4
    assert policy["keywords"] == ["养老服务", "医保"]


def test_crawler_accounts_list_filters_without_sensitive_credentials(client: TestClient):
    upsert = client.put(
        "/api/v1/crawler-accounts/wb_1088",
        headers=WORKSPACE_HEADERS,
        json={
            "platformId": "wb",
            "username": "bettafish_ops",
            "displayName": "BettaFish 运营号",
            "status": "active",
            "loginType": "qrcode",
            "lastCheckedAt": "2026-05-22T08:30:00Z",
            "details": {
                "message": "账号可用于搜索和评论采集。",
                "accessToken": "not-allowed",
                "nested": {"cookie": "not-allowed", "safe": "kept"},
            },
        },
    )
    assert upsert.status_code == 200

    response = client.get("/api/v1/crawler-accounts?platform=wb", headers=WORKSPACE_HEADERS)
    assert response.status_code == 200

    accounts = response.json()["accounts"]
    assert accounts
    assert {account["platformId"] for account in accounts} == {"wb"}
    account = accounts[0]
    assert account["status"] == "active"
    assert account["accountId"] == "wb_1088"
    assert account["displayName"] == "BettaFish 运营号"
    assert "lastCheckedAt" in account
    assert account["details"]["nested"] == {"safe": "kept"}

    serialized = json.dumps(account, ensure_ascii=False)
    for forbidden_text in ("password", "token", "secret", "cookie", "not-allowed"):
        assert forbidden_text not in serialized.lower()


def test_crawler_accounts_are_workspace_scoped_and_page_sized(client: TestClient):
    workspace_a = {"X-Workspace-Id": "workspace_accounts_a"}
    workspace_b = {"X-Workspace-Id": "workspace_accounts_b"}

    response = client.put(
        "/api/v1/crawler-accounts/wb_public_2001",
        headers=workspace_a,
        json={"platformId": "wb", "displayName": "微博采集号", "status": "active"},
    )
    assert response.status_code == 200

    list_response = client.get(
        "/api/v1/crawler-accounts?platform=wb&status=active&pageSize=1",
        headers=workspace_a,
    )
    assert list_response.status_code == 200
    assert [item["accountId"] for item in list_response.json()["accounts"]] == [
        "wb_public_2001"
    ]

    other_workspace = client.get("/api/v1/crawler-accounts", headers=workspace_b)
    assert other_workspace.status_code == 200
    assert other_workspace.json()["accounts"] == []


def test_crawler_account_can_be_deleted(client: TestClient):
    upsert = client.put(
        "/api/v1/crawler-accounts/acct_delete_me",
        headers=WORKSPACE_HEADERS,
        json={"platformId": "wb", "displayName": "待删除账号", "status": "active"},
    )
    assert upsert.status_code == 200
    internal_id = upsert.json()["account"]["id"]

    delete_response = client.delete(
        f"/api/v1/crawler-accounts/{internal_id}",
        headers=WORKSPACE_HEADERS,
    )
    assert delete_response.status_code == 204

    response = client.get("/api/v1/crawler-accounts", headers=WORKSPACE_HEADERS)
    assert response.status_code == 200
    assert response.json()["accounts"] == []


def test_crawler_account_rejects_top_level_secret_fields(client: TestClient):
    response = client.put(
        "/api/v1/crawler-accounts/wb_secret",
        headers=WORKSPACE_HEADERS,
        json={"platformId": "wb", "status": "active", "token": "not-allowed"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_crawler_account_login_session_persists_account_without_returning_credentials(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_login_capture(
        self: AccountService,
        session_id: str,
        workspace_id: str,
        payload,
    ) -> None:
        assert payload.headless is True
        account = self._persist_logged_in_account(
            workspace_id,
            payload.platformId,
            payload.loginType,
            session_id,
            self._profile_dir(payload.platformId),
            {"SUB": "secret-session-value", "SUBP": "secret-profile-value"},
        )
        self._update_login_session(
            session_id,
            status="completed",
            message="登录状态已保存",
            account=account,
        )

    monkeypatch.setattr(AccountService, "_run_login_session", fake_login_capture)

    response = client.post(
        "/api/v1/crawler-accounts/login-sessions",
        headers=WORKSPACE_HEADERS,
        json={"platformId": "wb", "loginType": "qrcode"},
    )
    assert response.status_code == 202
    session_id = response.json()["session"]["id"]

    session = _wait_for_login_session(client, session_id)
    assert session["status"] == "completed"
    assert session["account"]["platformId"] == "wb"

    serialized = json.dumps(session, ensure_ascii=False).lower()
    assert "secret-session-value" not in serialized
    assert "secret-profile-value" not in serialized
    assert "cookie" not in serialized


def test_crawler_account_login_session_replaces_active_platform_session(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_run_login_session(*_: Any) -> None:
        return None

    monkeypatch.setattr(AccountService, "_run_login_session", fake_run_login_session)
    service: AccountService = client.app.state.account_service
    service._login_sessions["login_active"] = {
        "id": "login_active",
        "workspaceId": WORKSPACE_HEADERS["X-Workspace-Id"],
        "platformId": "wb",
        "loginType": "qrcode",
        "status": "waiting",
        "loginUrl": "https://passport.weibo.com/sso/signin?entry=miniblog&source=miniblog",
        "createdAt": "2026-05-24T00:00:00Z",
        "updatedAt": "2026-05-24T00:00:00Z",
        "expiresAt": "2099-01-01T00:00:00Z",
        "message": "请用手机扫码完成登录",
    }

    response = client.post(
        "/api/v1/crawler-accounts/login-sessions",
        headers=WORKSPACE_HEADERS,
        json={"platformId": "wb", "loginType": "qrcode"},
    )

    assert response.status_code == 202
    session = response.json()["session"]
    assert session["id"] != "login_active"
    assert session["status"] == "opening"
    assert service._login_sessions["login_active"]["status"] == "failed"
    assert service._login_sessions["login_active"]["error"]["code"] == "LOGIN_SESSION_REPLACED"
    assert set(service._login_sessions) == {"login_active", session["id"]}


def test_crawler_account_login_session_reports_profile_lock_without_raw_browser_log(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_capture_login(*_: Any) -> dict[str, Any]:
        raise RuntimeError(
            "BrowserType.launch_persistent_context: Target page, context or browser has been closed. "
            "The profile appears to be in use by another Chromium process."
        )

    monkeypatch.setattr(AccountService, "_capture_login", fake_capture_login)
    service: AccountService = client.app.state.account_service
    session_id = "login_locked"
    service._login_sessions[session_id] = {
        "id": session_id,
        "workspaceId": WORKSPACE_HEADERS["X-Workspace-Id"],
        "platformId": "wb",
        "loginType": "qrcode",
        "status": "opening",
        "loginUrl": "https://passport.weibo.com/sso/signin?entry=miniblog&source=miniblog",
        "createdAt": "2026-05-24T00:00:00Z",
        "updatedAt": "2026-05-24T00:00:00Z",
    }

    service._run_login_session(
        session_id,
        WORKSPACE_HEADERS["X-Workspace-Id"],
        CreateCrawlerAccountLoginSessionRequest(platformId="wb", loginType="qrcode"),
    )
    session = service.get_login_session(WORKSPACE_HEADERS["X-Workspace-Id"], session_id)

    assert session["status"] == "failed"
    assert session["error"]["code"] == "LOGIN_PROFILE_BUSY"
    assert "系统已尝试停止旧进程" in session["message"]
    assert "BrowserType.launch_persistent_context" not in session["message"]


def test_login_session_kills_existing_chromium_for_profile(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    service: AccountService = client.app.state.account_service
    profile_dir = tmp_path / "cloak_wb_user_data_dir"
    profile_dir.mkdir()
    for filename in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        (profile_dir / filename).write_text("locked", encoding="utf-8")

    pid_reads = [[123], [], []]
    kills: list[tuple[int, int]] = []

    def fake_profile_browser_pids(_: AccountService, profile: Path) -> list[int]:
        assert profile == profile_dir
        return pid_reads.pop(0) if pid_reads else []

    def fake_kill(pid: int, sig: int) -> None:
        kills.append((pid, sig))

    monkeypatch.setattr(AccountService, "_profile_browser_pids", fake_profile_browser_pids)
    monkeypatch.setattr(accounts_module.os, "kill", fake_kill)

    assert service._terminate_profile_browsers(profile_dir) == 1
    assert kills == [(123, signal.SIGTERM)]
    for filename in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        assert not (profile_dir / filename).exists()


def test_headless_login_session_publishes_qrcode_preview(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    calls: dict[str, Any] = {}

    class FakeLocator:
        def __init__(self, selector: str):
            self.selector = selector

        def nth(self, index: int) -> "FakeLocator":
            calls["nth"] = index
            return self

        async def wait_for(self, **_: Any) -> None:
            calls["waited_selector"] = self.selector

        async def click(self, **_: Any) -> None:
            calls["clicked_selector"] = self.selector

        async def screenshot(self, **_: Any) -> bytes:
            calls["preview_selector"] = self.selector
            return b"qr-image"

    class FakePage:
        async def goto(self, url: str, **_: Any) -> None:
            calls["goto"] = url

        def locator(self, selector: str) -> FakeLocator:
            return FakeLocator(selector)

        async def wait_for_timeout(self, timeout: int) -> None:
            calls["wait_for_timeout"] = timeout

        async def screenshot(self, **_: Any) -> bytes:
            calls["page_screenshot"] = True
            return b"page-image"

    class FakeContext:
        async def new_page(self) -> FakePage:
            return FakePage()

        async def cookies(self) -> list[dict[str, str]]:
            return [{"name": "SUB", "value": "secret-session-value"}]

        async def close(self) -> None:
            calls["closed"] = True

    fake_cloakbrowser = types.ModuleType("cloakbrowser")

    async def launch_persistent_context_async(user_data_dir: str, **kwargs: Any) -> FakeContext:
        calls["user_data_dir"] = user_data_dir
        calls["launch_kwargs"] = kwargs
        return FakeContext()

    fake_cloakbrowser.launch_persistent_context_async = launch_persistent_context_async
    monkeypatch.setitem(sys.modules, "cloakbrowser", fake_cloakbrowser)
    monkeypatch.setenv("BETTAFISH_CRAWLER_BROWSER_DATA_DIR", str(tmp_path / "browser_data"))

    service: AccountService = client.app.state.account_service
    session_id = "login_preview"
    service._login_sessions[session_id] = {
        "id": session_id,
        "workspaceId": WORKSPACE_HEADERS["X-Workspace-Id"],
        "platformId": "wb",
        "loginType": "qrcode",
        "status": "opening",
        "loginUrl": "https://passport.weibo.com/sso/signin?entry=miniblog&source=miniblog",
        "createdAt": "2026-05-24T00:00:00Z",
        "updatedAt": "2026-05-24T00:00:00Z",
    }

    payload = CreateCrawlerAccountLoginSessionRequest(platformId="wb", loginType="qrcode")
    account = asyncio.run(
        service._capture_login(
            session_id,
            WORKSPACE_HEADERS["X-Workspace-Id"],
            payload,
        )
    )
    session = service.get_login_session(WORKSPACE_HEADERS["X-Workspace-Id"], session_id)

    assert calls["launch_kwargs"]["headless"] is True
    assert calls["preview_selector"] == "xpath=//img[@class='w-full h-full']"
    assert calls["closed"] is True
    assert account["platformId"] == "wb"
    assert session["loginPreviewImage"] == "data:image/png;base64,cXItaW1hZ2U="
    assert session["loginPreviewKind"] == "qrcode"


def test_login_state_markers_do_not_accept_visitor_cookies():
    assert not AccountService._has_required_login_state("xhs", {"a1": "visitor-cookie"})
    assert not AccountService._has_required_login_state(
        "xhs",
        {"web_session": "unchanged"},
        {"web_session": "unchanged"},
    )
    assert AccountService._has_required_login_state(
        "xhs",
        {"web_session": "after-scan"},
        {"web_session": "before-scan"},
    )
    assert not AccountService._has_required_login_state("ks", {"did": "visitor-device"})
    assert AccountService._has_required_login_state("ks", {"passToken": "auth-token"})


def test_crawler_data_search_reads_crawler_tables(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    store = client.app.state.store
    monkeypatch.setenv("BETTAFISH_CRAWLER_SQLITE_PATH", store.db_path)
    store.execute(
        """
        CREATE TABLE xhs_note (
            id INTEGER PRIMARY KEY,
            note_id TEXT,
            title TEXT,
            desc TEXT,
            nickname TEXT,
            note_url TEXT,
            source_keyword TEXT,
            time INTEGER,
            add_ts INTEGER,
            liked_count TEXT,
            comment_count TEXT,
            sentiment TEXT
        )
        """
    )
    store.execute(
        """
        INSERT INTO xhs_note (
            note_id, title, desc, nickname, note_url, source_keyword,
            time, add_ts, liked_count, comment_count, sentiment
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "note_001",
            "养老服务体验",
            "社区护理服务响应速度提升",
            "researcher",
            "https://example.test/note_001",
            "养老服务",
            1760000000,
            1760000100,
            "12",
            "3",
            "正向",
        ),
    )

    response = client.get(
        "/api/v1/crawler-data?platform=xhs&contentType=content&q=护理",
        headers=WORKSPACE_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["totalRecords"] == 1
    assert body["records"][0]["sourceId"] == "note_001"
    assert body["records"][0]["platformId"] == "xhs"
    assert body["records"][0]["sentiment"] == "positive"

    delete_response = client.delete(
        "/api/v1/crawler-data?tableName=xhs_note&sourceId=note_001&platform=xhs&contentType=content",
        headers=WORKSPACE_HEADERS,
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] == 1

    response = client.get(
        "/api/v1/crawler-data?platform=xhs&contentType=content&q=护理",
        headers=WORKSPACE_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["summary"]["totalRecords"] == 0

    invalid_delete = client.delete(
        "/api/v1/crawler-data?tableName=sqlite_master&sourceId=note_001",
        headers=WORKSPACE_HEADERS,
    )
    assert invalid_delete.status_code == 400
    assert invalid_delete.json()["error"]["code"] == "VALIDATION_ERROR"


def test_crawler_data_paginates_and_batch_deletes_records(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    store = client.app.state.store
    monkeypatch.setenv("BETTAFISH_CRAWLER_SQLITE_PATH", store.db_path)
    store.execute(
        """
        CREATE TABLE weibo_note (
            id INTEGER PRIMARY KEY,
            note_id TEXT,
            content TEXT,
            nickname TEXT,
            source_keyword TEXT,
            create_time INTEGER,
            add_ts INTEGER,
            liked_count TEXT,
            comments_count TEXT
        )
        """
    )
    for index in range(5):
        store.execute(
            """
            INSERT INTO weibo_note (
                note_id, content, nickname, source_keyword, create_time,
                add_ts, liked_count, comments_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"note_{index}",
                f"分页测试内容 {index}",
                "tester",
                "分页测试",
                1760000000 + index,
                1760000100 + index,
                str(index),
                "0",
            ),
        )

    response = client.get(
        "/api/v1/crawler-data?platform=wb&contentType=content&page=2&pageSize=2",
        headers=WORKSPACE_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["totalRecords"] == 5
    assert body["pageInfo"] == {
        "page": 2,
        "pageSize": 2,
        "totalRecords": 5,
        "totalPages": 3,
        "hasPreviousPage": True,
        "hasNextPage": True,
    }
    assert [record["sourceId"] for record in body["records"]] == ["note_2", "note_1"]

    delete_response = client.post(
        "/api/v1/crawler-data:delete",
        headers=WORKSPACE_HEADERS,
        json={
            "records": [
                {
                    "tableName": "weibo_note",
                    "sourceId": "note_2",
                    "platform": "wb",
                    "contentType": "content",
                },
                {
                    "tableName": "weibo_note",
                    "sourceId": "note_1",
                    "platform": "wb",
                    "contentType": "content",
                },
            ]
        },
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] == 2

    response = client.get(
        "/api/v1/crawler-data?platform=wb&contentType=content&page=1&pageSize=10",
        headers=WORKSPACE_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["summary"]["totalRecords"] == 3
    assert {record["sourceId"] for record in response.json()["records"]} == {
        "note_0",
        "note_3",
        "note_4",
    }


def test_crawler_data_does_not_read_sqlite_without_explicit_opt_in(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    store = client.app.state.store
    monkeypatch.delenv("BETTAFISH_CRAWLER_SQLITE_PATH", raising=False)
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("DB_DIALECT", "postgresql")
    monkeypatch.setenv("DB_HOST", "your_db_host")
    monkeypatch.setenv("DB_USER", "your_db_user")
    monkeypatch.setenv("DB_PASSWORD", "your_db_password")
    monkeypatch.setenv("DB_NAME", "your_db_name")
    store.execute(
        """
        CREATE TABLE xhs_note (
            id INTEGER PRIMARY KEY,
            note_id TEXT,
            title TEXT,
            desc TEXT,
            add_ts INTEGER
        )
        """
    )
    store.execute(
        """
        INSERT INTO xhs_note (note_id, title, desc, add_ts)
        VALUES (?, ?, ?, ?)
        """,
        ("note_sqlite_only", "SQLite 残留数据", "不应被爬取数据接口读取", 1760000100),
    )

    response = client.get(
        "/api/v1/crawler-data?platform=xhs&contentType=content&q=SQLite",
        headers=WORKSPACE_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["totalRecords"] == 0
    assert body["source"] == "unavailable"


def test_crawler_strategy_platform_policy_round_trip_and_invalid_platform(client: TestClient):
    payload = {
        "name": "微博每日采集",
        "runMode": "deep_sentiment",
        "platformPolicies": [_strategy_policy("wb")],
    }
    response = client.post(
        "/api/v1/crawler-strategies",
        headers=WORKSPACE_HEADERS,
        json=payload,
    )
    assert response.status_code == 201
    strategy = response.json()["strategy"]
    assert strategy["platformPolicies"][0]["platformId"] == "wb"

    list_response = client.get("/api/v1/crawler-strategies", headers=WORKSPACE_HEADERS)
    assert list_response.status_code == 200
    assert list_response.json()["strategies"][0]["platformPolicies"][0]["platformId"] == "wb"

    invalid_payload = {
        **payload,
        "name": "非法平台策略",
        "platformPolicies": [_strategy_policy("manual")],
    }
    invalid_response = client.post(
        "/api/v1/crawler-strategies",
        headers=WORKSPACE_HEADERS,
        json=invalid_payload,
    )
    assert invalid_response.status_code == 422
    assert invalid_response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_report_task_basic_lifecycle_and_events(client: TestClient):
    templates_response = client.get("/api/v1/report-templates", headers=WORKSPACE_HEADERS)
    assert templates_response.status_code == 200
    assert templates_response.json()["templates"][0]["id"] == "auto"

    auto_response = client.post(
        "/api/v1/report-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "topic": "自动模板选择验证",
            "outputFormats": ["html"],
        },
    )
    assert auto_response.status_code == 202
    auto_task = auto_response.json()["task"]
    assert auto_task["templateId"] == "auto"
    assert auto_task["workspaceId"] == WORKSPACE_HEADERS["X-Workspace-Id"]
    assert "tenantId" not in auto_task

    create_response = client.post(
        "/api/v1/report-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "topic": "养老服务发展趋势",
            "templateId": "daily-monitoring",
            "outputFormats": ["html", "md"],
        },
    )
    assert create_response.status_code == 202
    task = create_response.json()["task"]
    assert task["status"] == "queued"
    assert task["workspaceId"] == WORKSPACE_HEADERS["X-Workspace-Id"]
    assert "tenantId" not in task
    task_service: TaskService = client.app.state.task_service
    auto_task_workspace = task_service._report_task_workspace(auto_task["workspaceId"], auto_task["id"])
    task_workspace = task_service._report_task_workspace(task["workspaceId"], task["id"])
    assert auto_task_workspace.parent == task_workspace.parent
    assert auto_task_workspace.name == auto_task["id"]
    assert task_workspace.name == task["id"]
    assert create_response.json()["eventStreamUrl"].endswith(f"{task['id']}/events")

    get_response = client.get(f"/api/v1/report-tasks/{task['id']}", headers=WORKSPACE_HEADERS)
    assert get_response.status_code == 200
    assert get_response.json()["task"]["topic"] == "养老服务发展趋势"
    assert get_response.json()["task"]["workspaceId"] == task["workspaceId"]

    event_response = client.get(
        f"/api/v1/report-tasks/{task['id']}/events",
        headers=WORKSPACE_HEADERS,
    )
    assert event_response.status_code == 200
    assert "event: status" in event_response.text

    log_response = client.get(
        f"/api/v1/report-tasks/{task['id']}/logs",
        headers=WORKSPACE_HEADERS,
    )
    assert log_response.status_code == 200
    log_body = log_response.json()
    assert log_body["taskType"] == "report"
    assert log_body["events"][0]["type"] == "status"
    assert log_body["events"][0]["taskId"] == task["id"]

    cancel_response = client.post(
        f"/api/v1/report-tasks/{task['id']}:cancel",
        headers=WORKSPACE_HEADERS,
    )
    assert cancel_response.status_code == 202
    assert cancel_response.json()["task"]["status"] == "cancelled"

    second_cancel = client.post(
        f"/api/v1/report-tasks/{task['id']}:cancel",
        headers=WORKSPACE_HEADERS,
    )
    assert second_cancel.status_code == 409
    assert second_cancel.json()["error"]["code"] == "TASK_NOT_CANCELLABLE"


def test_report_export_browser_url_and_task_delete(client: TestClient):
    create_response = client.post(
        "/api/v1/report-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "topic": "可下载报告",
            "outputFormats": ["html", "pdf"],
        },
    )
    assert create_response.status_code == 202
    task = create_response.json()["task"]
    task_id = task["id"]
    task_workspace_id = task["workspaceId"]

    task_service: TaskService = client.app.state.task_service
    html_path = task_service.artifact_path(task_id, "html", task_workspace_id)
    pdf_path = task_service.artifact_path(task_id, "pdf", task_workspace_id)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text("<!doctype html><h1>报告下载</h1>", encoding="utf-8")
    pdf_path.write_bytes(b"%PDF-1.4\n%EOF\n")
    task_service._complete_report_task(
        WORKSPACE_HEADERS["X-Workspace-Id"],
        task_id,
        [
            {
                "format": "html",
                "ready": True,
                "filename": "report.html",
                "sizeBytes": html_path.stat().st_size,
                "downloadUrl": f"/api/v1/report-tasks/{task_id}/exports/html?workspaceId={task_workspace_id}",
            },
            {
                "format": "pdf",
                "ready": True,
                "filename": "report.pdf",
                "sizeBytes": pdf_path.stat().st_size,
                "downloadUrl": f"/api/v1/report-tasks/{task_id}/exports/pdf?workspaceId={task_workspace_id}",
            },
        ],
    )

    html_export = client.get(
        f"/api/v1/report-tasks/{task_id}/exports/html?workspaceId={task_workspace_id}"
    )
    assert html_export.status_code == 200
    assert "text/html" in html_export.headers["content-type"]

    pdf_export = client.get(
        f"/api/v1/report-tasks/{task_id}/exports/pdf?workspaceId={task_workspace_id}"
    )
    assert pdf_export.status_code == 200
    assert "application/pdf" in pdf_export.headers["content-type"]

    delete_response = client.delete(
        f"/api/v1/report-tasks/{task_id}",
        headers=WORKSPACE_HEADERS,
    )
    assert delete_response.status_code == 204
    assert not html_path.exists()
    assert not pdf_path.exists()
    assert client.get(f"/api/v1/report-tasks/{task_id}", headers=WORKSPACE_HEADERS).status_code == 404
    assert client.get(f"/api/v1/report-tasks/{task_id}/logs", headers=WORKSPACE_HEADERS).status_code == 404


def test_report_worker_uses_topic_seed_when_inputs_are_empty(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, Any] = {}

    class FakeReportAgent:
        def __init__(self, config: Any = None):
            captured["config"] = config

        def generate_report(
            self,
            *,
            query: str,
            reports: list[str],
            forum_logs: str,
            custom_template: str,
            save_report: bool,
            stream_handler: Any,
        ) -> dict[str, Any]:
            captured["query"] = query
            captured["reports"] = reports
            captured["forum_logs"] = forum_logs
            captured["custom_template"] = custom_template
            captured["save_report"] = save_report
            stream_handler("progress", {"progress": 35})
            return {"html_content": "<!doctype html><h1>主题初稿</h1>"}

    monkeypatch.setitem(
        sys.modules,
        "ReportEngine.agent",
        types.SimpleNamespace(
            ReportAgent=FakeReportAgent,
            create_agent=lambda *args, **kwargs: FakeReportAgent(*args, **kwargs),
        ),
    )
    monkeypatch.setattr(TaskService, "_load_latest_engine_reports", staticmethod(lambda: []))
    orchestration_calls: list[str] = []

    def empty_orchestration(
        self: TaskService,
        workspace_id: str,
        task: dict[str, Any],
        task_workspace: Path,
        base_settings: Any,
    ) -> tuple[list[str], str]:
        del self, workspace_id, task, base_settings
        orchestration_calls.append(str(task_workspace))
        return [], ""

    monkeypatch.setattr(TaskService, "_run_report_orchestration", empty_orchestration)

    config_response = client.patch(
        "/api/v1/system/config",
        headers=WORKSPACE_HEADERS,
        json={
            "values": {
                "REPORT_ENGINE_API_KEY": "sk-test-report",
                "REPORT_ENGINE_BASE_URL": "https://example.test/v1",
                "REPORT_ENGINE_MODEL_NAME": "test-report-model",
            }
        },
    )
    assert config_response.status_code == 200

    create_response = client.post(
        "/api/v1/report-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "topic": "COTY香水舆情分析",
            "outputFormats": ["html"],
        },
    )
    assert create_response.status_code == 202
    task_id = create_response.json()["task"]["id"]
    task_workspace_id = create_response.json()["task"]["workspaceId"]

    task_service: TaskService = client.app.state.task_service
    task_service._run_report_worker(WORKSPACE_HEADERS["X-Workspace-Id"], task_id)

    completed = task_service.get_report_task(WORKSPACE_HEADERS["X-Workspace-Id"], task_id)
    assert completed["status"] == "succeeded"
    assert captured["query"] == "COTY香水舆情分析"
    assert orchestration_calls
    assert task_id in orchestration_calls[0]
    assert task_workspace_id in orchestration_calls[0]
    assert captured["config"].REPORT_ENGINE_API_KEY == "sk-test-report"
    assert captured["config"].REPORT_ENGINE_MODEL_NAME == "test-report-model"
    from ReportEngine.utils.config import settings as report_settings

    assert report_settings.REPORT_ENGINE_API_KEY == "sk-test-report"
    assert "COTY香水舆情分析" in captured["reports"][0]
    assert "不要编造具体平台声量" in captured["reports"][0]

    logs = client.get(f"/api/v1/report-tasks/{task_id}/logs", headers=WORKSPACE_HEADERS)
    assert logs.status_code == 200
    warning = [event for event in logs.json()["events"] if event["type"] == "warning"]
    assert warning[0]["payload"]["payload"]["code"] == "NO_REPORT_INPUTS"


def test_report_worker_orchestrates_pre_report_engines_before_report(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, Any] = {}

    class FakeReportAgent:
        def __init__(self, config: Any = None):
            captured["report_config"] = config

        def generate_report(
            self,
            *,
            query: str,
            reports: list[str],
            forum_logs: str,
            custom_template: str,
            save_report: bool,
            stream_handler: Any,
        ) -> dict[str, Any]:
            captured["query"] = query
            captured["reports"] = reports
            captured["forum_logs"] = forum_logs
            captured["custom_template"] = custom_template
            captured["save_report"] = save_report
            stream_handler("progress", {"progress": 90})
            return {"html_content": "<!doctype html><h1>多引擎报告</h1>"}

    monkeypatch.setitem(
        sys.modules,
        "ReportEngine.agent",
        types.SimpleNamespace(
            ReportAgent=FakeReportAgent,
            create_agent=lambda *args, **kwargs: FakeReportAgent(*args, **kwargs),
        ),
    )

    def fake_pre_engine(
        self: TaskService,
        engine_id: str,
        topic: str,
        task_workspace: Path,
        base_settings: Any,
        task_id: str,
    ) -> dict[str, Any]:
        del self, base_settings
        captured.setdefault("call_order", []).append(f"{engine_id}_after_{captured.get('forum_started')}")
        artifact_path = task_workspace / engine_id / f"{engine_id}_report.md"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        report = f"{engine_id} report for {topic}"
        artifact_path.write_text(report, encoding="utf-8")
        return {
            "engine": engine_id,
            "status": "succeeded",
            "report": report,
            "artifactPath": str(artifact_path),
            "startedAt": "2026-05-25T04:30:00Z",
            "completedAt": "2026-05-25T04:31:00Z",
        }

    monkeypatch.setattr(TaskService, "_run_pre_report_engine", fake_pre_engine)

    class FakeForumMonitor:
        def __init__(self, forum_path: Path):
            self.forum_log_file = forum_path

    def fake_start_forum_monitor(
        self: TaskService,
        workspace_id: str,
        task_id: str,
        task_workspace: Path,
    ) -> FakeForumMonitor:
        captured["forum_started"] = True
        forum_path = task_workspace / "forum" / "forum.log"
        forum_path.parent.mkdir(parents=True, exist_ok=True)
        self.add_event(
            workspace_id,
            task_id,
            "report",
            "orchestration",
            {
                "payload": {
                    "stage": "forum_monitor_started",
                    "engine": "forum",
                    "status": "running",
                    "artifactPath": str(forum_path),
                }
            },
        )
        return FakeForumMonitor(forum_path)

    def fake_stop_forum_monitor(
        self: TaskService,
        workspace_id: str,
        task_id: str,
        monitor: FakeForumMonitor,
    ) -> str:
        forum_logs = "\n".join(
            [
                "[04:30:00] [QUERY] query report for COTY香水舆情分析",
                "[04:30:00] [MEDIA] media report for COTY香水舆情分析",
                "[04:30:00] [INSIGHT] insight report for COTY香水舆情分析",
                f"[04:30:00] [HOST] host summary for {task_id}",
            ]
        )
        monitor.forum_log_file.write_text(forum_logs, encoding="utf-8")
        self.add_event(
            workspace_id,
            task_id,
            "report",
            "orchestration",
            {
                "payload": {
                    "stage": "forum_monitor_stopped",
                    "engine": "forum",
                    "status": "succeeded",
                    "artifactPath": str(monitor.forum_log_file),
                }
            },
        )
        return forum_logs

    monkeypatch.setattr(TaskService, "_start_report_forum_monitor", fake_start_forum_monitor)
    monkeypatch.setattr(TaskService, "_stop_report_forum_monitor", fake_stop_forum_monitor)

    config_response = client.patch(
        "/api/v1/system/config",
        headers=WORKSPACE_HEADERS,
        json={
            "values": {
                "REPORT_ENGINE_API_KEY": "sk-test-report",
                "REPORT_ENGINE_BASE_URL": "https://example.test/v1",
                "REPORT_ENGINE_MODEL_NAME": "test-report-model",
            }
        },
    )
    assert config_response.status_code == 200

    create_response = client.post(
        "/api/v1/report-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "topic": "COTY香水舆情分析",
            "outputFormats": ["html"],
        },
    )
    assert create_response.status_code == 202
    created_task = create_response.json()["task"]
    task_id = created_task["id"]
    task_workspace_id = created_task["workspaceId"]

    task_service: TaskService = client.app.state.task_service
    task_service._run_report_worker(WORKSPACE_HEADERS["X-Workspace-Id"], task_id)

    completed = task_service.get_report_task(WORKSPACE_HEADERS["X-Workspace-Id"], task_id)
    assert completed["status"] == "succeeded"
    assert captured["query"] == "COTY香水舆情分析"
    assert captured["reports"] == [
        "query report for COTY香水舆情分析",
        "media report for COTY香水舆情分析",
        "insight report for COTY香水舆情分析",
    ]
    assert "[HOST]" in captured["forum_logs"]
    assert captured["call_order"]
    assert all(item.endswith("_after_True") for item in captured["call_order"])
    assert task_id in captured["report_config"].OUTPUT_DIR
    assert task_workspace_id in captured["report_config"].OUTPUT_DIR
    assert captured["report_config"].LOG_FILE.endswith("/report/report_engine.log")
    assert captured["report_config"].REPORT_TASK_ID == task_id

    orchestration = completed["sourceScope"]["orchestration"]
    assert orchestration["status"] == "succeeded"
    assert task_id in orchestration["workspacePath"]
    assert task_workspace_id in orchestration["workspacePath"]
    for engine_id in ("query", "media", "insight"):
        assert orchestration["engines"][engine_id]["status"] == "succeeded"
        assert Path(orchestration["engines"][engine_id]["artifactPath"]).exists()

    logs = client.get(f"/api/v1/report-tasks/{task_id}/logs", headers=WORKSPACE_HEADERS)
    assert logs.status_code == 200
    stages = [
        event["payload"]["payload"].get("stage")
        for event in logs.json()["events"]
        if event["type"] == "orchestration"
    ]
    assert "workspace_ready" in stages
    assert "forum_monitor_started" in stages
    assert "forum_monitor_stopped" in stages


def test_report_rerun_reuses_unselected_historical_engine_reports(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, Any] = {}

    class FakeReportAgent:
        def __init__(self, config: Any = None):
            captured["report_config"] = config

        def generate_report(
            self,
            *,
            query: str,
            reports: list[str],
            forum_logs: str,
            custom_template: str,
            save_report: bool,
            stream_handler: Any,
        ) -> dict[str, Any]:
            del query, forum_logs, custom_template, save_report, stream_handler
            captured["reports"] = reports
            return {"html_content": "<!doctype html><h1>重跑报告</h1>"}

    monkeypatch.setitem(
        sys.modules,
        "ReportEngine.agent",
        types.SimpleNamespace(
            ReportAgent=FakeReportAgent,
            create_agent=lambda *args, **kwargs: FakeReportAgent(*args, **kwargs),
        ),
    )

    def fake_pre_engine(
        self: TaskService,
        engine_id: str,
        topic: str,
        task_workspace: Path,
        base_settings: Any,
        task_id: str,
    ) -> dict[str, Any]:
        del self, topic, base_settings, task_id
        artifact_path = task_workspace / engine_id / f"{engine_id}_report.md"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        report = f"{engine_id} rerun report"
        artifact_path.write_text(report, encoding="utf-8")
        return {
            "engine": engine_id,
            "status": "succeeded",
            "report": report,
            "artifactPath": str(artifact_path),
            "startedAt": "2026-05-25T04:30:00Z",
            "completedAt": "2026-05-25T04:31:00Z",
        }

    monkeypatch.setattr(TaskService, "_run_pre_report_engine", fake_pre_engine)
    monkeypatch.setattr(
        TaskService,
        "_start_report_forum_monitor",
        lambda self, workspace_id, task_id, task_workspace: types.SimpleNamespace(
            forum_log_file=task_workspace / "forum" / "forum.log"
        ),
    )
    monkeypatch.setattr(TaskService, "_stop_report_forum_monitor", lambda *args: "")

    create_response = client.post(
        "/api/v1/report-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "topic": "历史报告重跑",
            "outputFormats": ["html"],
        },
    )
    assert create_response.status_code == 202
    task = create_response.json()["task"]
    task_id = task["id"]
    task_service: TaskService = client.app.state.task_service
    task_service._complete_report_task(WORKSPACE_HEADERS["X-Workspace-Id"], task_id, task["artifacts"])
    task_workspace = task_service._report_task_workspace(task["workspaceId"], task_id)
    for engine_id, report in {
        "media": "media historical report",
        "insight": "insight historical report",
    }.items():
        path = task_workspace / engine_id / f"{engine_id}_report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8")

    rerun_response = client.post(
        f"/api/v1/report-tasks/{task_id}:rerun",
        headers=WORKSPACE_HEADERS,
        json={"engines": ["query"]},
    )
    assert rerun_response.status_code == 202
    assert rerun_response.json()["task"]["status"] == "queued"
    assert rerun_response.json()["task"]["sourceScope"]["orchestration"]["engines"] == ["query"]

    task_service._run_report_worker(WORKSPACE_HEADERS["X-Workspace-Id"], task_id)

    completed = task_service.get_report_task(WORKSPACE_HEADERS["X-Workspace-Id"], task_id)
    assert completed["status"] == "succeeded"
    assert captured["reports"] == [
        "query rerun report",
        "media historical report",
        "insight historical report",
    ]
    orchestration = completed["sourceScope"]["orchestration"]
    assert orchestration["rerunEngines"] == ["query"]
    assert orchestration["historyEngines"] == ["media", "insight"]
    assert orchestration["engines"]["query"]["status"] == "succeeded"
    assert orchestration["engines"]["media"]["status"] == "reused"
    assert orchestration["engines"]["insight"]["status"] == "reused"


def test_report_orchestration_passes_insight_mode_to_engine_config(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, Any] = {}

    def fake_pre_engine(
        self: TaskService,
        engine_id: str,
        topic: str,
        task_workspace: Path,
        base_settings: Any,
        task_id: str,
    ) -> dict[str, Any]:
        del self, topic, task_id
        captured["mode"] = base_settings.INSIGHT_MODE
        artifact_path = task_workspace / engine_id / f"{engine_id}_report.md"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        report = "insight mode report"
        artifact_path.write_text(report, encoding="utf-8")
        return {
            "engine": engine_id,
            "status": "succeeded",
            "report": report,
            "artifactPath": str(artifact_path),
            "startedAt": "2026-05-25T04:30:00Z",
            "completedAt": "2026-05-25T04:31:00Z",
        }

    monkeypatch.setattr(TaskService, "_run_pre_report_engine", fake_pre_engine)
    monkeypatch.setattr(
        TaskService,
        "_start_report_forum_monitor",
        lambda self, workspace_id, task_id, task_workspace: types.SimpleNamespace(
            forum_log_file=task_workspace / "forum" / "forum.log"
        ),
    )
    monkeypatch.setattr(TaskService, "_stop_report_forum_monitor", lambda *args: "")

    create_response = client.post(
        "/api/v1/report-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "topic": "Insight mode config",
            "sourceScope": {
                "orchestration": {
                    "enabled": True,
                    "engines": ["insight"],
                    "insightMode": "deep",
                }
            },
            "outputFormats": ["html"],
        },
    )
    assert create_response.status_code == 202
    task = create_response.json()["task"]
    task_service: TaskService = client.app.state.task_service
    task_workspace = task_service._report_task_workspace(task["workspaceId"], task["id"])

    reports, forum_logs = task_service._run_report_orchestration(
        WORKSPACE_HEADERS["X-Workspace-Id"],
        task,
        task_workspace,
        types.SimpleNamespace(INSIGHT_MODE="normal"),
    )

    assert captured["mode"] == "deep"
    assert reports == ["insight mode report"]
    assert forum_logs == ""
    completed = task_service.get_report_task(WORKSPACE_HEADERS["X-Workspace-Id"], task["id"])
    assert completed["sourceScope"]["orchestration"]["insightMode"] == "deep"


def test_report_orchestration_ignores_insight_mode_when_insight_engine_is_not_selected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, Any] = {"modes": {}}

    def fake_pre_engine(
        self: TaskService,
        engine_id: str,
        topic: str,
        task_workspace: Path,
        base_settings: Any,
        task_id: str,
    ) -> dict[str, Any]:
        del self, topic, task_id
        captured.setdefault("engines", []).append(engine_id)
        captured["modes"][engine_id] = getattr(base_settings, "INSIGHT_MODE", None)
        artifact_path = task_workspace / engine_id / f"{engine_id}_report.md"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        report = f"{engine_id} report"
        artifact_path.write_text(report, encoding="utf-8")
        return {
            "engine": engine_id,
            "status": "succeeded",
            "report": report,
            "artifactPath": str(artifact_path),
            "startedAt": "2026-05-25T04:30:00Z",
            "completedAt": "2026-05-25T04:31:00Z",
        }

    monkeypatch.setattr(TaskService, "_run_pre_report_engine", fake_pre_engine)
    monkeypatch.setattr(
        TaskService,
        "_start_report_forum_monitor",
        lambda self, workspace_id, task_id, task_workspace: types.SimpleNamespace(
            forum_log_file=task_workspace / "forum" / "forum.log"
        ),
    )
    monkeypatch.setattr(TaskService, "_stop_report_forum_monitor", lambda *args: "")

    create_response = client.post(
        "/api/v1/report-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "topic": "Insight disabled mode isolation",
            "sourceScope": {
                "orchestration": {
                    "enabled": True,
                    "engines": ["query", "media"],
                    "insightMode": "deep",
                }
            },
            "outputFormats": ["html"],
        },
    )
    assert create_response.status_code == 202
    task = create_response.json()["task"]
    task_service: TaskService = client.app.state.task_service
    task_workspace = task_service._report_task_workspace(task["workspaceId"], task["id"])

    reports, forum_logs = task_service._run_report_orchestration(
        WORKSPACE_HEADERS["X-Workspace-Id"],
        task,
        task_workspace,
        types.SimpleNamespace(INSIGHT_MODE="normal"),
    )

    assert set(captured["engines"]) == {"query", "media"}
    assert "insight" not in captured["engines"]
    assert captured["modes"] == {"query": "normal", "media": "normal"}
    assert reports == ["query report", "media report"]
    assert forum_logs == ""

    completed = task_service.get_report_task(WORKSPACE_HEADERS["X-Workspace-Id"], task["id"])
    orchestration = completed["sourceScope"]["orchestration"]
    assert orchestration["insightMode"] == "deep"
    assert set(orchestration["engines"]) == {"query", "media"}
    assert orchestration["engines"]["query"]["status"] == "succeeded"
    assert orchestration["engines"]["media"]["status"] == "succeeded"


def test_crawler_task_stop_retry_and_conflict(client: TestClient):
    create_response = client.post(
        "/api/v1/crawler-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "targetDate": "2026-05-22",
            "platforms": ["wb", "xhs"],
            "keywords": ["养老服务", "医保支付"],
            "crawlDepth": 5,
        },
    )
    assert create_response.status_code == 202
    task = create_response.json()["task"]
    task_id = task["id"]
    assert task["runMode"] == "deep_sentiment"
    assert task["keywords"] == ["养老服务", "医保支付"]
    assert task["keywordSource"] == "manual"
    assert task["crawlDepth"] == 5
    assert task["startDate"] == "2026-05-22"
    assert task["endDate"] == "2026-05-22"
    assert task["schedule"]["mode"] == "manual"

    log_response = client.get(
        f"/api/v1/crawler-tasks/{task_id}/logs",
        headers=WORKSPACE_HEADERS,
    )
    assert log_response.status_code == 200
    log_body = log_response.json()
    assert log_body["taskType"] == "crawler"
    assert log_body["events"][0]["type"] == "status"
    assert log_body["events"][0]["taskId"] == task_id

    stop_response = client.post(
        f"/api/v1/crawler-tasks/{task_id}:stop",
        headers=WORKSPACE_HEADERS,
    )
    assert stop_response.status_code == 202
    assert stop_response.json()["task"]["status"] == "stopped"

    retry_response = client.post(
        f"/api/v1/crawler-tasks/{task_id}:retry",
        headers=WORKSPACE_HEADERS,
    )
    assert retry_response.status_code == 202
    assert retry_response.json()["task"]["status"] == "queued"

    conflict_response = client.post(
        f"/api/v1/crawler-tasks/{task_id}:retry",
        headers=WORKSPACE_HEADERS,
    )
    assert conflict_response.status_code == 409
    assert conflict_response.json()["error"]["code"] == "CONFLICT"


def test_crawler_task_delete_removes_task_and_logs(client: TestClient):
    create_response = client.post(
        "/api/v1/crawler-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "runMode": "deep_sentiment",
            "targetDate": "2026-05-22",
            "platforms": ["wb"],
            "keywords": ["养老服务"],
            "keywordSource": "manual",
        },
    )
    assert create_response.status_code == 202
    task_id = create_response.json()["task"]["id"]

    delete_response = client.delete(
        f"/api/v1/crawler-tasks/{task_id}",
        headers=WORKSPACE_HEADERS,
    )
    assert delete_response.status_code == 204
    assert client.get(f"/api/v1/crawler-tasks/{task_id}", headers=WORKSPACE_HEADERS).status_code == 404
    assert client.get(f"/api/v1/crawler-tasks/{task_id}/logs", headers=WORKSPACE_HEADERS).status_code == 404


def test_crawler_task_date_range_and_schedule(client: TestClient):
    create_response = client.post(
        "/api/v1/crawler-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "runMode": "deep_sentiment",
            "startDate": "2026-05-20",
            "endDate": "2026-05-25",
            "schedule": {"mode": "daily", "timezone": "Asia/Shanghai"},
            "platforms": ["wb"],
            "keywords": ["养老服务"],
            "keywordSource": "manual",
        },
    )
    assert create_response.status_code == 202
    task = create_response.json()["task"]
    assert task["status"] == "pending"
    assert task["targetDate"] == "2026-05-20"
    assert task["startDate"] == "2026-05-20"
    assert task["endDate"] == "2026-05-25"
    assert task["schedule"]["mode"] == "daily"

    invalid_range = client.post(
        "/api/v1/crawler-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "runMode": "deep_sentiment",
            "startDate": "2026-05-25",
            "endDate": "2026-05-20",
            "platforms": ["wb"],
            "keywords": ["养老服务"],
            "keywordSource": "manual",
        },
    )
    assert invalid_range.status_code == 422
    assert invalid_range.json()["error"]["code"] == "VALIDATION_ERROR"

    invalid_cron = client.post(
        "/api/v1/crawler-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "runMode": "deep_sentiment",
            "startDate": "2026-05-20",
            "endDate": "2026-05-25",
            "schedule": {"mode": "cron", "timezone": "Asia/Shanghai"},
            "platforms": ["wb"],
            "keywords": ["养老服务"],
            "keywordSource": "manual",
        },
    )
    assert invalid_cron.status_code == 422
    assert invalid_cron.json()["error"]["code"] == "VALIDATION_ERROR"


def test_crawler_task_platform_filter_applies_before_pagination(client: TestClient):
    xhs_task = _create_crawler_task(client, ["xhs"], run_mode="topic_extraction")
    wb_task = _create_crawler_task(client, ["wb"], run_mode="deep_sentiment")

    response = client.get(
        "/api/v1/crawler-tasks?platform=xhs&pageSize=1",
        headers=WORKSPACE_HEADERS,
    )
    assert response.status_code == 200
    assert [task["id"] for task in response.json()["tasks"]] == [xhs_task["id"]]
    assert response.json()["tasks"][0]["id"] != wb_task["id"]


def test_crawler_task_keywords_are_required_and_normalized(client: TestClient):
    response = client.post(
        "/api/v1/crawler-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "runMode": "deep_sentiment",
            "platforms": ["wb", "wb", "xhs"],
            "keywords": ["  养老服务  ", "", "医保支付", "养老服务"],
            "keywordSource": "manual",
            "maxNotesPerKeyword": 1,
            "maxCommentsPerNote": 0,
            "loginType": "cookie",
            "headless": False,
        },
    )
    assert response.status_code == 202
    task = response.json()["task"]
    assert task["platforms"] == ["wb", "xhs"]
    assert task["keywords"] == ["养老服务", "医保支付"]
    assert task["maxNotesPerKeyword"] == 1
    assert task["maxCommentsPerNote"] == 0
    assert task["loginType"] == "cookie"
    assert task["headless"] is False
    assert task["stats"]["totalKeywords"] == 2
    assert task["stats"]["totalTasks"] == 4

    invalid = client.post(
        "/api/v1/crawler-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "runMode": "deep_sentiment",
            "platforms": ["wb"],
            "keywords": [],
            "keywordSource": "manual",
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"

    out_of_bounds = client.post(
        "/api/v1/crawler-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "runMode": "deep_sentiment",
            "platforms": ["wb"],
            "keywords": ["养老服务"],
            "keywordSource": "manual",
            "maxCommentsPerNote": -1,
        },
    )
    assert out_of_bounds.status_code == 422
    assert out_of_bounds.json()["error"]["code"] == "VALIDATION_ERROR"


def test_crawler_task_status_filter_applies_before_pagination(client: TestClient):
    stopped_task = _create_crawler_task(client, ["xhs"], run_mode="topic_extraction")
    stop_response = client.post(
        f"/api/v1/crawler-tasks/{stopped_task['id']}:stop",
        headers=WORKSPACE_HEADERS,
    )
    assert stop_response.status_code == 202
    queued_task = _create_crawler_task(client, ["wb"], run_mode="deep_sentiment")

    response = client.get(
        "/api/v1/crawler-tasks?status=stopped&pageSize=1",
        headers=WORKSPACE_HEADERS,
    )
    assert response.status_code == 200
    assert [task["id"] for task in response.json()["tasks"]] == [stopped_task["id"]]
    assert response.json()["tasks"][0]["id"] != queued_task["id"]


def test_contract_error_response_for_missing_task(client: TestClient):
    response = client.get("/api/v1/report-tasks/missing", headers=WORKSPACE_HEADERS)
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"


def test_real_crawler_worker_marks_failed_stats_as_failed(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_real_crawler(_: TaskService, task: dict[str, Any]) -> dict[str, Any]:
        return {
            "totalKeywords": len(task["keywords"]),
            "totalPlatforms": len(task["platforms"]),
            "totalTasks": 1,
            "successfulTasks": 0,
            "failedTasks": 1,
            "totalNotes": 0,
            "totalComments": 0,
            "platformSummary": {
                "wb": {
                    "successfulKeywords": 0,
                    "failedKeywords": 1,
                    "totalNotes": 0,
                    "totalComments": 0,
                }
            },
        }

    monkeypatch.setattr(TaskService, "_run_real_crawler", fake_real_crawler)
    task_service: TaskService = client.app.state.task_service
    task = _create_crawler_task(client, ["wb"], run_mode="deep_sentiment")

    task_service._run_crawler_worker(WORKSPACE_HEADERS["X-Workspace-Id"], task["id"])
    completed = task_service.get_crawler_task(WORKSPACE_HEADERS["X-Workspace-Id"], task["id"])

    assert completed["status"] == "failed"
    assert completed["stats"]["failedTasks"] == 1
    assert completed["stats"]["totalNotes"] == 0
    assert completed["error"]["error"]["code"] == "CRAWLER_ADAPTER_FAILED"


def test_real_crawler_uses_cookie_login_when_active_account_exists(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[dict[str, Any]] = []

    class FakePlatformCrawler:
        def __init__(self, log_callback=None):
            self.log_callback = log_callback

        def run_multi_platform_crawl_by_keywords(
            self,
            keywords: list[str],
            platforms: list[str],
            *,
            login_type: str,
            crawl_depth: int,
            max_notes_per_keyword: int,
            headless: bool,
            start_date: str | None,
            end_date: str | None,
        ) -> dict[str, Any]:
            calls.append(
                {
                    "keywords": keywords,
                    "platforms": platforms,
                    "loginType": login_type,
                    "crawlDepth": crawl_depth,
                    "maxNotesPerKeyword": max_notes_per_keyword,
                    "headless": headless,
                    "startDate": start_date,
                    "endDate": end_date,
                }
            )
            return {
                "total_keywords": len(keywords),
                "total_platforms": len(platforms),
                "total_tasks": len(keywords) * len(platforms),
                "successful_tasks": len(keywords) * len(platforms),
                "failed_tasks": 0,
                "total_notes": 3,
                "total_comments": 2,
                "platform_summary": {
                    "wb": {
                        "successful_keywords": len(keywords),
                        "failed_keywords": 0,
                        "total_notes": 3,
                        "total_comments": 2,
                    }
                },
            }

    fake_module = types.ModuleType("MindSpider.DeepSentimentCrawling.platform_crawler")
    fake_module.PlatformCrawler = FakePlatformCrawler
    monkeypatch.setitem(sys.modules, "MindSpider.DeepSentimentCrawling.platform_crawler", fake_module)

    upsert = client.put(
        "/api/v1/crawler-accounts/wb_active_cookie",
        headers=WORKSPACE_HEADERS,
        json={"platformId": "wb", "displayName": "微博采集号", "status": "active"},
    )
    assert upsert.status_code == 200

    task_service: TaskService = client.app.state.task_service
    task = _create_crawler_task(client, ["wb"], run_mode="deep_sentiment")
    stats = task_service._run_real_crawler(task)

    assert calls[0]["loginType"] == "cookie"
    assert calls[0]["crawlDepth"] == task["crawlDepth"]
    assert calls[0]["headless"] is True
    assert calls[0]["startDate"] == task["startDate"]
    assert calls[0]["endDate"] == task["endDate"]
    assert stats["totalNotes"] == 3


def test_real_crawler_streams_adapter_logs_to_task_events(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    class FakePlatformCrawler:
        def __init__(self, log_callback=None):
            self.log_callback = log_callback

        def run_multi_platform_crawl_by_keywords(
            self,
            keywords: list[str],
            platforms: list[str],
            *,
            login_type: str,
            crawl_depth: int,
            max_notes_per_keyword: int,
            headless: bool,
            start_date: str | None,
            end_date: str | None,
        ) -> dict[str, Any]:
            del login_type, crawl_depth, max_notes_per_keyword, headless, start_date, end_date
            assert self.log_callback is not None
            self.log_callback("stdout", "MediaCrawler INFO started")
            self.log_callback("stderr", "MediaCrawler ERROR sample")
            return {
                "total_keywords": len(keywords),
                "total_platforms": len(platforms),
                "total_tasks": len(keywords) * len(platforms),
                "successful_tasks": len(keywords) * len(platforms),
                "failed_tasks": 0,
                "total_notes": 1,
                "total_comments": 0,
                "platform_summary": {
                    platform: {
                        "successful_keywords": len(keywords),
                        "failed_keywords": 0,
                        "total_notes": 1,
                        "total_comments": 0,
                    }
                    for platform in platforms
                },
            }

    fake_module = types.ModuleType("MindSpider.DeepSentimentCrawling.platform_crawler")
    fake_module.PlatformCrawler = FakePlatformCrawler
    monkeypatch.setitem(sys.modules, "MindSpider.DeepSentimentCrawling.platform_crawler", fake_module)

    upsert = client.put(
        "/api/v1/crawler-accounts/wb_streaming_cookie",
        headers=WORKSPACE_HEADERS,
        json={"platformId": "wb", "displayName": "微博采集号", "status": "active"},
    )
    assert upsert.status_code == 200

    task_service: TaskService = client.app.state.task_service
    task = _create_crawler_task(client, ["wb"], run_mode="deep_sentiment")
    task_service._run_real_crawler(task)
    events = task_service.list_events(WORKSPACE_HEADERS["X-Workspace-Id"], task["id"])
    log_events = [event for event in events if event["type"] == "log"]

    assert [event["payload"]["line"] for event in log_events] == [
        "MediaCrawler INFO started",
        "MediaCrawler ERROR sample",
    ]
    assert log_events[0]["payload"]["level"] == "info"
    assert log_events[1]["payload"]["level"] == "error"


def test_real_crawler_requires_active_account_for_workspace_task(client: TestClient):
    task_service: TaskService = client.app.state.task_service
    task = _create_crawler_task(client, ["zhihu"], run_mode="deep_sentiment")

    with pytest.raises(RuntimeError, match="No active crawler account"):
        task_service._run_real_crawler(task)


def test_real_crawler_ignores_active_account_with_only_visitor_state(client: TestClient):
    response = client.put(
        "/api/v1/crawler-accounts/xhs_visitor_only",
        headers=WORKSPACE_HEADERS,
        json={
            "platformId": "xhs",
            "displayName": "误登记小红书账号",
            "status": "active",
            "details": {"stateNames": ["a1", "webId"]},
        },
    )
    assert response.status_code == 200

    task_service: TaskService = client.app.state.task_service
    task = _create_crawler_task(client, ["xhs"], run_mode="deep_sentiment")

    with pytest.raises(RuntimeError, match="No active crawler account"):
        task_service._crawler_login_type(task)


def test_task_service_repairs_interrupted_running_crawler_task(client: TestClient):
    task_service: TaskService = client.app.state.task_service
    task = _create_crawler_task(client, ["wb"], run_mode="deep_sentiment")
    client.app.state.store.execute(
        """
        UPDATE crawler_tasks
        SET status = 'running', progress = 10
        WHERE workspace_id = ? AND id = ?
        """,
        (WORKSPACE_HEADERS["X-Workspace-Id"], task["id"]),
    )

    task_service._repair_interrupted_crawler_tasks()
    repaired = task_service.get_crawler_task(WORKSPACE_HEADERS["X-Workspace-Id"], task["id"])
    events = task_service.list_events(WORKSPACE_HEADERS["X-Workspace-Id"], task["id"])

    assert repaired["status"] == "failed"
    assert repaired["error"]["error"]["code"] == "CRAWLER_WORKER_INTERRUPTED"
    assert events[-1]["type"] == "failed"
    assert events[-1]["payload"]["task"]["error"]["error"]["code"] == "CRAWLER_WORKER_INTERRUPTED"


def test_crawler_task_repairs_legacy_success_with_failed_stats(client: TestClient):
    task_service: TaskService = client.app.state.task_service
    task = _create_crawler_task(client, ["wb"], run_mode="deep_sentiment")
    failed_stats = {
        "totalKeywords": 1,
        "totalPlatforms": 1,
        "totalTasks": 1,
        "successfulTasks": 0,
        "failedTasks": 1,
        "totalNotes": 0,
        "totalComments": 0,
        "platformSummary": {
            "wb": {
                "successfulKeywords": 0,
                "failedKeywords": 1,
                "totalNotes": 0,
                "totalComments": 0,
            }
        },
    }
    client.app.state.store.execute(
        """
        UPDATE crawler_tasks
        SET status = 'succeeded', progress = 100, stats_json = ?, error_json = NULL
        WHERE workspace_id = ? AND id = ?
        """,
        (json.dumps(failed_stats), WORKSPACE_HEADERS["X-Workspace-Id"], task["id"]),
    )

    task_service._repair_inconsistent_crawler_task_statuses()
    response = client.get(
        "/api/v1/crawler-tasks?status=failed",
        headers=WORKSPACE_HEADERS,
    )

    assert response.status_code == 200
    repaired = [item for item in response.json()["tasks"] if item["id"] == task["id"]]
    assert repaired[0]["status"] == "failed"
    assert repaired[0]["stats"]["failedTasks"] == 1
    assert repaired[0]["error"]["error"]["code"] == "CRAWLER_ADAPTER_FAILED"


def _config_fields(client: TestClient) -> dict[str, dict]:
    response = client.get("/api/v1/system/config", headers=WORKSPACE_HEADERS)
    assert response.status_code == 200
    return {field["key"]: field for field in response.json()["fields"]}


def _strategy_policy(platform_id: str) -> dict:
    return {
        "platformId": platform_id,
        "enabled": True,
        "crawlDepth": 3,
        "maxKeywords": 10,
        "maxNotesPerKeyword": 20,
        "maxCommentsPerNote": 50,
        "keywords": ["养老服务", "医保"],
        "keywordSource": "manual",
        "frequency": {"mode": "daily", "timezone": "Asia/Shanghai"},
        "loginType": "qrcode",
        "headless": True,
    }


def _create_crawler_task(
    client: TestClient,
    platforms: list[str],
    *,
    run_mode: str,
) -> dict:
    response = client.post(
        "/api/v1/crawler-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "runMode": run_mode,
            "targetDate": "2026-05-22",
            "platforms": platforms,
            "keywords": ["养老服务", "医保支付"],
            "keywordSource": "manual",
        },
    )
    assert response.status_code == 202
    return response.json()["task"]


def _wait_for_login_session(client: TestClient, session_id: str, timeout_seconds: float = 1.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_session = None
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/v1/crawler-accounts/login-sessions/{session_id}",
            headers=WORKSPACE_HEADERS,
        )
        assert response.status_code == 200
        last_session = response.json()["session"]
        if last_session["status"] == "completed":
            return last_session
        time.sleep(0.02)
    raise AssertionError(f"Timed out waiting for login session; last_session={last_session}")
