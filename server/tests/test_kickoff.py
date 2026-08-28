"""Tests for the MoQ "don't talk before anyone is listening" gate in bot.py.

RTVI `client-ready` reaches the bot as soon as the browser's relay session is
up (the transcript track replays it), which can be *before* the browser has
subscribed to the bot's audio track. Audio published in that window is lost, so
the kickoff must wait for `AudioProducer.used()`.
"""

import asyncio
from types import SimpleNamespace

import pytest

from bot import _env_float, wait_for_audio_subscriber


class FakeAudioProducer:
    def __init__(self, resolve_after: float | None = 0.0):
        self.resolve_after = resolve_after
        self.used_calls = 0

    async def used(self):
        self.used_calls += 1
        if self.resolve_after is None:
            await asyncio.Event().wait()  # never resolves
        await asyncio.sleep(self.resolve_after)


def _fake_moq_transport(producer):
    from pipecat.transports.moq.transport import MOQTransport

    t = object.__new__(MOQTransport)  # skip __init__; we only need the type + _client
    t._client = SimpleNamespace(_audio_out=producer)
    return t


async def test_waits_until_audio_track_has_a_subscriber():
    producer = FakeAudioProducer(resolve_after=0.05)
    waited = await wait_for_audio_subscriber(_fake_moq_transport(producer), timeout=1.0)
    assert producer.used_calls == 1
    assert waited is True


async def test_gives_up_after_timeout_and_reports_it():
    producer = FakeAudioProducer(resolve_after=None)
    waited = await wait_for_audio_subscriber(_fake_moq_transport(producer), timeout=0.05)
    assert waited is False  # caller proceeds anyway; better late audio than none


async def test_noop_when_audio_track_not_open():
    waited = await wait_for_audio_subscriber(_fake_moq_transport(None), timeout=0.05)
    assert waited is True


async def test_noop_when_transport_has_no_client():
    class NoClient:
        pass

    waited = await wait_for_audio_subscriber(NoClient(), timeout=0.05)
    assert waited is True


@pytest.mark.parametrize("bad", ["abc", "60 # comment", ""])
def test_env_float_tolerates_garbage(monkeypatch, bad):
    monkeypatch.setenv("X_TIMEOUT", bad)
    assert _env_float("X_TIMEOUT", 7.5) == (60.0 if bad.startswith("60") else 7.5)
