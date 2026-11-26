"""The MQTT bridge against a broker that answers a connect too late or never.

aiomqtt 2.5.1 left the connection of every attempt that timed out open (aiomqtt issue 409): a broker
on a host under pressure gathered one more connection with our client ID and last will per retry.
The bridge closes it (mqtt._close_unconnected); this checks the behaviour, so it keeps holding after
aiomqtt is fixed or replaced.
"""

import asyncio

import pytest

from visionstate import mqtt
from visionstate.settings import Settings

CONNACK = b"\x20\x02\x00\x00"  # MQTT 3.1.1: connection accepted


class LateBroker:
    """Accepts TCP, reads the CONNECT, and answers CONNACK after ``delay`` seconds (if the client
    is still there). Counts the connections that are open and that were made."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.open: set = set()
        self.made = 0

    async def _until_closed(self, reader: asyncio.StreamReader) -> None:
        while await reader.read(1024):
            pass

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.made += 1
        self.open.add(writer)
        try:
            await reader.read(2)
            try:
                await asyncio.wait_for(self._until_closed(reader), self.delay)
                return  # the client went away before the answer
            except TimeoutError:
                pass
            writer.write(CONNACK)
            await writer.drain()
            await self._until_closed(reader)
        except OSError:
            pass
        finally:
            self.open.discard(writer)
            writer.close()


@pytest.mark.parametrize("delay", [0.6, 1000.0], ids=["answers late", "never answers"])
def test_failed_connects_leave_no_connection_open(tmp_path, monkeypatch, delay):
    monkeypatch.setattr(mqtt, "CONNECT_TIMEOUT_S", 0.2)
    monkeypatch.setattr(mqtt, "RECONNECT_DELAY_S", 0.3)

    async def run() -> tuple[int, int, str]:
        broker = LateBroker(delay)
        server = await asyncio.start_server(broker.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        settings = Settings(
            data_dir=tmp_path,
            media_dir=tmp_path,
            frontend_dir=tmp_path,
            bundled_models_dir=tmp_path,
            mqtt_host="127.0.0.1",
            mqtt_port=port,
        )

        async def nothing(*_args) -> None:
            return None

        bridge = mqtt.MqttBridge(settings, nothing, nothing)
        bridge.start()
        while broker.made < 4:  # four attempts, each timed out
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.25)  # the last one timed out too (0.2 s) and is closing
        left, made, error = len(broker.open), broker.made, bridge.last_error
        await bridge.stop()
        server.close()
        return left, made, error

    left, made, error = asyncio.run(asyncio.wait_for(run(), 30), loop_factory=asyncio.SelectorEventLoop)
    assert made >= 4 and error  # it did retry, and says why it is not connected
    assert left <= 1, f"{left} connections left open after {made} attempts"  # at most the attempt under way
