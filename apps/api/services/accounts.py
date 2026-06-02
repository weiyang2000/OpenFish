"""Crawler account persistence and sensitive-field filtering."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from apps.api.schemas import (
    CRAWLER_ACCOUNT_STATUSES,
    ApiError,
    CreateCrawlerAccountLoginSessionRequest,
    CrawlerAccountUpsertRequest,
    PLATFORM_IDS,
)
from apps.api.services.common import new_id, utc_now
from apps.api.storage import Store, dumps, loads


SENSITIVE_DETAIL_KEYS = {
    "auth",
    "authorization",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "password",
    "refresh_token",
    "secret",
    "session",
    "token",
    "access_token",
}

PLATFORM_LOGIN_URLS = {
    "xhs": "https://www.xiaohongshu.com/explore",
    "dy": "https://www.douyin.com",
    "ks": "https://www.kuaishou.com",
    "bili": "https://www.bilibili.com",
    "wb": "https://passport.weibo.com/sso/signin?entry=miniblog&source=miniblog",
    "tieba": "https://tieba.baidu.com",
    "zhihu": "https://www.zhihu.com",
}

PLATFORM_LOGIN_MARKERS = {
    "xhs": ("web_session",),
    "dy": ("LOGIN_STATUS", "sessionid"),
    "ks": ("passToken",),
    "bili": ("SESSDATA", "DedeUserID"),
    "wb": ("SUB", "SUBP", "SSOLoginState", "WBPSESS"),
    "tieba": ("STOKEN", "PTOKEN", "BDUSS"),
    "zhihu": ("z_c0",),
}

PLATFORM_CHANGED_LOGIN_MARKERS = {
    "xhs": ("web_session",),
    "wb": ("WBPSESS",),
}

PLATFORM_DISPLAY_NAMES = {
    "xhs": "小红书",
    "dy": "抖音",
    "ks": "快手",
    "bili": "Bilibili",
    "wb": "微博",
    "tieba": "贴吧",
    "zhihu": "知乎",
}

LOGIN_TRIGGER_SELECTORS = {
    "bili": ("xpath=//div[@class='right-entry__outside go-login-btn']//div",),
    "dy": ("xpath=//p[text() = '登录']",),
    "ks": ("xpath=//p[text()='登录']",),
    "tieba": ("xpath=//li[@class='u_login']",),
    "xhs": ("xpath=//*[@id='app']/div[1]/div[2]/div[1]/ul/div[1]/button",),
}

LOGIN_QR_SELECTORS = {
    "xhs": ("xpath=//img[@class='qrcode-img']",),
    "dy": ("xpath=//div[@id='animate_qrcode_container']//img",),
    "ks": ("xpath=//div[@class='qrcode-img']//img",),
    "bili": ("xpath=//div[@class='login-scan-box']//img",),
    "wb": ("xpath=//img[@class='w-full h-full']",),
    "tieba": ("xpath=//img[@class='tang-pass-qrcode-img']",),
    "zhihu": ("canvas.Qrcode-qrcode",),
}

GENERIC_QR_SELECTORS = (
    "img[alt*='二维码']",
    "img[src*='qrcode']",
    "img[src*='qr']",
    "[class*='qrcode'] img",
    "[class*='qrcode'] canvas",
    "[class*='qr-code'] img",
    "[class*='qr-code'] canvas",
    "[class*='qr'] img",
    "[class*='qr'] canvas",
    "canvas",
)


class AccountService:
    def __init__(self, store: Store, repo_root: str | Path | None = None):
        self.store = store
        self.repo_root = Path(repo_root or Path.cwd())
        self._login_sessions: dict[str, dict[str, Any]] = {}
        self._login_lock = threading.Lock()
        self._profile_locks: dict[str, threading.Lock] = {}

    def list_accounts(
        self,
        workspace_id: str,
        platform_id: str | None = None,
        status: str | None = None,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        if platform_id:
            self._ensure_platform(platform_id)
        if status and status not in CRAWLER_ACCOUNT_STATUSES:
            raise ApiError(
                "VALIDATION_ERROR",
                "Unsupported crawler account status",
                status_code=400,
            )

        filters = ["workspace_id = ?"]
        params: list[Any] = [workspace_id]
        if platform_id:
            filters.append("platform_id = ?")
            params.append(platform_id)
        if status:
            filters.append("status = ?")
            params.append(status)
        params.append(page_size)

        rows = self.store.query_all(
            f"""
            SELECT *
            FROM crawler_accounts
            WHERE {' AND '.join(filters)}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            params,
        )
        return [self._account_row(row) for row in rows]

    def upsert_account(
        self,
        workspace_id: str,
        account_id: str,
        payload: CrawlerAccountUpsertRequest,
    ) -> dict[str, Any]:
        self._ensure_platform(payload.platformId)

        details = self.sanitize_details(payload.details)
        error = self.sanitize_details(payload.error) if payload.error else None
        now = utc_now()
        existing = self.store.query_one(
            """
            SELECT id, created_at
            FROM crawler_accounts
            WHERE workspace_id = ? AND platform_id = ? AND account_id = ?
            """,
            (workspace_id, payload.platformId, account_id),
        )
        row_id = existing["id"] if existing else new_id("account")
        created_at = existing["created_at"] if existing else now
        last_checked_at = payload.lastCheckedAt or now

        self.store.execute(
            """
            INSERT INTO crawler_accounts (
                id, workspace_id, platform_id, account_id, username, display_name,
                avatar_url, profile_url, status, login_type, last_login_at,
                last_checked_at, details_json, error_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workspace_id, platform_id, account_id) DO UPDATE SET
                username = excluded.username,
                display_name = excluded.display_name,
                avatar_url = excluded.avatar_url,
                profile_url = excluded.profile_url,
                status = excluded.status,
                login_type = excluded.login_type,
                last_login_at = excluded.last_login_at,
                last_checked_at = excluded.last_checked_at,
                details_json = excluded.details_json,
                error_json = excluded.error_json,
                updated_at = excluded.updated_at
            """,
            (
                row_id,
                workspace_id,
                payload.platformId,
                account_id,
                payload.username,
                payload.displayName,
                payload.avatarUrl,
                payload.profileUrl,
                payload.status,
                payload.loginType,
                payload.lastLoginAt,
                last_checked_at,
                dumps(details),
                dumps(error) if error else None,
                created_at,
                now,
            ),
        )
        row = self.store.query_one(
            """
            SELECT *
            FROM crawler_accounts
            WHERE workspace_id = ? AND platform_id = ? AND account_id = ?
            """,
            (workspace_id, payload.platformId, account_id),
        )
        return self._account_row(row)

    def delete_account(self, workspace_id: str, account_id: str) -> None:
        row = self.store.query_one(
            """
            SELECT id
            FROM crawler_accounts
            WHERE workspace_id = ? AND id = ?
            """,
            (workspace_id, account_id),
        )
        if not row:
            rows = self.store.query_all(
                """
                SELECT id
                FROM crawler_accounts
                WHERE workspace_id = ? AND account_id = ?
                ORDER BY updated_at DESC
                LIMIT 2
                """,
                (workspace_id, account_id),
            )
            if not rows:
                raise ApiError("NOT_FOUND", "Crawler account not found", status_code=404)
            if len(rows) > 1:
                raise ApiError(
                    "CONFLICT",
                    "Multiple crawler accounts match this accountId. Delete by the returned account id instead.",
                    status_code=409,
                )
            row = rows[0]

        self.store.execute(
            "DELETE FROM crawler_accounts WHERE workspace_id = ? AND id = ?",
            (workspace_id, row["id"]),
        )

    def account_counts(self, workspace_id: str, platform_id: str) -> dict[str, int]:
        self._ensure_platform(platform_id)
        rows = self.store.query_all(
            """
            SELECT status, COUNT(*) AS count
            FROM crawler_accounts
            WHERE workspace_id = ? AND platform_id = ?
            GROUP BY status
            """,
            (workspace_id, platform_id),
        )
        counts = {
            "active": 0,
            "loginRequired": 0,
            "expired": 0,
            "disabled": 0,
            "error": 0,
            "unknown": 0,
        }
        for row in rows:
            key = "loginRequired" if row["status"] == "login_required" else row["status"]
            counts[key] = row["count"]
        return counts

    def create_login_session(
        self,
        workspace_id: str,
        payload: CreateCrawlerAccountLoginSessionRequest,
    ) -> dict[str, Any]:
        self._ensure_platform(payload.platformId)
        session_id = new_id("login")
        now = utc_now()
        profile_dir = self._profile_dir(payload.platformId)
        session = {
            "id": session_id,
            "workspaceId": workspace_id,
            "platformId": payload.platformId,
            "loginType": payload.loginType,
            "status": "opening",
            "loginUrl": PLATFORM_LOGIN_URLS[payload.platformId],
            "profileDir": str(profile_dir),
            "createdAt": now,
            "updatedAt": now,
            "expiresAt": self._future_iso(payload.timeoutSeconds),
            "message": "正在打开登录页面",
        }
        with self._login_lock:
            self._replace_active_login_sessions_locked(payload.platformId, now)
            self._login_sessions[session_id] = session

        threading.Thread(
            target=self._run_login_session,
            args=(session_id, workspace_id, payload),
            daemon=True,
        ).start()
        return self._public_login_session(session)

    def get_login_session(self, workspace_id: str, session_id: str) -> dict[str, Any]:
        with self._login_lock:
            session = self._login_sessions.get(session_id)
            if not session or session["workspaceId"] != workspace_id:
                raise ApiError("NOT_FOUND", "Crawler account login session not found", status_code=404)
            if session.get("status") in {"opening", "waiting"} and self._session_is_expired(session):
                self._expire_login_session_locked(session)
            public_session = self._public_login_session(session)
        return public_session

    def _run_login_session(
        self,
        session_id: str,
        workspace_id: str,
        payload: CreateCrawlerAccountLoginSessionRequest,
    ) -> None:
        profile_dir = self._profile_dir(payload.platformId)
        self._terminate_profile_browsers(profile_dir)

        profile_lock = self._profile_lock(payload.platformId)
        if not profile_lock.acquire(blocking=False):
            self._terminate_profile_browsers(profile_dir)
            if profile_lock.acquire(timeout=10):
                acquired = True
            else:
                acquired = False
        else:
            acquired = True

        if not acquired:
            self._update_login_session(
                session_id,
                status="failed",
                message="旧登录浏览器仍未释放 profile，系统已尝试停止旧进程，请稍后重试。",
                error={
                    "code": "LOGIN_PROFILE_BUSY",
                    "message": "旧登录浏览器仍未释放 profile，系统已尝试停止旧进程，请稍后重试。",
                },
            )
            return

        try:
            account = asyncio.run(self._capture_login(session_id, workspace_id, payload))
            self._update_login_session(
                session_id,
                status="completed",
                message="登录状态已保存",
                account=account,
                loginPreviewImage=None,
                loginPreviewKind=None,
            )
        except Exception as exc:
            code, message = self._login_error(exc)
            self._update_login_session(
                session_id,
                status="failed",
                message=message,
                error={"code": code, "message": message},
            )
        finally:
            if acquired:
                profile_lock.release()

    async def _capture_login(
        self,
        session_id: str,
        workspace_id: str,
        payload: CreateCrawlerAccountLoginSessionRequest,
    ) -> dict[str, Any]:
        try:
            from cloakbrowser import launch_persistent_context_async
        except ModuleNotFoundError as exc:
            raise RuntimeError("CloakBrowser is not installed. Run `uv sync` or install `cloakbrowser`.") from exc

        profile_dir = self._profile_dir(payload.platformId)
        profile_dir.mkdir(parents=True, exist_ok=True)
        login_url = PLATFORM_LOGIN_URLS[payload.platformId]
        context = await launch_persistent_context_async(
            str(profile_dir),
            headless=payload.headless,
            viewport={"width": 1440, "height": 960},
            accept_downloads=True,
            stealth_args=True,
        )
        try:
            page = await context.new_page()
            await page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)
            if payload.loginType == "qrcode":
                await self._prepare_qrcode_login_page(page, payload.platformId)
            self._update_login_session(
                session_id,
                status="waiting",
                message="正在生成扫码预览" if payload.loginType == "qrcode" else "等待用户完成登录",
            )
            if payload.loginType == "qrcode":
                await self._publish_login_preview(session_id, page, payload.platformId)

            baseline_state = self._cookie_dict(await context.cookies())
            deadline = time.monotonic() + payload.timeoutSeconds
            last_state_names: list[str] = []
            while time.monotonic() < deadline:
                state_items = await context.cookies()
                state = self._cookie_dict(state_items)
                state_names = sorted(state)
                if state_names != last_state_names:
                    last_state_names = state_names
                    self._update_login_session(
                        session_id,
                        observedStateNames=state_names[:30],
                        observedStateCount=len(state_names),
                    )
                if self._has_required_login_state(payload.platformId, state, baseline_state):
                    return self._persist_logged_in_account(
                        workspace_id,
                        payload.platformId,
                        payload.loginType,
                        session_id,
                        profile_dir,
                        state,
                    )
                await asyncio.sleep(2)
            raise TimeoutError("登录超时，未检测到平台登录态")
        finally:
            try:
                await context.close()
            except Exception:
                pass

    def _replace_active_login_sessions_locked(self, platform_id: str, now: str) -> None:
        for session in self._login_sessions.values():
            if session.get("platformId") != platform_id:
                continue
            if session.get("status") in {"opening", "waiting"}:
                if self._session_is_expired(session):
                    session.update(
                        status="failed",
                        message="登录会话已过期，请重新发起",
                        error={"code": "LOGIN_SESSION_EXPIRED", "message": "登录会话已过期，请重新发起"},
                        updatedAt=utc_now(),
                    )
                    continue
                session.update(
                    status="failed",
                    message="已被新的扫码登录请求替换",
                    error={"code": "LOGIN_SESSION_REPLACED", "message": "已被新的扫码登录请求替换"},
                    loginPreviewImage=None,
                    loginPreviewKind=None,
                    updatedAt=now,
                )

    @staticmethod
    def _session_is_expired(session: dict[str, Any]) -> bool:
        expires_at = session.get("expiresAt")
        if not isinstance(expires_at, str) or not expires_at:
            return False
        try:
            expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        return datetime.now(timezone.utc) >= expires

    @staticmethod
    def _expire_login_session_locked(session: dict[str, Any]) -> None:
        message = "登录会话已过期，请重新发起"
        session.update(
            status="failed",
            message=message,
            error={"code": "LOGIN_SESSION_EXPIRED", "message": message},
            loginPreviewImage=None,
            loginPreviewKind=None,
            updatedAt=utc_now(),
        )

    def _profile_lock(self, platform_id: str) -> threading.Lock:
        with self._login_lock:
            lock = self._profile_locks.get(platform_id)
            if not lock:
                lock = threading.Lock()
                self._profile_locks[platform_id] = lock
            return lock

    def _terminate_profile_browsers(self, profile_dir: Path) -> int:
        pids = self._profile_browser_pids(profile_dir)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            except OSError:
                continue

        deadline = time.monotonic() + 3
        while pids and time.monotonic() < deadline:
            remaining = self._profile_browser_pids(profile_dir)
            if not remaining:
                break
            time.sleep(0.1)

        for pid in self._profile_browser_pids(profile_dir):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
            except OSError:
                continue

        self._clear_profile_singleton_files(profile_dir)
        return len(pids)

    @staticmethod
    def _profile_browser_pids(profile_dir: Path) -> list[int]:
        profile_text = str(profile_dir)
        try:
            result = subprocess.run(
                ["ps", "-eo", "pid=,args="],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []

        if result.returncode != 0:
            return []

        current_pid = os.getpid()
        pids: list[int] = []
        for line in result.stdout.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) != 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            args = parts[1]
            normalized = args.lower()
            if pid == current_pid:
                continue
            if profile_text not in args:
                continue
            if "chrome" not in normalized and "chromium" not in normalized:
                continue
            pids.append(pid)
        return pids

    @staticmethod
    def _clear_profile_singleton_files(profile_dir: Path) -> None:
        for filename in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            path = profile_dir / filename
            try:
                if path.exists() or path.is_symlink():
                    path.unlink()
            except OSError:
                continue

    @staticmethod
    def _login_error(exc: Exception) -> tuple[str, str]:
        message = str(exc)
        normalized = message.lower()
        if "profile appears to be in use" in normalized or "chromium has locked the profile" in normalized:
            return (
                "LOGIN_PROFILE_BUSY",
                "该平台登录浏览器 profile 仍被 Chromium 占用，系统已尝试停止旧进程，请稍后重试。",
            )
        if "missing x server" in normalized or "without having a xserver" in normalized:
            return (
                "LOGIN_DISPLAY_UNAVAILABLE",
                "当前环境无法打开有界面浏览器；请使用无头扫码登录，或配置可用的 DISPLAY/XServer。",
            )
        return ("LOGIN_CAPTURE_FAILED", message)

    async def _prepare_qrcode_login_page(self, page: Any, platform_id: str) -> None:
        for selector in LOGIN_TRIGGER_SELECTORS.get(platform_id, ()):
            try:
                trigger = page.locator(selector).nth(0)
                await trigger.wait_for(state="visible", timeout=1_500)
                await trigger.click(timeout=1_500)
                await page.wait_for_timeout(800)
                return
            except Exception:
                continue

    async def _publish_login_preview(self, session_id: str, page: Any, platform_id: str) -> None:
        preview = await self._capture_login_preview(page, platform_id)
        if preview:
            message = (
                "请用手机扫码完成登录"
                if preview["kind"] == "qrcode"
                else "登录页已打开，暂未捕获到二维码预览"
            )
            self._update_login_session(
                session_id,
                message=message,
                loginPreviewImage=preview["image"],
                loginPreviewKind=preview["kind"],
                loginPreviewUpdatedAt=utc_now(),
            )
            return

        self._update_login_session(
            session_id,
            message="登录页已打开，暂未捕获到二维码预览",
        )

    async def _capture_login_preview(self, page: Any, platform_id: str) -> dict[str, str] | None:
        await page.wait_for_timeout(1_000)
        selectors = (*LOGIN_QR_SELECTORS.get(platform_id, ()), *GENERIC_QR_SELECTORS)
        for selector in selectors:
            try:
                locator = page.locator(selector).nth(0)
                await locator.wait_for(state="visible", timeout=800)
                image = await locator.screenshot(type="png", timeout=1_500)
                if image:
                    return {
                        "image": self._image_data_url(image, "image/png"),
                        "kind": "qrcode",
                    }
            except Exception:
                continue

        try:
            image = await page.screenshot(type="jpeg", quality=70, full_page=False, timeout=3_000)
            if image:
                return {
                    "image": self._image_data_url(image, "image/jpeg"),
                    "kind": "page",
                }
        except Exception:
            return None
        return None

    def _persist_logged_in_account(
        self,
        workspace_id: str,
        platform_id: str,
        login_type: str,
        session_id: str,
        profile_dir: Path,
        state: dict[str, str],
    ) -> dict[str, Any]:
        markers = [name for name in PLATFORM_LOGIN_MARKERS[platform_id] if state.get(name)]
        marker_seed = "|".join(state[name] for name in markers) or "|".join(state)
        digest = hashlib.sha256(f"{platform_id}:{marker_seed}".encode("utf-8")).hexdigest()
        account_id = f"{platform_id}_{digest[:10]}"
        payload = CrawlerAccountUpsertRequest(
            platformId=platform_id,
            displayName=f"{PLATFORM_DISPLAY_NAMES[platform_id]}账号 {digest[:6]}",
            status="active",
            loginType=login_type,
            lastLoginAt=utc_now(),
            lastCheckedAt=utc_now(),
            details={
                "message": "登录状态已保存，可用于后续采集。",
                "scopes": ["search", "detail"],
                "loginStateNames": markers,
                "stateNames": sorted(state.keys())[:30],
                "stateCount": len(state),
                "loginSessionId": session_id,
            },
        )
        return self.upsert_account(workspace_id, account_id, payload)

    def _profile_dir(self, platform_id: str) -> Path:
        configured = os.getenv("BETTAFISH_CRAWLER_BROWSER_DATA_DIR")
        base_dir = (
            Path(configured)
            if configured
            else self.repo_root / "MindSpider" / "DeepSentimentCrawling" / "MediaCrawler" / "browser_data"
        )
        return base_dir / f"cloak_{platform_id}_user_data_dir"

    @staticmethod
    def _cookie_dict(items: list[dict[str, Any]]) -> dict[str, str]:
        state = {}
        for item in items:
            name = str(item.get("name") or "")
            value = str(item.get("value") or "")
            if name and value:
                state[name] = value
        return state

    @staticmethod
    def _has_required_login_state(
        platform_id: str,
        state: dict[str, str],
        baseline_state: dict[str, str] | None = None,
    ) -> bool:
        markers = PLATFORM_LOGIN_MARKERS[platform_id]
        changed_markers = set(PLATFORM_CHANGED_LOGIN_MARKERS.get(platform_id, ()))
        baseline_state = baseline_state or {}
        for marker in markers:
            value = state.get(marker)
            if not value:
                continue
            if marker in changed_markers and baseline_state.get(marker) == value:
                continue
            return True
        return False

    @staticmethod
    def _image_data_url(content: bytes, mime_type: str) -> str:
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _update_login_session(self, session_id: str, **updates: Any) -> None:
        with self._login_lock:
            session = self._login_sessions.get(session_id)
            if not session:
                return
            if session.get("error", {}).get("code") == "LOGIN_SESSION_REPLACED":
                return
            session.update(updates)
            session["updatedAt"] = utc_now()

    @staticmethod
    def _public_login_session(session: dict[str, Any]) -> dict[str, Any]:
        hidden_keys = {"profileDir"}
        return {key: value for key, value in session.items() if key not in hidden_keys}

    @staticmethod
    def _future_iso(seconds: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")

    @staticmethod
    def sanitize_details(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}

        def sanitize(item: Any) -> Any:
            if isinstance(item, dict):
                cleaned = {}
                for key, nested in item.items():
                    key_text = str(key)
                    if AccountService._is_sensitive_key(key_text):
                        continue
                    cleaned[key_text] = sanitize(nested)
                return cleaned
            if isinstance(item, list):
                return [sanitize(nested) for nested in item]
            return item

        return sanitize(value)

    @staticmethod
    def _is_sensitive_key(key: str) -> bool:
        normalized = key.lower().replace("-", "_")
        return normalized in SENSITIVE_DETAIL_KEYS or any(
            marker in normalized
            for marker in (
                "auth",
                "cookie",
                "credential",
                "password",
                "secret",
                "session",
                "token",
            )
        )

    @staticmethod
    def _ensure_platform(platform_id: str) -> None:
        if platform_id not in PLATFORM_IDS:
            raise ApiError(
                "VALIDATION_ERROR",
                f"Unsupported platform: {platform_id}",
                status_code=400,
                details={"supported": list(PLATFORM_IDS)},
            )

    @staticmethod
    def _account_row(row: dict[str, Any] | None) -> dict[str, Any]:
        if not row:
            raise ApiError("NOT_FOUND", "Crawler account not found", status_code=404)
        account = {
            "id": row["id"],
            "workspaceId": row["workspace_id"],
            "platformId": row["platform_id"],
            "accountId": row["account_id"],
            "username": row["username"],
            "displayName": row["display_name"],
            "avatarUrl": row["avatar_url"],
            "profileUrl": row["profile_url"],
            "status": row["status"],
            "loginType": row["login_type"],
            "lastLoginAt": row["last_login_at"],
            "lastCheckedAt": row["last_checked_at"],
            "details": loads(row["details_json"], {}),
            "error": loads(row["error_json"], None),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        return {key: value for key, value in account.items() if value is not None}
