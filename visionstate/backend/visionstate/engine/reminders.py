"""The review reminder: a notification in Home Assistant (and, if chosen, a push to a phone) when
frames have waited for review a long time — never when a frame arrives. It goes away by itself
once the queue is handled."""

from __future__ import annotations

import asyncio
import logging
import time

from ..redact import redact
from ..settings import REMINDER, merge_reminder
from .logic import reminder_action, reminder_text
from .publishing import PublishingMixin

log = logging.getLogger(__name__)


class RemindersMixin(PublishingMixin):
    async def _reminder_loop(self) -> None:
        """Looks at the queue every REMINDER["check_interval_s"], and right after it changed."""
        last_error = ""
        while True:
            self._reminder_wanted.clear()
            try:
                await self.check_reminder()
                last_error = ""
            except Exception as err:  # noqa: BLE001 - Home Assistant restarting: try again next time
                error = redact(str(err) or type(err).__name__)
                if error != last_error:
                    log.warning("Could not send the review reminder: %s", error)
                last_error = error
            try:
                await asyncio.wait_for(self._reminder_wanted.wait(), timeout=REMINDER["check_interval_s"])
            except TimeoutError:
                pass

    def reminder_settings(self) -> dict:
        return merge_reminder(self.reminder_rules)

    def set_reminder(self, rules: dict) -> None:
        self.reminder_rules = merge_reminder(rules)
        self.db.set_setting("reminder", self.reminder_rules)
        self._reminder_wanted.set()  # turned off or made later: clear it now; earlier: send it now

    def _set_sent_at(self, sent_at: float | None) -> None:
        self.reminder_sent_at = sent_at
        self.db.set_setting("reminder_sent_at", sent_at)

    async def check_reminder(self) -> str | None:
        """Send or clear the reminder when it is time; returns what was done ("send", "clear")."""
        if not self.ha.enabled:
            return None
        rules = self.reminder_settings()
        waiting, _, oldest = await asyncio.to_thread(self._review_counts)
        now = time.time()
        age = now - oldest.timestamp() if oldest else None
        action = reminder_action(rules, waiting, age, self.reminder_sent_at, now)
        if action == "send" and age is not None:
            await self.send_reminder(rules, waiting, age, REMINDER["notification_id"])
            await asyncio.to_thread(self._set_sent_at, now)
            log.info("Reminded of %d frames waiting for review", waiting)
        elif action == "clear":
            await self.ha.call_service(
                "persistent_notification", "dismiss", {"notification_id": REMINDER["notification_id"]}
            )
            await asyncio.to_thread(self._set_sent_at, None)
        return action

    async def test_reminder(self, rules: dict) -> str:
        """A reminder as it would look now, as its own notification (it is not cleared by itself)."""
        waiting, _, oldest = await asyncio.to_thread(self._review_counts)
        age = time.time() - oldest.timestamp() if oldest else 0.0
        return await self.send_reminder(merge_reminder(rules), waiting, age, REMINDER["test_notification_id"])

    async def send_reminder(self, rules: dict, waiting: int, age_s: float, notification_id: str) -> str:
        """The notification in Home Assistant, then the push when one is chosen.

        A failing push is logged, not raised: the notification is there, and trying again every
        hour would put it up again each time. Returns the push's error ("" = sent or none chosen).
        """
        text = reminder_text(waiting, age_s)
        link = await self._app_link()
        message = f"{text}\n\n[Open VisionState]({link})" if link else text
        await self.ha.call_service(
            "persistent_notification",
            "create",
            {"title": REMINDER["title"], "message": message, "notification_id": notification_id},
        )
        service = rules["notify_service"]
        if not service:
            return ""
        data: dict = {"title": REMINDER["title"], "message": text}
        if link:
            data["data"] = {"url": link, "clickAction": link}  # tapping it opens the app (iOS, Android)
        try:
            domain, name = service.split(".", 1)
            await self.ha.call_service(domain, name, data)
        except Exception as err:  # noqa: BLE001
            error = redact(str(err) or type(err).__name__)
            log.warning("Could not push the review reminder with %s: %s", service, error)
            return error
        return ""

    async def _app_link(self) -> str | None:
        try:
            return await self.ha.app_path()
        except Exception as err:  # noqa: BLE001 - a reminder without a link is still a reminder
            log.debug("No link to the app: %s", err)
            return None
