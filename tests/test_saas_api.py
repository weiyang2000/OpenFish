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


WORKSPACE_HEADERS = {"X-Workspace-Id": "workspace_test"}


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
            }
        },
    )
    assert response.status_code == 200

    fields = _config_fields(client)
    assert fields["REPORT_ENGINE_API_KEY"]["value"] == "********"
    assert fields["REPORT_ENGINE_API_KEY"]["sensitive"] is True
    assert fields["SEARCH_TOOL_TYPE"]["value"] == "BochaAPI"

    response = client.patch(
        "/api/v1/system/config",
        headers=WORKSPACE_HEADERS,
        json={"values": {"REPORT_ENGINE_API_KEY": "********"}},
    )
    assert response.status_code == 200
    assert _config_fields(client)["REPORT_ENGINE_API_KEY"]["value"] == "********"


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
            comment_count TEXT
        )
        """
    )
    store.execute(
        """
        INSERT INTO xhs_note (
            note_id, title, desc, nickname, note_url, source_keyword,
            time, add_ts, liked_count, comment_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    assert create_response.json()["eventStreamUrl"].endswith(f"{task['id']}/events")

    get_response = client.get(f"/api/v1/report-tasks/{task['id']}", headers=WORKSPACE_HEADERS)
    assert get_response.status_code == 200
    assert get_response.json()["task"]["topic"] == "养老服务发展趋势"

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


def test_crawler_task_stop_retry_and_conflict(client: TestClient):
    create_response = client.post(
        "/api/v1/crawler-tasks",
        headers=WORKSPACE_HEADERS,
        json={
            "runMode": "deep_sentiment",
            "targetDate": "2026-05-22",
            "platforms": ["wb", "xhs"],
            "keywords": ["养老服务", "医保支付"],
            "keywordSource": "manual",
        },
    )
    assert create_response.status_code == 202
    task = create_response.json()["task"]
    task_id = task["id"]
    assert task["keywords"] == ["养老服务", "医保支付"]
    assert task["keywordSource"] == "manual"

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
            max_notes_per_keyword: int,
            headless: bool,
        ) -> dict[str, Any]:
            calls.append(
                {
                    "keywords": keywords,
                    "platforms": platforms,
                    "loginType": login_type,
                    "maxNotesPerKeyword": max_notes_per_keyword,
                    "headless": headless,
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
    assert calls[0]["headless"] is True
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
            max_notes_per_keyword: int,
            headless: bool,
        ) -> dict[str, Any]:
            del login_type, max_notes_per_keyword, headless
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
