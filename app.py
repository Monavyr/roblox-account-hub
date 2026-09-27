from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, urlencode, urlparse

import aiohttp
from aiohttp import web


APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
APP_VERSION = "1.2.3"
DEFAULT_CONFIG_PATH = APP_DIR / "config.json"

OMNI_URL = "https://apis.roblox.com/discovery-api/omni-recommendation"
AUTHENTICATED_USER_URL = "https://users.roblox.com/v1/users/authenticated"
PRESENCE_URL = "https://presence.roblox.com/v1/presence/users"
GAMES_URL = "https://games.roblox.com/v1/games"
PLACE_UNIVERSE_URL = "https://apis.roblox.com/universes/v1/places/{place_id}/universe"
GAME_NAME_URL = (
    "https://gameinternationalization.roblox.com/"
    "v1/name-description/games/{universe_id}"
)
AUTH_TICKET_URL = "https://auth.roblox.com/v1/authentication-ticket"
USERS_BY_USERNAME_URL = "https://users.roblox.com/v1/usernames/users"
USERS_BY_IDS_URL = "https://users.roblox.com/v1/users"
SHARE_LINK_RESOLVE_URL = "https://apis.roblox.com/sharelinks/v1/resolve-link"
FRIEND_REQUESTS_URL = "https://friends.roblox.com/v1/my/friends/requests"
FRIEND_REQUEST_URL = "https://friends.roblox.com/v1/users/{user_id}/request-friendship"
FRIEND_ACCEPT_URL = "https://friends.roblox.com/v1/users/{user_id}/accept-friend-request"
FRIEND_DECLINE_URL = "https://friends.roblox.com/v1/users/{user_id}/decline-friend-request"
FRIENDS_URL = "https://friends.roblox.com/v1/users/{user_id}/friends"
CURRENCY_URL = "https://economy.roblox.com/v1/user/currency"
TRANSACTION_TOTALS_URL = "https://economy.roblox.com/v2/users/{user_id}/transaction-totals"
TRANSACTIONS_URL = "https://economy.roblox.com/v2/users/{user_id}/transactions"

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{3,20}$")
ROBLOX_WEB_HOSTS = {"roblox.com", "www.roblox.com", "web.roblox.com", "ro.blox.com"}

CONTINUE_TOPIC_ID = 100000003
COOKIE_PREFIX = "_|WARNING:-DO-NOT-SHARE-THIS."
COOKIE_PATTERN = re.compile(r"_\|WARNING:-DO-NOT-SHARE-THIS\.\S+")
NAMED_COOKIE_PATTERN = re.compile(r"\.ROBLOSECURITY\s*=\s*([^;\s]+)", re.I)
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_COOKIE_LENGTH = 16_384
PENDING_LAUNCH_TTL = 30.0

LOGGER = logging.getLogger("roblox_continue_panel")


class RobloxApiError(RuntimeError):
    def __init__(self, message: str, *, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Config:
    target_place_ids: tuple[int, ...]
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True
    launch_method: str = "browser"
    request_timeout_seconds: int = 25
    account_concurrency: int = 4
    catalog_concurrency: int = 6
    retry_count: int = 2
    max_accounts: int = 100

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        raw_place_ids = raw.get("target_place_ids", [])
        if not isinstance(raw_place_ids, list):
            raise ValueError("config.json: target_place_ids должен быть массивом.")

        place_ids: list[int] = []
        seen: set[int] = set()
        for value in raw_place_ids:
            place_id = as_int(value)
            if place_id is None or place_id <= 0:
                raise ValueError(f"Некорректный PlaceId в config.json: {value!r}")
            if place_id not in seen:
                seen.add(place_id)
                place_ids.append(place_id)

        if not place_ids:
            raise ValueError("Добавьте хотя бы один PlaceId в config.json.")

        host = str(raw.get("host", "127.0.0.1")).strip()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(
                "По соображениям безопасности host должен быть 127.0.0.1, "
                "localhost или ::1."
            )

        launch_method = str(raw.get("launch_method", "browser")).casefold()
        if launch_method not in {"browser", "system"}:
            raise ValueError("launch_method должен быть 'browser' или 'system'.")

        return cls(
            target_place_ids=tuple(place_ids),
            host=host,
            port=bounded_int(raw.get("port", 8765), 1, 65535, "port"),
            open_browser=bool(raw.get("open_browser", True)),
            launch_method=launch_method,
            request_timeout_seconds=bounded_int(
                raw.get("request_timeout_seconds", 25),
                5,
                120,
                "request_timeout_seconds",
            ),
            account_concurrency=bounded_int(
                raw.get("account_concurrency", 4), 1, 12, "account_concurrency"
            ),
            catalog_concurrency=bounded_int(
                raw.get("catalog_concurrency", 6), 1, 12, "catalog_concurrency"
            ),
            retry_count=bounded_int(raw.get("retry_count", 2), 0, 5, "retry_count"),
            max_accounts=bounded_int(
                raw.get("max_accounts", 100), 1, 500, "max_accounts"
            ),
        )


@dataclass(frozen=True)
class GameCandidate:
    universe_id: int
    place_id: int | None


@dataclass(frozen=True)
class CatalogGame:
    name: str
    place_id: int
    universe_id: int

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "place_id": self.place_id,
            "universe_id": self.universe_id,
            "url": f"https://www.roblox.com/games/{self.place_id}",
        }


@dataclass(frozen=True)
class GameMatch:
    name: str
    place_id: int
    universe_id: int

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "place_id": self.place_id,
            "universe_id": self.universe_id,
            "url": f"https://www.roblox.com/games/{self.place_id}",
        }


@dataclass(frozen=True)
class FriendRequestItem:
    user_id: int
    username: str
    display_name: str
    created_at: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class InGameFriend:
    user_id: int
    username: str
    display_name: str
    last_location: str | None = None
    place_id: int | None = None
    universe_id: int | None = None

    def public(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "last_location": self.last_location,
            "place_id": self.place_id,
            "universe_id": self.universe_id,
        }


@dataclass
class AccountRecord:
    account_id: str
    cookie: str
    fingerprint: str
    user_id: int
    username: str
    display_name: str
    status: str = "ready"
    error: str | None = None
    matches: list[GameMatch] = field(default_factory=list)
    scanned_at: float | None = None
    presence: str = "unknown"
    presence_place_id: int | None = None
    presence_universe_id: int | None = None
    presence_last_location: str | None = None
    presence_updated_at: float | None = None
    robux_balance: int | None = None
    pending_robux: int | None = None
    lifetime_spent: int | None = None
    year_spent: int | None = None
    finance_status: str = "idle"
    finance_error: str | None = None
    finance_updated_at: float | None = None
    friend_requests: list[FriendRequestItem] = field(default_factory=list)
    friend_requests_status: str = "idle"
    friend_requests_error: str | None = None
    friend_requests_updated_at: float | None = None
    in_game_friends: list[InGameFriend] = field(default_factory=list)
    friends_status: str = "idle"
    friends_error: str | None = None
    friends_updated_at: float | None = None

    def public(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "status": self.status,
            "error": self.error,
            "matches": [match.public() for match in self.matches],
            "match_count": len(self.matches),
            "scanned_at": self.scanned_at,
            "presence": self.presence,
            "presence_place_id": self.presence_place_id,
            "presence_universe_id": self.presence_universe_id,
            "presence_last_location": self.presence_last_location,
            "presence_updated_at": self.presence_updated_at,
            "robux_balance": self.robux_balance,
            "pending_robux": self.pending_robux,
            "lifetime_spent": self.lifetime_spent,
            "year_spent": self.year_spent,
            "finance_status": self.finance_status,
            "finance_error": self.finance_error,
            "finance_updated_at": self.finance_updated_at,
            "friend_requests": [item.public() for item in self.friend_requests],
            "friend_requests_status": self.friend_requests_status,
            "friend_requests_error": self.friend_requests_error,
            "friend_requests_updated_at": self.friend_requests_updated_at,
            "in_game_friends": [friend.public() for friend in self.in_game_friends],
            "friends_status": self.friends_status,
            "friends_error": self.friends_error,
            "friends_updated_at": self.friends_updated_at,
        }


@dataclass(frozen=True)
class PendingLaunch:
    uri: str
    expires_at: float


def as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def bounded_int(value: Any, minimum: int, maximum: int, name: str) -> int:
    parsed = as_int(value)
    if parsed is None or not minimum <= parsed <= maximum:
        raise ValueError(f"config.json: {name} должен быть от {minimum} до {maximum}.")
    return parsed


def normalize_cookie(raw_cookie: str) -> str:
    cookie = (
        raw_cookie.strip()
        .strip('"')
        .strip("'")
        .rstrip(";")
        .strip('"')
        .strip("'")
    )
    prefix = ".ROBLOSECURITY="
    if cookie.casefold().startswith(prefix.casefold()):
        cookie = cookie[len(prefix) :].split(";", 1)[0].strip()
    return cookie


def extract_cookies(text: str) -> list[str]:
    """Извлекает cookies до первого пробельного символа и удаляет дубли."""
    if not isinstance(text, str):
        return []

    candidates = COOKIE_PATTERN.findall(text)
    candidates.extend(match.group(1) for match in NAMED_COOKIE_PATTERN.finditer(text))

    stripped = text.strip()
    if not candidates and stripped and not any(char.isspace() for char in stripped):
        candidates.append(stripped)

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        cookie = normalize_cookie(candidate)
        if not cookie.startswith(COOKIE_PREFIX):
            continue
        if len(cookie) > MAX_COOKIE_LENGTH:
            continue
        fingerprint = cookie_fingerprint(cookie)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        result.append(cookie)
    return result


def cookie_fingerprint(cookie: str) -> str:
    return hashlib.blake2s(cookie.encode("utf-8"), digest_size=16).hexdigest()


def parse_json_object(body: str, source: str) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RobloxApiError(f"{source} вернул ответ не в формате JSON.") from exc
    if not isinstance(payload, dict):
        raise RobloxApiError(f"Неожиданный формат ответа {source}.")
    return payload


def find_continue_sort(data: dict[str, Any]) -> dict[str, Any]:
    sorts = data.get("sorts")
    if not isinstance(sorts, list):
        raise RobloxApiError("В ответе Omni API нет массива sorts.")

    for sort in sorts:
        if isinstance(sort, dict) and as_int(sort.get("topicId")) == CONTINUE_TOPIC_ID:
            return sort

    keywords = ("continue", "recent", "revisit", "продолж", "недав")
    for sort in sorts:
        if not isinstance(sort, dict):
            continue
        topic = str(sort.get("topic", "")).casefold()
        if any(keyword in topic for keyword in keywords):
            return sort

    raise RobloxApiError("Roblox не вернул раздел Continue для этого аккаунта.")


def extract_candidates(data: dict[str, Any]) -> list[GameCandidate]:
    continue_sort = find_continue_sort(data)
    recommendations = continue_sort.get("recommendationList")
    if not isinstance(recommendations, list):
        raise RobloxApiError("У раздела Continue нет recommendationList.")

    content_metadata = data.get("contentMetadata", {})
    game_metadata: dict[str, Any] = {}
    if isinstance(content_metadata, dict):
        possible = content_metadata.get("Game", {})
        if isinstance(possible, dict):
            game_metadata = possible

    candidates: list[GameCandidate] = []
    seen_universe_ids: set[int] = set()
    for recommendation in recommendations:
        if not isinstance(recommendation, dict):
            continue
        if str(recommendation.get("contentType", "")).casefold() != "game":
            continue

        content_id = as_int(recommendation.get("contentId"))
        if content_id is None:
            continue

        global_metadata = game_metadata.get(str(content_id), {})
        if not isinstance(global_metadata, dict):
            global_metadata = {}
        inline_metadata = recommendation.get("contentMetadata", {})
        if not isinstance(inline_metadata, dict):
            inline_metadata = {}

        metadata = {**inline_metadata, **global_metadata}
        universe_id = as_int(metadata.get("universeId")) or content_id
        if universe_id in seen_universe_ids:
            continue
        seen_universe_ids.add(universe_id)

        place_id = as_int(metadata.get("rootPlaceId")) or as_int(
            metadata.get("placeId")
        )
        candidates.append(GameCandidate(universe_id=universe_id, place_id=place_id))
    return candidates


def chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _validate_ticket(ticket: str) -> None:
    if not ticket or "\r" in ticket or "\n" in ticket:
        raise ValueError("Roblox вернул некорректный authentication ticket.")


def build_launcher_uri(
    ticket: str,
    request_type: str,
    parameters: list[tuple[str, str]],
    *,
    tracker_id: str | None = None,
    launch_time_ms: int | None = None,
) -> str:
    _validate_ticket(ticket)
    tracker = tracker_id or str(secrets.randbelow(8_000_000_000) + 1_000_000_000)
    launch_time = launch_time_ms or int(time.time() * 1000)
    query = [("request", request_type), ("browserTrackerId", tracker), *parameters]
    launcher_url = "https://assetgame.roblox.com/game/PlaceLauncher.ashx?" + urlencode(query)
    return (
        "roblox-player:1+launchmode:play"
        f"+gameinfo:{ticket}"
        f"+launchtime:{launch_time}"
        f"+placelauncherurl:{quote(launcher_url, safe='')}"
        f"+browsertrackerid:{tracker}"
        "+robloxLocale:en_us+gameLocale:en_us+channel:+LaunchExp:InApp"
    )


def build_launch_uri(
    ticket: str,
    place_id: int,
    *,
    tracker_id: str | None = None,
    launch_time_ms: int | None = None,
) -> str:
    return build_launcher_uri(
        ticket,
        "RequestGame",
        [("placeId", str(place_id)), ("isPlayTogetherGame", "false")],
        tracker_id=tracker_id,
        launch_time_ms=launch_time_ms,
    )


def build_follow_user_uri(
    ticket: str,
    user_id: int,
    *,
    tracker_id: str | None = None,
    launch_time_ms: int | None = None,
) -> str:
    return build_launcher_uri(
        ticket,
        "RequestFollowUser",
        [
            ("userId", str(user_id)),
            ("joinAttemptId", str(uuid.uuid4())),
            ("joinAttemptOrigin", "friendsServerListJoin"),
        ],
        tracker_id=tracker_id,
        launch_time_ms=launch_time_ms,
    )


def build_private_server_uri(
    ticket: str,
    place_id: int,
    *,
    access_code: str | None = None,
    link_code: str | None = None,
    tracker_id: str | None = None,
    launch_time_ms: int | None = None,
) -> str:
    parameters = [("placeId", str(place_id)), ("isPlayTogetherGame", "false")]
    if access_code:
        parameters.append(("accessCode", access_code))
    if link_code:
        parameters.append(("linkCode", link_code))
    return build_launcher_uri(
        ticket,
        "RequestPrivateGame",
        parameters,
        tracker_id=tracker_id,
        launch_time_ms=launch_time_ms,
    )


def parse_vip_target(raw_link: str) -> dict[str, Any] | None:
    link = raw_link.strip()
    if not link or len(link) > 4096:
        return None

    parsed = urlparse(link)
    query = parse_qs(parsed.query, keep_blank_values=False)
    lowered = {key.casefold(): values for key, values in query.items()}

    if parsed.scheme.casefold() in {"roblox", "roblox-player"}:
        # Некоторые deep-link варианты кладут параметры после roblox:// без '?'.
        extra = parse_qs((parsed.netloc + parsed.path).lstrip("/"), keep_blank_values=False)
        for key, values in extra.items():
            lowered.setdefault(key.casefold(), values)
    elif parsed.scheme.casefold() in {"http", "https"}:
        if parsed.hostname is None or parsed.hostname.casefold() not in ROBLOX_WEB_HOSTS:
            return None
    else:
        return None

    def first(*names: str) -> str | None:
        for name in names:
            values = lowered.get(name.casefold())
            if values and values[0].strip():
                return values[0].strip()
        return None

    # Новый официальный формат Roblox: /share?code=...&type=Server.
    # В нём специально нет PlaceId/linkCode, поэтому код нужно резолвить API-запросом.
    share_code = first("code")
    share_type = first("type")
    if (
        share_code
        and share_type
        and share_type.casefold() == "server"
        and parsed.path.rstrip("/").casefold() in {"/share", "/share-links"}
    ):
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", share_code):
            return None
        return {"share_code": share_code, "share_type": "Server"}

    place_id = as_int(first("placeId"))
    if place_id is None:
        match = re.search(r"/games/(\d+)(?:/|$)", parsed.path, re.I)
        if match:
            place_id = int(match.group(1))

    access_code = first("accessCode")
    link_code = first("privateServerLinkCode", "linkCode")
    if place_id is None or place_id <= 0 or not (access_code or link_code):
        return None
    return {
        "place_id": place_id,
        "access_code": access_code,
        "link_code": link_code,
    }


def launch_with_system(uri: str) -> None:
    if os.name == "nt":
        os.startfile(uri)  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", uri], close_fds=True)
        return
    subprocess.Popen(["xdg-open", uri], close_fds=True)


class RobloxClient:
    def __init__(self, session: aiohttp.ClientSession, config: Config) -> None:
        self.session = session
        self.config = config

    async def request_json(
        self,
        method: str,
        url: str,
        source: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(self.config.retry_count + 1):
            try:
                async with self.session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                ) as response:
                    body = await response.text()
                    if response.status == 429 or response.status >= 500:
                        if attempt < self.config.retry_count:
                            await asyncio.sleep(0.75 * (2**attempt))
                            continue
                    if response.status == 401:
                        raise RobloxApiError(
                            f"{source}: cookie недействительна или сессия завершена.",
                            status=401,
                        )
                    if response.status >= 400:
                        raise RobloxApiError(
                            f"{source} вернул HTTP {response.status}.",
                            status=502,
                        )
                    return parse_json_object(body, source)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < self.config.retry_count:
                    await asyncio.sleep(0.75 * (2**attempt))
                    continue
                raise RobloxApiError(f"Не удалось подключиться к {source}.") from exc
        raise RobloxApiError(f"Не удалось получить ответ от {source}.")

    @staticmethod
    def cookie_headers(
        cookie: str, *, referer: str = "https://www.roblox.com/"
    ) -> dict[str, str]:
        return {
            "Cookie": f".ROBLOSECURITY={cookie}",
            "Origin": "https://www.roblox.com",
            "Referer": referer,
        }

    @staticmethod
    def response_error(body: str, fallback: str) -> str:
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            return fallback
        if isinstance(payload, dict):
            errors = payload.get("errors")
            if isinstance(errors, list):
                for item in errors:
                    if isinstance(item, dict):
                        message = item.get("message") or item.get("userFacingMessage")
                        if isinstance(message, str) and message.strip():
                            return message.strip()
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        return fallback

    async def authenticated_post(
        self,
        url: str,
        source: str,
        cookie: str,
        *,
        json_body: dict[str, Any] | None = None,
        referer: str = "https://www.roblox.com/",
    ) -> dict[str, Any]:
        base_headers = self.cookie_headers(cookie, referer=referer)
        base_headers["Content-Type"] = "application/json"

        async def post(csrf_token: str | None = None) -> tuple[int, str | None, str]:
            headers = dict(base_headers)
            if csrf_token:
                headers["X-CSRF-TOKEN"] = csrf_token
            async with self.session.post(
                url,
                headers=headers,
                json=json_body or {},
                allow_redirects=False,
            ) as response:
                return (
                    response.status,
                    response.headers.get("x-csrf-token"),
                    await response.text(),
                )

        for attempt in range(self.config.retry_count + 1):
            try:
                status, csrf_token, body = await post()
                if status == 403 and csrf_token:
                    status, _, body = await post(csrf_token)
                if status == 429 or status >= 500:
                    if attempt < self.config.retry_count:
                        await asyncio.sleep(0.75 * (2**attempt))
                        continue
                if status == 401:
                    raise RobloxApiError(
                        f"{source}: cookie недействительна или сессия завершена.",
                        status=401,
                    )
                if status == 429:
                    raise RobloxApiError(
                        f"{source}: Roblox временно ограничил запросы (429).",
                        status=429,
                    )
                if status >= 400:
                    message = self.response_error(
                        body, f"{source} вернул HTTP {status}."
                    )
                    raise RobloxApiError(message, status=400 if status < 500 else 502)
                if not body.strip():
                    return {}
                return parse_json_object(body, source)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < self.config.retry_count:
                    await asyncio.sleep(0.75 * (2**attempt))
                    continue
                raise RobloxApiError(f"Не удалось подключиться к {source}.") from exc
        raise RobloxApiError(f"Не удалось получить ответ от {source}.")

    async def resolve_username(self, username: str) -> dict[str, Any]:
        normalized = username.strip().lstrip("@")
        if not USERNAME_PATTERN.fullmatch(normalized):
            raise RobloxApiError(
                "Username должен содержать 3–20 латинских букв, цифр или символов _.",
                status=400,
            )
        payload = await self.request_json(
            "POST",
            USERS_BY_USERNAME_URL,
            "Username API",
            json_body={"usernames": [normalized], "excludeBannedUsers": True},
        )
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise RobloxApiError(f"Пользователь @{normalized} не найден.", status=404)
        user = data[0]
        if not isinstance(user, dict):
            raise RobloxApiError("Username API вернул неожиданный ответ.")
        user_id = as_int(user.get("id"))
        name = user.get("name")
        display_name = user.get("displayName")
        if user_id is None or not isinstance(name, str):
            raise RobloxApiError("Username API не вернул корректного пользователя.")
        return {
            "user_id": user_id,
            "username": name,
            "display_name": display_name if isinstance(display_name, str) else name,
        }

    async def users_by_ids(self, user_ids: Iterable[int]) -> dict[int, tuple[str, str]]:
        ids = list(dict.fromkeys(int(user_id) for user_id in user_ids if int(user_id) > 0))
        result: dict[int, tuple[str, str]] = {}
        # Roblox Users API supports batch lookup; keep chunks conservative.
        for offset in range(0, len(ids), 100):
            chunk = ids[offset : offset + 100]
            payload = await self.request_json(
                "POST",
                USERS_BY_IDS_URL,
                "Users API",
                json_body={"userIds": chunk, "excludeBannedUsers": False},
            )
            data = payload.get("data")
            if not isinstance(data, list):
                raise RobloxApiError("Users API не вернул список пользователей.")
            for raw in data:
                if not isinstance(raw, dict):
                    continue
                user_id = as_int(raw.get("id"))
                username = raw.get("name")
                display_name = raw.get("displayName")
                if user_id is None or not isinstance(username, str) or not username:
                    continue
                result[user_id] = (
                    username,
                    display_name if isinstance(display_name, str) and display_name else username,
                )
        return result

    async def send_friend_request(self, cookie: str, target_user_id: int) -> None:
        await self.authenticated_post(
            FRIEND_REQUEST_URL.format(user_id=target_user_id),
            "Friends API",
            cookie,
        )

    async def friend_requests(self, cookie: str, *, limit: int = 10) -> list[FriendRequestItem]:
        payload = await self.request_json(
            "GET",
            FRIEND_REQUESTS_URL,
            "Friends API",
            headers=self.cookie_headers(cookie),
            params={"sortOrder": "Desc", "limit": str(max(1, min(limit, 100)))},
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise RobloxApiError("Friends API не вернул список заявок.")

        raw_by_id: dict[int, dict[str, Any]] = {}
        ordered_ids: list[int] = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            user_id = as_int(raw.get("id")) or as_int(raw.get("userId"))
            if user_id is None:
                continue
            if user_id not in raw_by_id:
                ordered_ids.append(user_id)
            raw_by_id[user_id] = raw

        names = await self.users_by_ids(ordered_ids) if ordered_ids else {}
        result: list[FriendRequestItem] = []
        for user_id in ordered_ids:
            raw = raw_by_id[user_id]
            fallback_username = raw.get("name") or raw.get("username")
            fallback_display = raw.get("displayName")
            username, display_name = names.get(
                user_id,
                (
                    fallback_username if isinstance(fallback_username, str) and fallback_username else str(user_id),
                    fallback_display if isinstance(fallback_display, str) and fallback_display else (fallback_username if isinstance(fallback_username, str) and fallback_username else str(user_id)),
                ),
            )
            created = raw.get("created") or raw.get("createdAt")
            result.append(
                FriendRequestItem(
                    user_id=user_id,
                    username=username,
                    display_name=display_name,
                    created_at=created if isinstance(created, str) else None,
                )
            )
        return result

    async def accept_friend_request(self, cookie: str, requester_user_id: int) -> None:
        await self.authenticated_post(
            FRIEND_ACCEPT_URL.format(user_id=requester_user_id),
            "Friends API",
            cookie,
        )

    async def decline_friend_request(self, cookie: str, requester_user_id: int) -> None:
        await self.authenticated_post(
            FRIEND_DECLINE_URL.format(user_id=requester_user_id),
            "Friends API",
            cookie,
        )

    async def friends_in_game(self, cookie: str, user_id: int) -> list[InGameFriend]:
        payload = await self.request_json(
            "GET",
            FRIENDS_URL.format(user_id=user_id),
            "Friends API",
            headers=self.cookie_headers(cookie),
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise RobloxApiError("Friends API не вернул список друзей.")

        friend_ids: list[int] = []
        fallback_names: dict[int, tuple[str, str]] = {}
        for raw in data:
            if not isinstance(raw, dict):
                continue
            friend_id = as_int(raw.get("id")) or as_int(raw.get("userId"))
            if friend_id is None:
                continue
            if friend_id not in fallback_names:
                friend_ids.append(friend_id)
            username = raw.get("name") or raw.get("username")
            display_name = raw.get("displayName")
            fallback_username = username if isinstance(username, str) and username else str(friend_id)
            fallback_names[friend_id] = (
                fallback_username,
                display_name if isinstance(display_name, str) and display_name else fallback_username,
            )

        names = await self.users_by_ids(friend_ids) if friend_ids else {}
        presences = await self.user_presences(friend_ids)
        result: list[InGameFriend] = []
        for friend_id in friend_ids:
            username, display_name = names.get(friend_id, fallback_names[friend_id])
            presence = presences.get(friend_id)
            if not presence or presence.get("presence") != "in_game":
                continue
            result.append(
                InGameFriend(
                    user_id=friend_id,
                    username=username,
                    display_name=display_name,
                    last_location=presence.get("last_location"),
                    place_id=presence.get("place_id"),
                    universe_id=presence.get("universe_id"),
                )
            )
        result.sort(key=lambda item: item.username.casefold())
        return result

    async def transaction_summary(
        self, cookie: str, user_id: int, time_frame: str
    ) -> dict[str, Any]:
        return await self.request_json(
            "GET",
            TRANSACTION_TOTALS_URL.format(user_id=user_id),
            "Economy API",
            headers=self.cookie_headers(cookie),
            params={"timeFrame": time_frame, "transactionType": "summary"},
        )

    async def lifetime_purchase_total(self, cookie: str, user_id: int) -> int:
        cursor: str | None = None
        total = 0
        seen_cursors: set[str] = set()
        while True:
            params = {
                "transactionType": "Purchase",
                "limit": "100",
                "sortOrder": "Asc",
            }
            if cursor:
                params["cursor"] = cursor
            payload = await self.request_json(
                "GET",
                TRANSACTIONS_URL.format(user_id=user_id),
                "Economy API",
                headers=self.cookie_headers(cookie),
                params=params,
            )
            data = payload.get("data")
            if not isinstance(data, list):
                raise RobloxApiError("Economy API не вернул историю покупок.")
            for transaction in data:
                if not isinstance(transaction, dict):
                    continue
                currency = transaction.get("currency")
                if not isinstance(currency, dict):
                    continue
                amount = as_int(currency.get("amount"))
                if amount is not None and amount != 0:
                    total += abs(amount)
            next_cursor = payload.get("nextPageCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            if next_cursor in seen_cursors:
                raise RobloxApiError("Economy API зациклил страницы истории покупок.")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return total

    async def account_finance(self, cookie: str, user_id: int) -> dict[str, int]:
        currency_task = self.request_json(
            "GET",
            CURRENCY_URL,
            "Economy API",
            headers=self.cookie_headers(cookie),
        )
        year_task = self.transaction_summary(cookie, user_id, "Year")
        currency, year = await asyncio.gather(currency_task, year_task)
        balance = as_int(currency.get("robux"))
        if balance is None:
            raise RobloxApiError("Economy API не вернул баланс Robux.")
        year_spent = abs(as_int(year.get("outgoingRobuxTotal")) or 0)
        pending = max(0, as_int(year.get("pendingRobuxTotal")) or 0)

        try:
            all_time = await self.transaction_summary(cookie, user_id, "AllTime")
            lifetime_spent = abs(as_int(all_time.get("outgoingRobuxTotal")) or 0)
        except RobloxApiError:
            lifetime_spent = await self.lifetime_purchase_total(cookie, user_id)

        return {
            "robux_balance": balance,
            "pending_robux": pending,
            "lifetime_spent": lifetime_spent,
            "year_spent": year_spent,
        }

    async def resolve_vip_link(self, raw_link: str, cookie: str) -> dict[str, Any]:
        target = parse_vip_target(raw_link)
        if target is not None:
            share_code = target.get("share_code")
            if isinstance(share_code, str):
                payload = await self.authenticated_post(
                    SHARE_LINK_RESOLVE_URL,
                    "Share Links API",
                    cookie,
                    json_body={"linkId": share_code, "linkType": "Server"},
                    referer=raw_link.strip(),
                )
                invite = payload.get("privateServerInviteData")
                if not isinstance(invite, dict):
                    raise RobloxApiError(
                        "Roblox не вернул данные VIP-сервера для этой share-ссылки.",
                        status=400,
                    )
                status = invite.get("status")
                if isinstance(status, str) and status.casefold() != "valid":
                    raise RobloxApiError(
                        f"VIP-ссылка недействительна или недоступна ({status}).",
                        status=400,
                    )
                place_id = as_int(invite.get("placeId"))
                link_code = invite.get("linkCode")
                if place_id is None or place_id <= 0 or not isinstance(link_code, str) or not link_code:
                    raise RobloxApiError(
                        "Roblox не смог определить PlaceId или linkCode этой VIP-ссылки.",
                        status=400,
                    )
                return {
                    "place_id": place_id,
                    "access_code": None,
                    "link_code": link_code,
                }
            return target

        parsed = urlparse(raw_link.strip())
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or parsed.hostname is None
            or parsed.hostname.casefold() not in ROBLOX_WEB_HOSTS
        ):
            raise RobloxApiError("Вставьте корректную ссылку VIP-сервера Roblox.", status=400)

        # Сохраняем поддержку Roblox redirect-ссылок и старого формата.
        current = raw_link.strip()
        try:
            for _ in range(5):
                async with self.session.get(current, allow_redirects=False) as response:
                    location = response.headers.get("Location")
                    if response.status not in {301, 302, 303, 307, 308} or not location:
                        break
                    next_parsed = urlparse(location)
                    if not next_parsed.scheme:
                        base = urlparse(current)
                        location = f"{base.scheme}://{base.netloc}{location}"
                        next_parsed = urlparse(location)
                    if (
                        next_parsed.hostname is None
                        or next_parsed.hostname.casefold() not in ROBLOX_WEB_HOSTS
                    ):
                        break
                    current = location
                    target = parse_vip_target(current)
                    if target is not None:
                        share_code = target.get("share_code")
                        if isinstance(share_code, str):
                            return await self.resolve_vip_link(current, cookie)
                        return target
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass

        raise RobloxApiError(
            "Не удалось распознать VIP-ссылку. Поддерживаются roblox.com/share?code=...&type=Server "
            "и старые ссылки games/... с privateServerLinkCode/accessCode.",
            status=400,
        )

    async def authenticated_user(self, cookie: str) -> dict[str, Any]:
        payload = await self.request_json(
            "GET",
            AUTHENTICATED_USER_URL,
            "Users API",
            headers=self.cookie_headers(cookie),
        )
        user_id = as_int(payload.get("id"))
        username = payload.get("name")
        display_name = payload.get("displayName")
        if user_id is None or not isinstance(username, str) or not username.strip():
            raise RobloxApiError("Users API не вернул корректный аккаунт.")
        return {
            "user_id": user_id,
            "username": username.strip(),
            "display_name": (
                display_name.strip()
                if isinstance(display_name, str) and display_name.strip()
                else username.strip()
            ),
        }

    async def user_presences(self, user_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Возвращает публичный статус аккаунтов без использования их cookies."""
        if not user_ids:
            return {}

        result: dict[int, dict[str, Any]] = {}
        for batch in chunks(user_ids, 50):
            payload = await self.request_json(
                "POST",
                PRESENCE_URL,
                "Presence API",
                json_body={"userIds": batch},
            )
            raw_presences = payload.get("userPresences")
            if not isinstance(raw_presences, list):
                raise RobloxApiError("Presence API не вернул массив userPresences.")

            for raw in raw_presences:
                if not isinstance(raw, dict):
                    continue
                user_id = as_int(raw.get("userId"))
                presence_type = as_int(raw.get("userPresenceType"))
                if user_id is None or presence_type is None:
                    continue

                if presence_type == 0:
                    presence = "offline"
                elif presence_type == 2:
                    presence = "in_game"
                else:
                    # Roblox Studio и сайт считаем обычным состоянием «Онлайн».
                    presence = "online"

                last_location = raw.get("lastLocation")
                result[user_id] = {
                    "presence": presence,
                    "place_id": as_int(raw.get("placeId")),
                    "universe_id": as_int(raw.get("universeId")),
                    "last_location": (
                        last_location.strip()
                        if isinstance(last_location, str) and last_location.strip()
                        else None
                    ),
                }
        return result

    async def resolve_universe_id(self, place_id: int) -> int:
        payload = await self.request_json(
            "GET",
            PLACE_UNIVERSE_URL.format(place_id=place_id),
            f"Universe API для PlaceId {place_id}",
        )
        universe_id = as_int(payload.get("universeId"))
        if universe_id is None:
            raise RobloxApiError(
                f"Для PlaceId {place_id} не удалось определить UniverseId."
            )
        return universe_id

    async def english_name(self, universe_id: int) -> str:
        payload = await self.request_json(
            "GET",
            GAME_NAME_URL.format(universe_id=universe_id),
            f"English name API для UniverseId {universe_id}",
        )
        translations = payload.get("data")
        if not isinstance(translations, list):
            raise RobloxApiError(
                f"Для UniverseId {universe_id} не получен список названий."
            )
        for translation in translations:
            if not isinstance(translation, dict):
                continue
            if str(translation.get("languageCode", "")).casefold() != "en":
                continue
            name = translation.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
            raise RobloxApiError(
                f"Название en для UniverseId {universe_id} пустое."
            )
        raise RobloxApiError(
            f"Для UniverseId {universe_id} отсутствует название с кодом en."
        )

    async def catalog_game(self, place_id: int) -> CatalogGame:
        universe_id = await self.resolve_universe_id(place_id)
        name = await self.english_name(universe_id)
        return CatalogGame(name=name, place_id=place_id, universe_id=universe_id)

    async def omni(self, cookie: str) -> dict[str, Any]:
        payload = {
            "pageType": "Home",
            "sessionId": str(uuid.uuid4()),
            "isTruncatedResultsEnabled": True,
        }
        base_headers = self.cookie_headers(cookie)

        async def post(csrf_token: str | None = None) -> tuple[int, str | None, str]:
            headers = dict(base_headers)
            if csrf_token:
                headers["X-CSRF-TOKEN"] = csrf_token
            async with self.session.post(OMNI_URL, json=payload, headers=headers) as response:
                return (
                    response.status,
                    response.headers.get("x-csrf-token"),
                    await response.text(),
                )

        for attempt in range(self.config.retry_count + 1):
            try:
                status, csrf_token, body = await post()
                if status == 403 and csrf_token:
                    status, _, body = await post(csrf_token)
                if status == 429 or status >= 500:
                    if attempt < self.config.retry_count:
                        await asyncio.sleep(0.75 * (2**attempt))
                        continue
                if status == 401:
                    raise RobloxApiError(
                        "Omni API: cookie недействительна или сессия завершена.",
                        status=401,
                    )
                if status >= 400:
                    raise RobloxApiError(f"Omni API вернул HTTP {status}.")
                return parse_json_object(body, "Omni API")
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < self.config.retry_count:
                    await asyncio.sleep(0.75 * (2**attempt))
                    continue
                raise RobloxApiError("Не удалось подключиться к Omni API.") from exc
        raise RobloxApiError("Не удалось получить ответ от Omni API.")

    async def root_place_ids(self, universe_ids: list[int]) -> dict[int, int]:
        result: dict[int, int] = {}
        for batch in chunks(universe_ids, 50):
            payload = await self.request_json(
                "GET",
                GAMES_URL,
                "Games API",
                params={"universeIds": ",".join(map(str, batch))},
            )
            games = payload.get("data")
            if not isinstance(games, list):
                raise RobloxApiError("Games API не вернул массив data.")
            for game in games:
                if not isinstance(game, dict):
                    continue
                universe_id = as_int(game.get("id"))
                place_id = as_int(game.get("rootPlaceId"))
                if universe_id is not None and place_id is not None:
                    result[universe_id] = place_id
        return result

    async def continue_candidates(self, cookie: str) -> list[GameCandidate]:
        data = await self.omni(cookie)
        candidates = extract_candidates(data)
        missing = [
            candidate.universe_id
            for candidate in candidates
            if candidate.place_id is None
        ]
        resolved = await self.root_place_ids(missing) if missing else {}
        return [
            GameCandidate(
                universe_id=candidate.universe_id,
                place_id=candidate.place_id or resolved.get(candidate.universe_id),
            )
            for candidate in candidates
        ]

    async def authentication_ticket(self, cookie: str, place_id: int | None = None) -> str:
        referer = (
            f"https://www.roblox.com/games/{place_id}"
            if place_id is not None
            else "https://www.roblox.com/"
        )
        base_headers = self.cookie_headers(cookie, referer=referer)

        async def post(
            csrf_token: str | None = None,
        ) -> tuple[int, str | None, str | None]:
            headers = dict(base_headers)
            headers["Content-Type"] = "application/json"
            if csrf_token:
                headers["X-CSRF-TOKEN"] = csrf_token
            async with self.session.post(
                AUTH_TICKET_URL,
                headers=headers,
                data=b"{}",
                allow_redirects=False,
            ) as response:
                await response.read()
                return (
                    response.status,
                    response.headers.get("x-csrf-token"),
                    response.headers.get("rbx-authentication-ticket"),
                )

        try:
            status, csrf_token, ticket = await post()
            if status == 403 and csrf_token:
                status, _, ticket = await post(csrf_token)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise RobloxApiError("Не удалось запросить билет запуска Roblox.") from exc

        if status == 401:
            raise RobloxApiError(
                "Cookie выбранного аккаунта больше недействительна.", status=401
            )
        if status == 429:
            raise RobloxApiError(
                "Roblox временно ограничил создание билетов запуска (429).",
                status=429,
            )
        if status >= 400:
            raise RobloxApiError(
                f"Roblox не выдал билет запуска (HTTP {status})."
            )
        if not ticket:
            raise RobloxApiError("В ответе Roblox отсутствует authentication ticket.")
        return ticket


class PanelState:
    def __init__(self, config: Config, index_template: str) -> None:
        self.config = config
        self.index_template = index_template
        self.panel_token = secrets.token_urlsafe(32)
        self.session: aiohttp.ClientSession | None = None
        self.roblox: RobloxClient | None = None
        self.accounts: dict[str, AccountRecord] = {}
        self.catalog: dict[int, CatalogGame] = {}
        self.catalog_errors: dict[int, str] = {}
        self.catalog_loaded_at: float | None = None
        self.catalog_loading = False
        self.pending_launches: dict[str, PendingLaunch] = {}
        self.account_lock = asyncio.Lock()
        self.catalog_lock = asyncio.Lock()
        self.scan_lock = asyncio.Lock()
        self.presence_lock = asyncio.Lock()
        self.details_locks: dict[str, asyncio.Lock] = {}

    def require_client(self) -> RobloxClient:
        if self.roblox is None:
            raise RuntimeError("HTTP-клиент ещё не запущен.")
        return self.roblox

    def public_state(self) -> dict[str, Any]:
        return {
            "accounts": [account.public() for account in self.accounts.values()],
            "catalog": {
                "target_count": len(self.config.target_place_ids),
                "loaded_count": len(self.catalog),
                "error_count": len(self.catalog_errors),
                "loading": self.catalog_loading,
                "loaded_at": self.catalog_loaded_at,
            },
            "settings": {
                "launch_method": self.config.launch_method,
                "max_accounts": self.config.max_accounts,
            },
        }

    async def refresh_catalog(self) -> dict[str, Any]:
        async with self.catalog_lock:
            self.catalog_loading = True
            client = self.require_client()
            semaphore = asyncio.Semaphore(self.config.catalog_concurrency)

            async def load(
                place_id: int,
            ) -> tuple[int, CatalogGame | None, str | None]:
                try:
                    async with semaphore:
                        game = await client.catalog_game(place_id)
                    return place_id, game, None
                except RobloxApiError as exc:
                    return place_id, None, str(exc)

            try:
                results = await asyncio.gather(
                    *(load(place_id) for place_id in self.config.target_place_ids)
                )
                catalog: dict[int, CatalogGame] = {}
                errors: dict[int, str] = {}
                for place_id, game, error in results:
                    if game is not None:
                        catalog[place_id] = game
                    elif error:
                        errors[place_id] = error
                self.catalog = catalog
                self.catalog_errors = errors
                self.catalog_loaded_at = time.time()
            finally:
                self.catalog_loading = False
        return self.public_state()["catalog"]

    async def import_cookies(self, cookies: list[str]) -> dict[str, Any]:
        client = self.require_client()
        existing_fingerprints = {
            account.fingerprint for account in self.accounts.values()
        }
        unique: list[str] = []
        duplicate_count = 0
        for cookie in cookies:
            fingerprint = cookie_fingerprint(cookie)
            if fingerprint in existing_fingerprints:
                duplicate_count += 1
                continue
            existing_fingerprints.add(fingerprint)
            unique.append(cookie)

        remaining = self.config.max_accounts - len(self.accounts)
        if remaining <= 0:
            raise RobloxApiError(
                f"Достигнут лимит {self.config.max_accounts} аккаунтов.", status=400
            )
        skipped_limit = max(0, len(unique) - remaining)
        unique = unique[:remaining]

        semaphore = asyncio.Semaphore(self.config.account_concurrency)

        async def validate(
            index: int, cookie: str
        ) -> tuple[int, str, dict[str, Any] | None, str | None]:
            try:
                async with semaphore:
                    user = await client.authenticated_user(cookie)
                return index, cookie, user, None
            except RobloxApiError as exc:
                return index, cookie, None, str(exc)

        validated = await asyncio.gather(
            *(validate(index, cookie) for index, cookie in enumerate(unique, 1))
        )

        added = 0
        updated = 0
        errors: list[dict[str, Any]] = []
        async with self.account_lock:
            by_user_id = {
                account.user_id: account for account in self.accounts.values()
            }
            for index, cookie, user, error in validated:
                if user is None:
                    errors.append(
                        {"item": index, "error": error or "Неизвестная ошибка."}
                    )
                    continue
                fingerprint = cookie_fingerprint(cookie)
                existing = by_user_id.get(user["user_id"])
                if existing is not None:
                    existing.cookie = cookie
                    existing.fingerprint = fingerprint
                    existing.username = user["username"]
                    existing.display_name = user["display_name"]
                    existing.error = None
                    if existing.status == "error":
                        existing.status = "ready"
                    existing.finance_status = "idle"
                    existing.friend_requests_status = "idle"
                    existing.friends_status = "idle"
                    updated += 1
                    continue

                account = AccountRecord(
                    account_id=secrets.token_urlsafe(12),
                    cookie=cookie,
                    fingerprint=fingerprint,
                    user_id=user["user_id"],
                    username=user["username"],
                    display_name=user["display_name"],
                )
                self.accounts[account.account_id] = account
                by_user_id[account.user_id] = account
                added += 1

        await self.refresh_presence()

        return {
            "added": added,
            "updated": updated,
            "duplicates": duplicate_count,
            "skipped_limit": skipped_limit,
            "errors": errors,
            "accounts": [account.public() for account in self.accounts.values()],
        }

    async def refresh_presence(self) -> list[dict[str, Any]]:
        if not self.accounts:
            return []

        async with self.presence_lock:
            accounts = list(self.accounts.values())
            try:
                presences = await self.require_client().user_presences(
                    [account.user_id for account in accounts]
                )
            except RobloxApiError:
                # Ошибка статуса не должна ломать импорт или проверку Continue.
                LOGGER.warning("Не удалось обновить Presence", exc_info=True)
                return [account.public() for account in self.accounts.values()]

            updated_at = time.time()
            for account in accounts:
                presence = presences.get(account.user_id)
                if presence is None:
                    account.presence = "unknown"
                    account.presence_place_id = None
                    account.presence_universe_id = None
                    account.presence_last_location = None
                else:
                    account.presence = presence["presence"]
                    account.presence_place_id = presence["place_id"]
                    account.presence_universe_id = presence["universe_id"]
                    account.presence_last_location = presence["last_location"]
                account.presence_updated_at = updated_at

        return [account.public() for account in self.accounts.values()]

    def require_account(self, account_id: str) -> AccountRecord:
        account = self.accounts.get(account_id)
        if account is None:
            raise RobloxApiError("Аккаунт не найден.", status=404)
        return account

    async def refresh_account_details(self, account_id: str) -> dict[str, Any]:
        account = self.require_account(account_id)
        lock = self.details_locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            # Аккаунт мог быть удалён во время ожидания блокировки.
            account = self.require_account(account_id)
            client = self.require_client()
            account.finance_status = "loading"
            account.finance_error = None
            account.friend_requests_status = "loading"
            account.friend_requests_error = None
            account.friends_status = "loading"
            account.friends_error = None

            async def load_finance() -> None:
                try:
                    finance = await client.account_finance(account.cookie, account.user_id)
                    account.robux_balance = finance["robux_balance"]
                    account.pending_robux = finance["pending_robux"]
                    account.lifetime_spent = finance["lifetime_spent"]
                    account.year_spent = finance["year_spent"]
                    account.finance_status = "ready"
                    account.finance_error = None
                except RobloxApiError as exc:
                    account.finance_status = "error"
                    account.finance_error = str(exc)
                finally:
                    account.finance_updated_at = time.time()

            async def load_requests() -> None:
                try:
                    account.friend_requests = await client.friend_requests(account.cookie)
                    account.friend_requests_status = "ready"
                    account.friend_requests_error = None
                except RobloxApiError as exc:
                    account.friend_requests_status = "error"
                    account.friend_requests_error = str(exc)
                finally:
                    account.friend_requests_updated_at = time.time()

            async def load_friends() -> None:
                try:
                    account.in_game_friends = await client.friends_in_game(
                        account.cookie, account.user_id
                    )
                    account.friends_status = "ready"
                    account.friends_error = None
                except RobloxApiError as exc:
                    account.friends_status = "error"
                    account.friends_error = str(exc)
                finally:
                    account.friends_updated_at = time.time()

            await asyncio.gather(load_finance(), load_requests(), load_friends())
            return account.public()

    async def send_friend_request(
        self, account_id: str, username: str
    ) -> dict[str, Any]:
        account = self.require_account(account_id)
        target = await self.require_client().resolve_username(username)
        if target["user_id"] == account.user_id:
            raise RobloxApiError("Нельзя отправить заявку самому себе.", status=400)
        await self.require_client().send_friend_request(
            account.cookie, target["user_id"]
        )
        return {"sent": True, "target": target, "account": account.public()}

    async def respond_friend_request(
        self, account_id: str, requester_user_id: int, *, accept: bool
    ) -> dict[str, Any]:
        account = self.require_account(account_id)
        client = self.require_client()
        if accept:
            await client.accept_friend_request(account.cookie, requester_user_id)
        else:
            await client.decline_friend_request(account.cookie, requester_user_id)
        account.friend_requests = [
            item for item in account.friend_requests if item.user_id != requester_user_id
        ]
        account.friend_requests_updated_at = time.time()
        if accept:
            try:
                account.in_game_friends = await client.friends_in_game(
                    account.cookie, account.user_id
                )
                account.friends_status = "ready"
                account.friends_error = None
                account.friends_updated_at = time.time()
            except RobloxApiError as exc:
                account.friends_status = "error"
                account.friends_error = str(exc)
        return {"accepted": accept, "account": account.public()}

    async def scan_accounts(self) -> list[dict[str, Any]]:
        if self.scan_lock.locked():
            raise RobloxApiError("Проверка уже выполняется.", status=409)
        if not self.accounts:
            raise RobloxApiError("Сначала добавьте хотя бы один аккаунт.", status=400)

        async with self.scan_lock:
            if len(self.catalog) + len(self.catalog_errors) < len(
                self.config.target_place_ids
            ):
                await self.refresh_catalog()

            client = self.require_client()
            target_ids = set(self.config.target_place_ids)
            semaphore = asyncio.Semaphore(self.config.account_concurrency)
            accounts_to_scan = list(self.accounts.values())

            for account in accounts_to_scan:
                account.status = "scanning"
                account.error = None

            async def scan(account: AccountRecord) -> None:
                try:
                    async with semaphore:
                        candidates = await client.continue_candidates(account.cookie)
                    matches: list[GameMatch] = []
                    for candidate in candidates:
                        if candidate.place_id is None or candidate.place_id not in target_ids:
                            continue
                        catalog_game = self.catalog.get(candidate.place_id)
                        name = (
                            catalog_game.name
                            if catalog_game is not None
                            else f"PlaceId {candidate.place_id}"
                        )
                        matches.append(
                            GameMatch(
                                name=name,
                                place_id=candidate.place_id,
                                universe_id=candidate.universe_id,
                            )
                        )
                    account.matches = matches
                    account.status = "done"
                    account.error = None
                except RobloxApiError as exc:
                    account.matches = []
                    account.status = "error"
                    account.error = str(exc)
                finally:
                    account.scanned_at = time.time()

            await asyncio.gather(*(scan(account) for account in accounts_to_scan))
            await self.refresh_presence()
        return [account.public() for account in self.accounts.values()]

    async def remove_account(self, account_id: str) -> bool:
        async with self.account_lock:
            account = self.accounts.pop(account_id, None)
            if account is None:
                return False
            account.cookie = ""
            account.fingerprint = ""
            self.details_locks.pop(account_id, None)
            return True

    async def clear_accounts(self) -> None:
        async with self.account_lock:
            for account in self.accounts.values():
                account.cookie = ""
                account.fingerprint = ""
            self.accounts.clear()
            self.details_locks.clear()

    def reveal_account_cookie(self, account_id: str) -> dict[str, str]:
        account = self.require_account(account_id)
        if not account.cookie:
            raise RobloxApiError("Cookie аккаунта отсутствует в памяти.", status=404)
        # Cookie выдаётся только по явному локальному POST-действию и не входит в /api/state.
        return {"cookie": account.cookie, "username": account.username}

    def purge_expired_launches(self) -> None:
        now = time.monotonic()
        expired = [
            nonce
            for nonce, pending in self.pending_launches.items()
            if pending.expires_at <= now
        ]
        for nonce in expired:
            self.pending_launches.pop(nonce, None)

    def deliver_launch(self, account: AccountRecord, uri: str) -> dict[str, Any]:
        if self.config.launch_method == "system":
            try:
                launch_with_system(uri)
            except OSError as exc:
                raise RobloxApiError(
                    "Система не смогла открыть протокол roblox-player. "
                    "Переустановите Roblox Player."
                ) from exc
            return {"launched": True, "username": account.username}

        self.purge_expired_launches()
        nonce = secrets.token_urlsafe(24)
        self.pending_launches[nonce] = PendingLaunch(
            uri=uri,
            expires_at=time.monotonic() + PENDING_LAUNCH_TTL,
        )
        return {
            "launched": False,
            "open_url": f"/open/{nonce}",
            "username": account.username,
        }

    async def create_launch(self, account_id: str, place_id: int) -> dict[str, Any]:
        account = self.require_account(account_id)
        if place_id not in self.config.target_place_ids:
            raise RobloxApiError("PlaceId отсутствует в config.json.", status=400)
        if not any(match.place_id == place_id for match in account.matches):
            raise RobloxApiError(
                "Эта игра не была найдена у выбранного аккаунта.", status=409
            )

        ticket = await self.require_client().authentication_ticket(
            account.cookie, place_id
        )
        return self.deliver_launch(account, build_launch_uri(ticket, place_id))

    async def create_friend_launch(
        self, account_id: str, friend_user_id: int
    ) -> dict[str, Any]:
        account = self.require_account(account_id)
        friend = next(
            (
                item
                for item in account.in_game_friends
                if item.user_id == friend_user_id
            ),
            None,
        )
        if friend is None:
            raise RobloxApiError(
                "Друг уже не отображается в игре. Обновите список друзей.",
                status=409,
            )
        ticket = await self.require_client().authentication_ticket(account.cookie, None)
        result = self.deliver_launch(
            account, build_follow_user_uri(ticket, friend_user_id)
        )
        result["friend_username"] = friend.username
        return result

    async def create_vip_launch(self, account_id: str, link: str) -> dict[str, Any]:
        account = self.require_account(account_id)
        target = await self.require_client().resolve_vip_link(link, account.cookie)
        place_id = as_int(target.get("place_id"))
        if place_id is None:
            raise RobloxApiError("В VIP-ссылке отсутствует PlaceId.", status=400)
        ticket = await self.require_client().authentication_ticket(
            account.cookie, place_id
        )
        uri = build_private_server_uri(
            ticket,
            place_id,
            access_code=target.get("access_code"),
            link_code=target.get("link_code"),
        )
        result = self.deliver_launch(account, uri)
        result["place_id"] = place_id
        return result

    def consume_launch(self, nonce: str) -> str | None:
        self.purge_expired_launches()
        pending = self.pending_launches.pop(nonce, None)
        if pending is None or pending.expires_at <= time.monotonic():
            return None
        return pending.uri

    async def close(self) -> None:
        await self.clear_accounts()
        self.pending_launches.clear()
        self.panel_token = ""


def json_response(payload: Any, *, status: int = 200) -> web.Response:
    return web.json_response(
        payload,
        status=status,
        headers={"Cache-Control": "no-store"},
    )


def get_state(request: web.Request) -> PanelState:
    return request.app[STATE_KEY]


@web.middleware
async def error_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    try:
        return await handler(request)
    except RobloxApiError as exc:
        return json_response({"error": str(exc)}, status=exc.status)
    except web.HTTPException:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError):
        return json_response({"error": "Некорректный формат запроса."}, status=400)
    except Exception:
        LOGGER.exception("Необработанная ошибка локального сервера")
        return json_response({"error": "Внутренняя ошибка локального сервера."}, status=500)


@web.middleware
async def local_security_middleware(
    request: web.Request, handler: Any
) -> web.StreamResponse:
    host = request.host.rsplit(":", 1)[0].strip("[]").casefold()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise web.HTTPForbidden(text="Local access only")

    if request.path.startswith("/api/") and request.method not in {"GET", "HEAD"}:
        expected = get_state(request).panel_token
        supplied = request.headers.get("X-Panel-Token", "")
        if not expected or not secrets.compare_digest(supplied, expected):
            return json_response({"error": "Недействительный токен панели."}, status=403)

    response = await handler(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.path == "/" or request.path.endswith(".html"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
    return response


async def handle_index(request: web.Request) -> web.Response:
    state = get_state(request)
    html = state.index_template.replace("__PANEL_TOKEN__", state.panel_token)
    return web.Response(
        text=html,
        content_type="text/html",
        charset="utf-8",
        headers={"Cache-Control": "no-store"},
    )


async def handle_state(request: web.Request) -> web.Response:
    return json_response(get_state(request).public_state())


async def handle_import(request: web.Request) -> web.Response:
    if request.content_length is not None and request.content_length > (
        MAX_IMPORT_BYTES + 64 * 1024
    ):
        raise RobloxApiError("Файл слишком большой. Максимум 2 МБ.", status=413)
    payload = await request.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise RobloxApiError("Ожидается текст с cookie.", status=400)
    text = payload["text"]
    if len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise RobloxApiError("Файл слишком большой. Максимум 2 МБ.", status=413)
    cookies = extract_cookies(text)
    if not cookies:
        raise RobloxApiError(
            "Не найдено значений, начинающихся с "
            "_|WARNING:-DO-NOT-SHARE-THIS.",
            status=400,
        )
    result = await get_state(request).import_cookies(cookies)
    return json_response(result)


async def handle_scan(request: web.Request) -> web.Response:
    accounts = await get_state(request).scan_accounts()
    return json_response({"accounts": accounts})


async def handle_refresh_catalog(request: web.Request) -> web.Response:
    catalog = await get_state(request).refresh_catalog()
    return json_response({"catalog": catalog})


async def handle_refresh_presence(request: web.Request) -> web.Response:
    accounts = await get_state(request).refresh_presence()
    return json_response({"accounts": accounts})


async def handle_refresh_account_details(request: web.Request) -> web.Response:
    account = await get_state(request).refresh_account_details(
        request.match_info["account_id"]
    )
    return json_response({"account": account})


async def handle_send_friend_request(request: web.Request) -> web.Response:
    payload = await request.json()
    username = payload.get("username") if isinstance(payload, dict) else None
    if not isinstance(username, str) or not username.strip():
        raise RobloxApiError("Введите username пользователя.", status=400)
    result = await get_state(request).send_friend_request(
        request.match_info["account_id"], username
    )
    return json_response(result)


async def handle_accept_friend_request(request: web.Request) -> web.Response:
    requester_user_id = as_int(request.match_info.get("user_id"))
    if requester_user_id is None:
        raise RobloxApiError("Некорректный UserId заявки.", status=400)
    result = await get_state(request).respond_friend_request(
        request.match_info["account_id"], requester_user_id, accept=True
    )
    return json_response(result)


async def handle_decline_friend_request(request: web.Request) -> web.Response:
    requester_user_id = as_int(request.match_info.get("user_id"))
    if requester_user_id is None:
        raise RobloxApiError("Некорректный UserId заявки.", status=400)
    result = await get_state(request).respond_friend_request(
        request.match_info["account_id"], requester_user_id, accept=False
    )
    return json_response(result)


async def handle_join_friend(request: web.Request) -> web.Response:
    payload = await request.json()
    friend_user_id = as_int(payload.get("user_id")) if isinstance(payload, dict) else None
    if friend_user_id is None:
        raise RobloxApiError("Не указан UserId друга.", status=400)
    result = await get_state(request).create_friend_launch(
        request.match_info["account_id"], friend_user_id
    )
    return json_response(result)


async def handle_join_vip(request: web.Request) -> web.Response:
    payload = await request.json()
    link = payload.get("link") if isinstance(payload, dict) else None
    if not isinstance(link, str) or not link.strip():
        raise RobloxApiError("Вставьте ссылку VIP-сервера.", status=400)
    result = await get_state(request).create_vip_launch(
        request.match_info["account_id"], link
    )
    return json_response(result)


async def handle_copy_cookie(request: web.Request) -> web.Response:
    result = get_state(request).reveal_account_cookie(request.match_info["account_id"])
    return json_response(result)


async def handle_remove_account(request: web.Request) -> web.Response:
    removed = await get_state(request).remove_account(request.match_info["account_id"])
    if not removed:
        raise RobloxApiError("Аккаунт не найден.", status=404)
    return json_response({"removed": True})


async def handle_clear_accounts(request: web.Request) -> web.Response:
    await get_state(request).clear_accounts()
    return json_response({"cleared": True})


async def handle_launch(request: web.Request) -> web.Response:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise RobloxApiError("Некорректный запрос запуска.", status=400)
    account_id = payload.get("account_id")
    place_id = as_int(payload.get("place_id"))
    if not isinstance(account_id, str) or place_id is None:
        raise RobloxApiError("Не указан аккаунт или PlaceId.", status=400)
    result = await get_state(request).create_launch(account_id, place_id)
    return json_response(result)


async def handle_open_launch(request: web.Request) -> web.StreamResponse:
    uri = get_state(request).consume_launch(request.match_info["nonce"])
    if uri is None:
        raise web.HTTPGone(text="Launch ticket expired")
    raise web.HTTPFound(location=uri, headers={"Cache-Control": "no-store"})


async def handle_health(request: web.Request) -> web.Response:
    return json_response({"ok": True, "version": APP_VERSION})


async def handle_styles(request: web.Request) -> web.FileResponse:
    response = web.FileResponse(WEB_DIR / "styles.css")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


async def handle_script(request: web.Request) -> web.FileResponse:
    response = web.FileResponse(WEB_DIR / "app.js")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


STATE_KEY: web.AppKey[PanelState] = web.AppKey("panel_state", PanelState)


def create_app(
    config: Config,
    *,
    start_catalog: bool = True,
    open_browser_on_start: bool | None = None,
) -> web.Application:
    index_template = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    state = PanelState(config, index_template)
    app = web.Application(
        middlewares=[error_middleware, local_security_middleware],
        client_max_size=MAX_IMPORT_BYTES + 64 * 1024,
    )
    app[STATE_KEY] = state

    async def lifecycle(application: web.Application):
        panel_state = application[STATE_KEY]
        timeout = aiohttp.ClientTimeout(total=config.request_timeout_seconds)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
        }
        panel_state.session = aiohttp.ClientSession(
            timeout=timeout,
            headers=headers,
            # Cookies каждого Roblox-аккаунта передаются только явно в запросе.
            # Общее cookie-хранилище могло смешивать сессии между аккаунтами.
            cookie_jar=aiohttp.DummyCookieJar(),
            trust_env=True,
        )
        panel_state.roblox = RobloxClient(panel_state.session, config)
        if start_catalog:
            try:
                await panel_state.refresh_catalog()
            except Exception:
                LOGGER.exception("Не удалось предварительно загрузить список игр")

        should_open = (
            config.open_browser
            if open_browser_on_start is None
            else open_browser_on_start
        )
        if should_open:
            host_for_url = (
                "127.0.0.1" if config.host in {"::1", "localhost"} else config.host
            )
            url = f"http://{host_for_url}:{config.port}"
            asyncio.get_running_loop().call_later(0.9, webbrowser.open, url)

        yield
        await panel_state.close()
        if panel_state.session is not None:
            await panel_state.session.close()
        panel_state.roblox = None
        panel_state.session = None

    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/", handle_index)
    app.router.add_get("/styles.css", handle_styles)
    app.router.add_get("/app.js", handle_script)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/state", handle_state)
    app.router.add_post("/api/accounts/import", handle_import)
    app.router.add_post(
        "/api/accounts/{account_id}/details/refresh",
        handle_refresh_account_details,
    )
    app.router.add_post(
        "/api/accounts/{account_id}/friends/request",
        handle_send_friend_request,
    )
    app.router.add_post(
        "/api/accounts/{account_id}/friend-requests/{user_id}/accept",
        handle_accept_friend_request,
    )
    app.router.add_post(
        "/api/accounts/{account_id}/friend-requests/{user_id}/decline",
        handle_decline_friend_request,
    )
    app.router.add_post(
        "/api/accounts/{account_id}/join-friend",
        handle_join_friend,
    )
    app.router.add_post(
        "/api/accounts/{account_id}/join-vip",
        handle_join_vip,
    )
    app.router.add_post(
        "/api/accounts/{account_id}/cookie",
        handle_copy_cookie,
    )
    app.router.add_delete("/api/accounts/{account_id}", handle_remove_account)
    app.router.add_post("/api/accounts/clear", handle_clear_accounts)
    app.router.add_post("/api/catalog/refresh", handle_refresh_catalog)
    app.router.add_post("/api/presence/refresh", handle_refresh_presence)
    app.router.add_post("/api/scan", handle_scan)
    app.router.add_post("/api/launch", handle_launch)
    app.router.add_get("/open/{nonce}", handle_open_launch)
    return app


def load_config(path: Path) -> Config:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Не найден файл настроек: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Некорректный JSON в {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Корнем config.json должен быть объект.")
    return Config.from_dict(raw)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Локальная панель Roblox Continue")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="путь к config.json",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="не открывать браузер автоматически",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()
    try:
        config = load_config(args.config.resolve())
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2

    print(f"Roblox Account Hub v{APP_VERSION}: http://127.0.0.1:{config.port}")
    print("Cookies хранятся только в памяти процесса. Для остановки нажмите Ctrl+C.")
    app = create_app(
        config,
        open_browser_on_start=False if args.no_browser else None,
    )
    try:
        web.run_app(
            app,
            host=config.host,
            port=config.port,
            access_log=None,
            print=None,
        )
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
