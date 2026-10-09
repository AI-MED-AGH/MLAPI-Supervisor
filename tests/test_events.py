import asyncio
import time

from app.events import EventBus


class FakeNotifier:
    def __init__(self, delay=None, fail=False):
        self.calls = []
        self.delay = delay
        self.fail = fail

    async def notify(self, session, event, payload):
        if self.delay is not None:
            await self.delay.wait()
        if self.fail:
            raise RuntimeError("observer exploded")
        self.calls.append((event, payload))


async def test_payload_shape(sessionmaker):
    notifier = FakeNotifier()
    bus = EventBus(sessionmaker, notifier)
    bus.emit("deploy.succeeded", "ecg", digest="sha256:abc", status="ready", reason="ok")
    await bus.drain()
    event, payload = notifier.calls[0]
    assert event == "deploy.succeeded"
    assert payload["event"] == "deploy.succeeded"
    assert payload["model"] == "ecg"
    assert payload["digest"] == "sha256:abc"
    assert payload["status"] == "ready"
    assert payload["details"] == {"reason": "ok"}
    assert abs(payload["timestamp"] - time.time()) < 5


async def test_optional_fields_default_to_none(sessionmaker):
    notifier = FakeNotifier()
    bus = EventBus(sessionmaker, notifier)
    bus.emit("model.removed", "ecg")
    await bus.drain()
    payload = notifier.calls[0][1]
    assert payload["digest"] is None and payload["status"] is None and payload["details"] == {}


async def test_emit_does_not_wait_for_slow_observers(sessionmaker):
    gate = asyncio.Event()
    notifier = FakeNotifier(delay=gate)
    bus = EventBus(sessionmaker, notifier)
    bus.emit("model.woke", "ecg")          # returns immediately
    assert notifier.calls == []
    gate.set()
    await bus.drain()
    assert len(notifier.calls) == 1


async def test_failing_notifier_never_breaks_emit_or_drain(sessionmaker):
    bus = EventBus(sessionmaker, FakeNotifier(fail=True))
    bus.emit("deploy.failed", "ecg")
    await bus.drain()  # must not raise


async def test_drain_with_nothing_pending(sessionmaker):
    await EventBus(sessionmaker, FakeNotifier()).drain()
