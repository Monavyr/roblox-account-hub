from __future__ import annotations

import asyncio
import re
import unittest
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from app import (
    CONTINUE_TOPIC_ID,
    AccountRecord,
    CatalogGame,
    Config,
    FriendRequestItem,
    GameCandidate,
    InGameFriend,
    RobloxClient,
    STATE_KEY,
    build_follow_user_uri,
    build_launch_uri,
    build_private_server_uri,
    create_app,
    extract_candidates,
    extract_cookies,
    parse_vip_target,
)


COOKIE_A = "_|WARNING:-DO-NOT-SHARE-THIS." + "a" * 80
COOKIE_B = "_|WARNING:-DO-NOT-SHARE-THIS." + "b" * 80


class CookieParsingTests(unittest.TestCase):
    def test_extracts_until_whitespace_and_deduplicates(self) -> None:
        text = f"prefix {COOKIE_A}\n{COOKIE_B} trailing {COOKIE_A}"
        self.assertEqual(extract_cookies(text), [COOKIE_A, COOKIE_B])

    def test_accepts_named_cookie(self) -> None:
        self.assertEqual(
            extract_cookies(f".ROBLOSECURITY={COOKIE_A}; Path=/"),
            [COOKIE_A],
        )

    def test_rejects_unrelated_tokens(self) -> None:
        self.assertEqual(extract_cookies("hello world"), [])


class OmniParsingTests(unittest.TestCase):
    def test_continue_is_selected_by_numeric_topic_id(self) -> None:
        payload = {
            "sorts": [
                {"topicId": 1, "topic": "Continue", "recommendationList": []},
                {
                    "topicId": CONTINUE_TOPIC_ID,
                    "topic": "Продолжить",
                    "recommendationList": [
                        {"contentType": "Game", "contentId": 99}
                    ],
                },
            ],
            "contentMetadata": {
                "Game": {"99": {"universeId": 99, "rootPlaceId": 123}}
            },
        }
        self.assertEqual(extract_candidates(payload)[0].place_id, 123)


class LaunchTests(unittest.TestCase):
    def test_launch_uri_contains_ticket_and_encoded_place(self) -> None:
        uri = build_launch_uri(
            "ticket-value",
            920587237,
            tracker_id="1234567890",
            launch_time_ms=1700000000000,
        )
        self.assertTrue(uri.startswith("roblox-player:1+launchmode:play"))
        self.assertIn("+gameinfo:ticket-value", uri)
        self.assertIn("placeId%3D920587237", uri)
        self.assertIn("+browsertrackerid:1234567890", uri)

    def test_follow_and_private_launch_uris(self) -> None:
        follow = build_follow_user_uri(
            "ticket-value",
            777,
            tracker_id="1234567890",
            launch_time_ms=1700000000000,
        )
        private = build_private_server_uri(
            "ticket-value",
            123,
            link_code="vip-code",
            tracker_id="1234567890",
            launch_time_ms=1700000000000,
        )
        self.assertIn("RequestFollowUser", follow)
        self.assertIn("userId%3D777", follow)
        self.assertIn("RequestPrivateGame", private)
        self.assertIn("placeId%3D123", private)
        self.assertIn("linkCode%3Dvip-code", private)

    def test_parses_full_vip_link(self) -> None:
        target = parse_vip_target(
            "https://www.roblox.com/games/123/Test?privateServerLinkCode=abc123"
        )
        self.assertEqual(
            target,
            {"place_id": 123, "access_code": None, "link_code": "abc123"},
        )
        self.assertIsNone(parse_vip_target("https://example.com/games/123?linkCode=x"))

    def test_parses_new_share_vip_link(self) -> None:
        target = parse_vip_target(
            "https://www.roblox.com/share?code=dc09cb8fdc063342b50edf1cc3fb4ad5&type=Server"
        )
        self.assertEqual(
            target,
            {
                "share_code": "dc09cb8fdc063342b50edf1cc3fb4ad5",
                "share_type": "Server",
            },
        )

    def test_public_account_never_contains_cookie(self) -> None:
        record = AccountRecord(
            account_id="account",
            cookie=COOKIE_A,
            fingerprint="fingerprint",
            user_id=1,
            username="example",
            display_name="Example",
        )
        public = record.public()
        self.assertNotIn("cookie", public)
        self.assertNotIn("fingerprint", public)
        self.assertNotIn(COOKIE_A, repr(public))


class RobloxClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.ticket_calls = 0
        self.friend_post_calls = 0
        self.share_post_calls = 0

        async def ticket(request: web.Request) -> web.Response:
            self.ticket_calls += 1
            if request.headers.get("X-CSRF-TOKEN") != "csrf-value":
                return web.Response(status=403, headers={"X-CSRF-TOKEN": "csrf-value"})
            return web.Response(
                status=200,
                headers={"RBX-Authentication-Ticket": "temporary-ticket"},
            )

        async def names(request: web.Request) -> web.Response:
            return web.json_response(
                {
                    "data": [
                        {"languageCode": "ru", "name": "Русское имя"},
                        {"languageCode": "en-us", "name": "Not exact"},
                        {"languageCode": "en", "name": "English Name"},
                    ]
                }
            )

        async def presences(request: web.Request) -> web.Response:
            body = await request.json()
            return web.json_response(
                {
                    "userPresences": [
                        {
                            "userPresenceType": 2 if user_id == 1 else 1,
                            "lastLocation": "Test Game" if user_id == 1 else "Website",
                            "placeId": 123 if user_id == 1 else None,
                            "universeId": 456 if user_id == 1 else None,
                            "userId": user_id,
                        }
                        for user_id in body["userIds"]
                    ]
                }
            )

        async def friend_post(request: web.Request) -> web.Response:
            self.friend_post_calls += 1
            if request.headers.get("X-CSRF-TOKEN") != "friend-csrf":
                return web.Response(status=403, headers={"X-CSRF-TOKEN": "friend-csrf"})
            return web.json_response({})

        async def users_by_ids(request: web.Request) -> web.Response:
            body = await request.json()
            names = {
                1: ("playing_friend", "Playing Friend"),
                2: ("requester_two", "Requester Two"),
            }
            return web.json_response(
                {
                    "data": [
                        {"id": user_id, "name": names[user_id][0], "displayName": names[user_id][1]}
                        for user_id in body.get("userIds", [])
                        if user_id in names
                    ]
                }
            )

        async def friend_requests(request: web.Request) -> web.Response:
            # Current Friends API can return just user identifiers.
            return web.json_response(
                {"data": [{"id": 2, "created": "2026-08-07T18:00:00Z"}]}
            )

        async def friends(request: web.Request) -> web.Response:
            return web.json_response({"data": [{"id": 1}, {"id": 2}]})

        async def share_resolve(request: web.Request) -> web.Response:
            self.share_post_calls += 1
            if request.headers.get("X-CSRF-TOKEN") != "share-csrf":
                return web.Response(status=403, headers={"X-CSRF-TOKEN": "share-csrf"})
            body = await request.json()
            self.assertEqual(body["linkType"], "Server")
            return web.json_response(
                {
                    "privateServerInviteData": {
                        "status": "Valid",
                        "placeId": 555,
                        "universeId": 999,
                        "linkCode": "resolved-link-code",
                    }
                }
            )

        async def currency(request: web.Request) -> web.Response:
            return web.json_response({"robux": 321})

        async def totals(request: web.Request) -> web.Response:
            if request.query.get("timeFrame") == "AllTime":
                return web.json_response({"errors": [{"message": "Unsupported"}]}, status=400)
            return web.json_response(
                {"outgoingRobuxTotal": -45, "pendingRobuxTotal": 12}
            )

        async def transactions(request: web.Request) -> web.Response:
            if request.query.get("cursor") == "next":
                return web.json_response(
                    {
                        "data": [{"currency": {"amount": -7}}],
                        "nextPageCursor": None,
                    }
                )
            return web.json_response(
                {
                    "data": [
                        {"currency": {"amount": -10}},
                        {"currency": {"amount": -20}},
                    ],
                    "nextPageCursor": "next",
                }
            )

        api_app = web.Application()
        api_app.router.add_post("/ticket", ticket)
        api_app.router.add_get("/names/456", names)
        api_app.router.add_post("/presence", presences)
        api_app.router.add_post("/friend/777", friend_post)
        api_app.router.add_post("/users", users_by_ids)
        api_app.router.add_get("/friend-requests", friend_requests)
        api_app.router.add_get("/friends/1", friends)
        api_app.router.add_post("/share-resolve", share_resolve)
        api_app.router.add_get("/currency", currency)
        api_app.router.add_get("/totals/1", totals)
        api_app.router.add_get("/transactions/1", transactions)
        self.server = TestServer(api_app)
        await self.server.start_server()
        self.session = aiohttp.ClientSession()
        self.roblox = RobloxClient(
            self.session,
            Config(target_place_ids=(920587237,), open_browser=False),
        )

    async def asyncTearDown(self) -> None:
        await self.session.close()
        await self.server.close()

    async def test_authentication_ticket_uses_csrf_retry(self) -> None:
        with patch("app.AUTH_TICKET_URL", str(self.server.make_url("/ticket"))):
            ticket = await self.roblox.authentication_ticket(COOKIE_A, 920587237)
        self.assertEqual(ticket, "temporary-ticket")
        self.assertEqual(self.ticket_calls, 2)

    async def test_english_name_requires_exact_en_code(self) -> None:
        with patch(
            "app.GAME_NAME_URL",
            str(self.server.make_url("/names/")) + "{universe_id}",
        ):
            name = await self.roblox.english_name(456)
        self.assertEqual(name, "English Name")

    async def test_presence_mapping(self) -> None:
        with patch("app.PRESENCE_URL", str(self.server.make_url("/presence"))):
            result = await self.roblox.user_presences([1, 2])
        self.assertEqual(result[1]["presence"], "in_game")
        self.assertEqual(result[1]["place_id"], 123)
        self.assertEqual(result[2]["presence"], "online")

    async def test_authenticated_friend_post_uses_csrf_retry(self) -> None:
        with patch(
            "app.FRIEND_REQUEST_URL",
            str(self.server.make_url("/friend/")) + "{user_id}",
        ):
            await self.roblox.send_friend_request(COOKIE_A, 777)
        self.assertEqual(self.friend_post_calls, 2)

    async def test_friend_names_are_resolved_from_users_api(self) -> None:
        with (
            patch("app.USERS_BY_IDS_URL", str(self.server.make_url("/users"))),
            patch("app.FRIEND_REQUESTS_URL", str(self.server.make_url("/friend-requests"))),
            patch("app.FRIENDS_URL", str(self.server.make_url("/friends/")) + "{user_id}"),
            patch("app.PRESENCE_URL", str(self.server.make_url("/presence"))),
        ):
            requests = await self.roblox.friend_requests(COOKIE_A)
            in_game = await self.roblox.friends_in_game(COOKIE_A, 1)
        self.assertEqual(requests[0].username, "requester_two")
        self.assertEqual(requests[0].display_name, "Requester Two")
        self.assertEqual(in_game[0].username, "playing_friend")
        self.assertEqual(in_game[0].display_name, "Playing Friend")

    async def test_new_share_link_is_resolved_for_selected_cookie(self) -> None:
        link = "https://www.roblox.com/share?code=dc09cb8fdc063342b50edf1cc3fb4ad5&type=Server"
        with patch("app.SHARE_LINK_RESOLVE_URL", str(self.server.make_url("/share-resolve"))):
            target = await self.roblox.resolve_vip_link(link, COOKIE_A)
        self.assertEqual(self.share_post_calls, 2)
        self.assertEqual(target["place_id"], 555)
        self.assertEqual(target["link_code"], "resolved-link-code")

    async def test_finance_uses_purchase_history_for_lifetime_fallback(self) -> None:
        with (
            patch("app.CURRENCY_URL", str(self.server.make_url("/currency"))),
            patch(
                "app.TRANSACTION_TOTALS_URL",
                str(self.server.make_url("/totals/")) + "{user_id}",
            ),
            patch(
                "app.TRANSACTIONS_URL",
                str(self.server.make_url("/transactions/")) + "{user_id}",
            ),
        ):
            result = await self.roblox.account_finance(COOKIE_A, 1)
        self.assertEqual(result["robux_balance"], 321)
        self.assertEqual(result["pending_robux"], 12)
        self.assertEqual(result["year_spent"], 45)
        self.assertEqual(result["lifetime_spent"], 37)


class LocalServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        config = Config(
            target_place_ids=(920587237,),
            open_browser=False,
        )
        self.app = create_app(
            config, start_catalog=False, open_browser_on_start=False
        )
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.friend_actions = []

        outer = self

        class FakeRobloxClient:
            async def authenticated_user(self, cookie: str):
                return {
                    "user_id": 123,
                    "username": "test_account",
                    "display_name": "Test Account",
                }

            async def continue_candidates(self, cookie: str):
                return [GameCandidate(universe_id=456, place_id=920587237)]

            async def user_presences(self, user_ids: list[int]):
                return {
                    user_id: {
                        "presence": "offline",
                        "place_id": None,
                        "universe_id": None,
                        "last_location": None,
                    }
                    for user_id in user_ids
                }

            async def account_finance(self, cookie: str, user_id: int):
                return {
                    "robux_balance": 900,
                    "pending_robux": 50,
                    "lifetime_spent": 12000,
                    "year_spent": 3200,
                }

            async def friend_requests(self, cookie: str):
                return [
                    FriendRequestItem(
                        user_id=888,
                        username="requester",
                        display_name="Requester",
                        created_at="2026-08-05T12:00:00Z",
                    )
                ]

            async def friends_in_game(self, cookie: str, user_id: int):
                return [
                    InGameFriend(
                        user_id=777,
                        username="playing_friend",
                        display_name="Playing Friend",
                        last_location="Test Game",
                        place_id=123,
                        universe_id=456,
                    )
                ]

            async def resolve_username(self, username: str):
                return {
                    "user_id": 999,
                    "username": username.lstrip("@"),
                    "display_name": "Target",
                }

            async def send_friend_request(self, cookie: str, target_user_id: int):
                outer.friend_actions.append(("send", target_user_id))

            async def accept_friend_request(self, cookie: str, requester_user_id: int):
                outer.friend_actions.append(("accept", requester_user_id))

            async def decline_friend_request(self, cookie: str, requester_user_id: int):
                outer.friend_actions.append(("decline", requester_user_id))

            async def resolve_vip_link(self, link: str, cookie: str):
                return {"place_id": 123, "access_code": None, "link_code": "vip"}

            async def authentication_ticket(self, cookie: str, place_id: int | None = None):
                return "temporary-ticket"

        state = self.app[STATE_KEY]
        state.roblox = FakeRobloxClient()
        state.catalog[920587237] = CatalogGame(
            name="Adopt Me!",
            place_id=920587237,
            universe_id=456,
        )

    async def asyncTearDown(self) -> None:
        await self.client.close()

    async def test_index_injects_panel_token(self) -> None:
        response = await self.client.get("/")
        self.assertEqual(response.status, 200)
        body = await response.text()
        token = re.search(r'name="panel-token" content="([^"]+)"', body)
        self.assertIsNotNone(token)
        self.assertNotIn("__PANEL_TOKEN__", body)

    async def test_shared_http_session_does_not_store_account_cookies(self) -> None:
        state = self.app[STATE_KEY]
        self.assertIsNotNone(state.session)
        self.assertIsInstance(state.session.cookie_jar, aiohttp.DummyCookieJar)

    async def test_mutation_requires_panel_token(self) -> None:
        response = await self.client.post("/api/accounts/clear")
        self.assertEqual(response.status, 403)

    async def test_import_scan_and_one_time_launch(self) -> None:
        token = self.app[STATE_KEY].panel_token
        headers = {"X-Panel-Token": token}

        imported = await self.client.post(
            "/api/accounts/import",
            json={"text": COOKIE_A},
            headers=headers,
        )
        self.assertEqual(imported.status, 200)
        import_payload = await imported.json()
        self.assertEqual(import_payload["added"], 1)
        account = import_payload["accounts"][0]
        self.assertNotIn(COOKIE_A, repr(import_payload))

        scanned = await self.client.post("/api/scan", headers=headers)
        self.assertEqual(scanned.status, 200)
        scan_payload = await scanned.json()
        self.assertEqual(scan_payload["accounts"][0]["matches"][0]["name"], "Adopt Me!")

        launched = await self.client.post(
            "/api/launch",
            json={
                "account_id": account["account_id"],
                "place_id": 920587237,
            },
            headers=headers,
        )
        self.assertEqual(launched.status, 200)
        launch_payload = await launched.json()

        redirect = await self.client.get(
            launch_payload["open_url"], allow_redirects=False
        )
        self.assertEqual(redirect.status, 302)
        self.assertTrue(redirect.headers["Location"].startswith("roblox-player:1+"))

        consumed = await self.client.get(
            launch_payload["open_url"], allow_redirects=False
        )
        self.assertEqual(consumed.status, 410)

    async def test_cookie_is_returned_only_by_explicit_copy_endpoint(self) -> None:
        token = self.app[STATE_KEY].panel_token
        headers = {"X-Panel-Token": token}
        imported = await self.client.post(
            "/api/accounts/import",
            json={"text": COOKIE_A},
            headers=headers,
        )
        account = (await imported.json())["accounts"][0]
        account_id = account["account_id"]

        state_response = await self.client.get("/api/state")
        self.assertNotIn(COOKIE_A, await state_response.text())

        copied = await self.client.post(
            f"/api/accounts/{account_id}/cookie",
            headers=headers,
        )
        self.assertEqual(copied.status, 200)
        payload = await copied.json()
        self.assertEqual(payload["cookie"], COOKIE_A)
        self.assertEqual(payload["username"], "test_account")
        self.assertEqual(copied.headers.get("Cache-Control"), "no-store")

    async def test_details_friends_and_quick_launch_endpoints(self) -> None:
        token = self.app[STATE_KEY].panel_token
        headers = {"X-Panel-Token": token}
        imported = await self.client.post(
            "/api/accounts/import",
            json={"text": COOKIE_A},
            headers=headers,
        )
        account = (await imported.json())["accounts"][0]
        account_id = account["account_id"]

        details = await self.client.post(
            f"/api/accounts/{account_id}/details/refresh", headers=headers
        )
        self.assertEqual(details.status, 200)
        detailed = (await details.json())["account"]
        self.assertEqual(detailed["robux_balance"], 900)
        self.assertEqual(detailed["lifetime_spent"], 12000)
        self.assertEqual(detailed["friend_requests"][0]["username"], "requester")
        self.assertEqual(detailed["in_game_friends"][0]["username"], "playing_friend")

        sent = await self.client.post(
            f"/api/accounts/{account_id}/friends/request",
            json={"username": "target_user"},
            headers=headers,
        )
        self.assertEqual(sent.status, 200)
        self.assertIn(("send", 999), self.friend_actions)

        accepted = await self.client.post(
            f"/api/accounts/{account_id}/friend-requests/888/accept",
            headers=headers,
        )
        self.assertEqual(accepted.status, 200)
        self.assertIn(("accept", 888), self.friend_actions)

        friend_launch = await self.client.post(
            f"/api/accounts/{account_id}/join-friend",
            json={"user_id": 777},
            headers=headers,
        )
        self.assertEqual(friend_launch.status, 200)
        friend_payload = await friend_launch.json()
        friend_redirect = await self.client.get(
            friend_payload["open_url"], allow_redirects=False
        )
        self.assertIn("RequestFollowUser", friend_redirect.headers["Location"])

        vip_launch = await self.client.post(
            f"/api/accounts/{account_id}/join-vip",
            json={"link": "https://www.roblox.com/games/123/Test?privateServerLinkCode=vip"},
            headers=headers,
        )
        self.assertEqual(vip_launch.status, 200)
        vip_payload = await vip_launch.json()
        vip_redirect = await self.client.get(
            vip_payload["open_url"], allow_redirects=False
        )
        self.assertIn("RequestPrivateGame", vip_redirect.headers["Location"])

    async def test_each_account_keeps_its_own_continue_order(self) -> None:
        first_place = 920587237
        second_place = 2753915549
        state = self.app[STATE_KEY]
        state.config = Config(
            target_place_ids=(first_place, second_place),
            open_browser=False,
        )
        state.catalog = {
            first_place: CatalogGame(
                name="Adopt Me!",
                place_id=first_place,
                universe_id=456,
            ),
            second_place: CatalogGame(
                name="Blox Fruits",
                place_id=second_place,
                universe_id=994732206,
            ),
        }

        class PerAccountRobloxClient:
            async def authenticated_user(self, cookie: str):
                if cookie == COOKIE_A:
                    await asyncio.sleep(0.03)
                    return {
                        "user_id": 1,
                        "username": "slow_account",
                        "display_name": "Slow",
                    }
                return {
                    "user_id": 2,
                    "username": "fast_account",
                    "display_name": "Fast",
                }

            async def continue_candidates(self, cookie: str):
                if cookie == COOKIE_A:
                    await asyncio.sleep(0.03)
                    return [
                        GameCandidate(universe_id=994732206, place_id=second_place),
                        GameCandidate(universe_id=456, place_id=first_place),
                    ]
                return [GameCandidate(universe_id=456, place_id=first_place)]

            async def user_presences(self, user_ids: list[int]):
                return {
                    user_id: {
                        "presence": "online",
                        "place_id": None,
                        "universe_id": None,
                        "last_location": None,
                    }
                    for user_id in user_ids
                }

            async def authentication_ticket(self, cookie: str, place_id: int):
                return "temporary-ticket"

        state.roblox = PerAccountRobloxClient()
        token = state.panel_token
        headers = {"X-Panel-Token": token}

        imported = await self.client.post(
            "/api/accounts/import",
            json={"text": f"{COOKIE_A}\n{COOKIE_B}"},
            headers=headers,
        )
        self.assertEqual(imported.status, 200)

        scanned = await self.client.post("/api/scan", headers=headers)
        self.assertEqual(scanned.status, 200)
        accounts = (await scanned.json())["accounts"]
        by_name = {account["username"]: account for account in accounts}

        self.assertEqual(
            [game["name"] for game in by_name["slow_account"]["matches"]],
            ["Blox Fruits", "Adopt Me!"],
        )
        self.assertEqual(
            [game["name"] for game in by_name["fast_account"]["matches"]],
            ["Adopt Me!"],
        )


if __name__ == "__main__":
    unittest.main()
