"""The review reminder: when it is sent, cleared and repeated, what it says, its settings and the
Home Assistant calls behind it."""

import asyncio
import contextlib
import time
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from visionstate.db import Prediction, utcnow
from visionstate.engine.logic import DAY_S, reminder_action, reminder_text
from visionstate.main import create_app
from visionstate.mqtt import REVIEW_TOPICS
from visionstate.settings import REMINDER, REMINDER_DEFAULTS, Settings, merge_reminder
from visionstate.sources import HomeAssistant, SourceError, ha_version

from .conftest import MODEL_DIR
from .test_publish import RecordingMqtt

RULES = merge_reminder(None)
NOW = 1_800_000_000.0


def test_defaults():
    assert RULES["enabled"] is True and RULES["repeat"] is False
    assert RULES["notify_service"] == "" and RULES["min_items"] == 1 and RULES["after_days"] == 7
    assert merge_reminder({"after_days": 3, "unknown": 1}) == {**REMINDER_DEFAULTS, "after_days": 3}


def test_reminder_is_sent_once_per_round_and_cleared_when_handled():
    week = 7 * DAY_S
    assert reminder_action(RULES, 0, None, None, NOW) is None  # nothing waits
    assert reminder_action(RULES, 5, week - 60, None, NOW) is None  # not long enough yet
    assert reminder_action(RULES, 5, week, None, NOW) == "send"
    assert reminder_action(RULES, 5, 30 * DAY_S, NOW - 20 * DAY_S, NOW) is None  # sent this round: no repeat
    assert reminder_action(RULES, 0, None, NOW - DAY_S, NOW) == "clear"  # the queue was handled
    assert reminder_action(RULES, 3, DAY_S, NOW - DAY_S, NOW) == "clear"  # only newer frames are left
    assert reminder_action({**RULES, "enabled": False}, 5, week, NOW - DAY_S, NOW) == "clear"
    assert reminder_action({**RULES, "enabled": False}, 5, week, None, NOW) is None
    few = {**RULES, "min_items": 10}
    assert reminder_action(few, 9, week, None, NOW) is None
    assert reminder_action(few, 10, week, None, NOW) == "send"


def test_reminder_repeats_only_when_asked():
    rules = {**RULES, "repeat": True, "repeat_days": 3}
    assert reminder_action(rules, 2, 9 * DAY_S, NOW - 2 * DAY_S, NOW) is None
    assert reminder_action(rules, 2, 9 * DAY_S, NOW - 3 * DAY_S, NOW) == "send"


def test_ha_version():
    assert ha_version("2026.10.0b1") == (2026, 10) and ha_version("2025.10.4") == (2025, 10)
    assert ha_version("dev") == (0, 0)


def test_reminder_text():
    assert (
        reminder_text(11, 8.5 * DAY_S)
        == "There are 11 frames waiting for review in VisionState, the oldest for 8 days."
    )
    assert reminder_text(1, DAY_S) == "There is 1 frame waiting for review in VisionState, for 1 day."
    assert reminder_text(0, 0.0) == "No frames are waiting for review in VisionState right now."  # a test


class NotifyHa:
    """Home Assistant stand-in that records service calls."""

    enabled = True

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.fail: set[str] = set()  # "domain.service" that answer with an error

    async def call_service(self, domain, service, data):
        if f"{domain}.{service}" in self.fail:
            raise SourceError(f"{domain}.{service}: HTTP 400")
        self.calls.append((domain, service, data))

    async def notify_services(self):
        return ["notify.mobile_app_pixel", "notify.family"]

    async def app_path(self):
        return "/hassio/ingress/abc_visionstate"

    async def close(self):
        pass


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data", media_dir=tmp_path / "media", frontend_dir=tmp_path, bundled_models_dir=MODEL_DIR
    )
    with TestClient(create_app(settings)) as client:
        rt = client.app.state.runtime
        rt.mqtt = RecordingMqtt(rt.mqtt)
        # The tests look at the reminder themselves; test_the_loop_… starts the loop again.
        for task in list(rt._background):
            if task.get_coro().__name__ == "_reminder_loop":
                client.portal.call(stop, task)
        body = {
            "name": "Door",
            "source_type": "http",
            "source": "http://cam/snap.jpg",
            "states": [{"name": "Open"}, {"name": "Closed"}],
            "enabled": False,
        }
        sid = client.post("/api/v1/sensors", json=body).json()["id"]
        yield client, rt, sid


def add_waiting(rt, sensor_id: int, ages_days: list[float]) -> list[int]:
    ids = []
    with rt.db.session() as s:
        for age in ages_days:
            row = Prediction(
                sensor_id=sensor_id,
                created_at=utcnow() - timedelta(days=age),
                state_key="open",
                published_key="open",
                confidence=0.5,
                probs={},
                is_change=False,
                review_reason="low_confidence",
                reviewed=False,
            )
            s.add(row)
            s.flush()
            ids.append(row.id)
    return ids


def test_reminder_in_home_assistant(app):
    client, rt, sid = app
    ha = rt.ha = NotifyHa()
    check = lambda: client.portal.call(rt.check_reminder)  # noqa: E731
    ids = add_waiting(rt, sid, [8, 2, 1])
    assert check() == "send"
    domain, service, data = ha.calls[-1]
    assert (domain, service) == ("persistent_notification", "create")
    assert data["notification_id"] == REMINDER["notification_id"]
    assert data["message"].startswith("There are 3 frames waiting for review in VisionState, the oldest for 8 days.")
    assert "(/hassio/ingress/abc_visionstate)" in data["message"]
    assert len(ha.calls) == 1  # no push unless chosen
    assert check() is None  # once per round
    assert client.get("/api/v1/review-reminder").json()["sent_at"] is not None

    # The oldest frame is answered: only newer ones are left, so the reminder goes away.
    with rt.db.session() as s:
        s.get(Prediction, ids[0]).reviewed = True
    assert check() == "clear"
    assert ha.calls[-1] == ("persistent_notification", "dismiss", {"notification_id": REMINDER["notification_id"]})
    assert client.get("/api/v1/review-reminder").json()["sent_at"] is None
    assert check() is None


def test_push_to_a_phone_and_a_failing_push(app):
    client, rt, sid = app
    ha = rt.ha = NotifyHa()
    add_waiting(rt, sid, [10])
    client.put("/api/v1/review-reminder", json={**REMINDER_DEFAULTS, "notify_service": "notify.mobile_app_pixel"})
    assert client.portal.call(rt.check_reminder) == "send"
    assert [c[:2] for c in ha.calls] == [("persistent_notification", "create"), ("notify", "mobile_app_pixel")]
    push = ha.calls[-1][2]
    assert push["message"] == "There is 1 frame waiting for review in VisionState, for 10 days."
    assert push["data"] == {"url": "/hassio/ingress/abc_visionstate", "clickAction": "/hassio/ingress/abc_visionstate"}

    # A push that fails does not put the reminder up again every hour.
    client.portal.call(rt._set_sent_at, None)
    ha.calls.clear()
    ha.fail.add("notify.mobile_app_pixel")
    assert client.portal.call(rt.check_reminder) == "send"
    assert client.portal.call(rt.check_reminder) is None


def test_repeat_and_turning_it_off(app):
    client, rt, sid = app
    ha = rt.ha = NotifyHa()
    add_waiting(rt, sid, [9])
    client.put("/api/v1/review-reminder", json={**REMINDER_DEFAULTS, "repeat": True, "repeat_days": 2})
    assert client.portal.call(rt.check_reminder) == "send"
    client.portal.call(rt._set_sent_at, time.time() - 2 * DAY_S)
    assert client.portal.call(rt.check_reminder) == "send"
    client.put("/api/v1/review-reminder", json={**REMINDER_DEFAULTS, "enabled": False})
    assert client.portal.call(rt.check_reminder) == "clear"
    assert ha.calls[-1][1] == "dismiss"


def test_a_failing_notification_is_tried_again(app):
    client, rt, sid = app
    ha = rt.ha = NotifyHa()
    add_waiting(rt, sid, [9])
    ha.fail.add("persistent_notification.create")
    with pytest.raises(SourceError):
        client.portal.call(rt.check_reminder)
    ha.fail.clear()
    assert client.portal.call(rt.check_reminder) == "send"


def test_the_loop_looks_when_the_queue_changes(app):
    client, rt, sid = app
    ha = rt.ha = NotifyHa()

    async def start():
        rt._spawn(rt._reminder_loop())
        await asyncio.sleep(0.2)  # its first look: nothing waits; the next one is an hour away

    client.portal.call(start)
    assert ha.calls == []
    add_waiting(rt, sid, [9])
    client.portal.call(rt.publish_review_count)  # what every change of the queue calls: it looks again

    async def wait_for_call():
        for _ in range(100):
            if ha.calls:
                return True
            await asyncio.sleep(0.05)
        return False

    assert client.portal.call(wait_for_call)
    assert ha.calls[0][:2] == ("persistent_notification", "create")


def test_review_queue_entity_tells_how_old_the_oldest_frame_is(app):
    client, rt, sid = app
    add_waiting(rt, sid, [3, 1])
    client.portal.call(rt.publish_review_count)
    attributes = rt.mqtt.sent[REVIEW_TOPICS["attributes"]]
    assert attributes["per_sensor"] == {"Door": 2}
    oldest = attributes["oldest_waiting_since"]
    assert oldest.endswith("+00:00")
    assert abs((utcnow() - timedelta(days=3)) - datetime.fromisoformat(oldest)) < timedelta(minutes=1)


def test_settings_api(app):
    client, rt, _ = app
    config = client.get("/api/v1/config").json()
    assert config["reminder_defaults"] == REMINDER_DEFAULTS and "after_days" in config["reminder_limits"]
    assert client.get("/api/v1/review-reminder").json() == {**REMINDER_DEFAULTS, "sent_at": None}
    saved = client.put("/api/v1/review-reminder", json={**REMINDER_DEFAULTS, "after_days": 3, "min_items": 5}).json()
    assert saved["after_days"] == 3 and saved["min_items"] == 5
    assert client.get("/api/v1/review-reminder").json()["after_days"] == 3
    assert rt.db.get_dict("reminder")["min_items"] == 5
    for bad in ({"after_days": 0}, {"min_items": 0}, {"repeat_days": 1000}, {"notify_service": "light.kitchen"}):
        assert client.put("/api/v1/review-reminder", json={**REMINDER_DEFAULTS, **bad}).status_code == 422


def test_test_reminder_and_notify_services(app):
    client, rt, sid = app
    assert client.post("/api/v1/review-reminder/test", json=REMINDER_DEFAULTS).status_code == 409  # no Home Assistant
    assert client.get("/api/v1/notify-services").json() == []
    ha = rt.ha = NotifyHa()
    assert client.get("/api/v1/notify-services").json() == ["notify.mobile_app_pixel", "notify.family"]
    add_waiting(rt, sid, [2])
    body = {**REMINDER_DEFAULTS, "notify_service": "notify.family"}
    assert client.post("/api/v1/review-reminder/test", json=body).json() == {"push_error": ""}
    assert ha.calls[0][2]["notification_id"] == REMINDER["test_notification_id"]  # not the real one
    assert ha.calls[1][:2] == ("notify", "family")
    assert rt.reminder_sent_at is None  # a test is no round
    ha.fail.add("notify.family")
    assert client.post("/api/v1/review-reminder/test", json=body).json()["push_error"] == "notify.family: HTTP 400"
    ha.fail.add("persistent_notification.create")
    assert client.post("/api/v1/review-reminder/test", json=body).status_code == 502


def test_home_assistant_client(tmp_path):
    version = {"v": "2026.10.0"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/core/api/services" and request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"domain": "light", "services": {"turn_on": {}}},
                    {
                        "domain": "notify",
                        "services": {
                            "persistent_notification": {},
                            "send_message": {},
                            "family": {},
                            "mobile_app_pixel": {},
                        },
                    },
                ],
            )
        if request.url.path == "/addons/self/info":
            return httpx.Response(200, json={"result": "ok", "data": {"slug": "abc_visionstate"}})
        if request.url.path == "/core/api/config":
            return httpx.Response(200, json={"version": version["v"]})
        if request.url.path == "/core/api/services/notify/broken":
            return httpx.Response(400)
        return httpx.Response(200, json=[])

    async def scenario():
        settings = Settings(
            data_dir=tmp_path,
            media_dir=tmp_path,
            frontend_dir=tmp_path,
            bundled_models_dir=tmp_path,
            supervisor_token="t",
        )
        ha = HomeAssistant(settings)
        ha._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        assert await ha.notify_services() == ["notify.mobile_app_pixel", "notify.family"]
        assert await ha.app_path() == "/app/abc_visionstate"  # Home Assistant 2026.2 and later
        ha._app_path, version["v"] = None, "2026.1.3"
        assert await ha.app_path() == "/hassio/ingress/abc_visionstate"
        await ha.call_service("notify", "family", {"message": "hi"})
        with pytest.raises(SourceError, match="notify.broken: HTTP 400"):
            await ha.call_service("notify", "broken", {"message": "hi"})
        await ha.close()

        local = HomeAssistant(
            Settings(data_dir=tmp_path, media_dir=tmp_path, frontend_dir=tmp_path, bundled_models_dir=tmp_path)
        )
        assert await local.app_path() is None and await local.notify_services() == []
        with pytest.raises(SourceError):
            await local.call_service("notify", "family", {})
        await local.close()

    asyncio.run(scenario())
