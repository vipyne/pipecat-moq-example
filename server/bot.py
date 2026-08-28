#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""pipecat-moq-example - a Pipecat voice agent over Media over QUIC (MoQ).

Cascade pipeline: Speech-to-Text → LLM → Text-to-Speech, with MoQ as the transport.
The bot dials a MoQ relay in client mode; the browser dials the same relay and the two
rendezvous on a per-session namespace.

Required AI services:
- Deepgram (Speech-to-Text)
- OpenAI (LLM) — or Anthropic, see the commented block in run_bot()
- Cartesia (Text-to-Speech)

Run locally with the Pipecat dev runner (serves the prebuilt UI at http://localhost:7860)::

    uv run bot.py --moq-connect https://cdn.moq.dev/anon

On Pipecat Cloud the platform's ``/start`` endpoint doesn't know MoQ, so a session is
started with ``body.moq = {namespace, relayUrl, clientId, botId}`` (see ``client/proxy.py``);
the bot reads that block and dials the relay itself.
"""

import asyncio
import os
from typing import cast

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import MOQRunnerArguments, RunnerArguments
from pipecat.runner.utils import create_transport

# from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.moq.transport import MOQParams, MOQTransport
from pipecat.workers.runner import WorkerRunner

load_dotenv(override=True)


def _env_float(name: str, default: float) -> float:
    """Read a float env var, tolerating inline comments/whitespace and bad values.

    Secrets uploaded from a .env file can carry an inline ``# comment`` verbatim
    (python-dotenv strips it locally; Pipecat Cloud does not).
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    token = raw.split("#", 1)[0].strip()
    try:
        return float(token)
    except ValueError:
        logger.warning(f"{name}={raw!r} is not a number; using default {default}")
        return default


async def wait_for_audio_subscriber(transport: BaseTransport, timeout: float) -> bool:
    """Block until the browser has subscribed to the bot's audio track.

    RTVI ``client-ready`` arrives as soon as the browser's relay session is up —
    the transcript track is a replayed append-log — which can be before the
    browser has discovered the bot's broadcast and subscribed to its audio.
    ``MOQTransport``'s ``on_client_connected`` doesn't help either: it fires when
    the *peer's* broadcast is announced, not when the peer subscribes to ours.
    Audio published in that window is dropped (live media, no replay), so the
    intro's text would show up in the UI with no sound. ``AudioProducer.used()``
    resolves once at least one subscriber is attached.

    Returns True when it's safe to speak (subscriber attached, or nothing to
    wait for), False if we gave up after ``timeout`` and are proceeding anyway.
    """
    # pipecat doesn't expose the audio producer publicly; reach through the
    # transport client deliberately and degrade to a no-op if the shape changes.
    producer = getattr(getattr(transport, "_client", None), "_audio_out", None)
    if producer is None:
        return True
    started = asyncio.get_running_loop().time()
    try:
        await asyncio.wait_for(producer.used(), timeout=timeout)
    except TimeoutError:
        logger.warning(
            f"MoQ: no subscriber on the bot audio track after {timeout:.0f}s; speaking anyway"
        )
        return False
    waited_ms = (asyncio.get_running_loop().time() - started) * 1000
    logger.info(f"MoQ: audio subscriber attached after {waited_ms:.0f}ms; safe to speak")
    return True


def _moq_params() -> MOQParams:
    """MoQ transport params shared by the dev-runner and Pipecat Cloud paths."""
    return MOQParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        # How long to wait for the browser's broadcast to show up at the relay
        # before giving up. Generous, to cover Pipecat Cloud cold starts.
        connection_timeout=_env_float("MOQ_CONNECTION_TIMEOUT", 60.0),
    )


# Transport-specific parameters, consumed by pipecat.runner.utils.create_transport
transport_params = {
    "moq": _moq_params,
}


async def run_bot(transport: MOQTransport, runner_args: RunnerArguments):
    """Main bot logic."""
    logger.info(f"Starting bot with {type(transport).__name__}")

    # Speech-to-Text service
    stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"])

    # Text-to-Speech service
    tts = CartesiaTTSService(
        api_key=os.environ["CARTESIA_API_KEY"],
        settings=CartesiaTTSService.Settings(voice="86e30c1d-714b-4074-a1f2-1cb6b552fb49"),
    )

    # LLM service — OpenAI (active) or Anthropic (commented out).
    system_instruction = "You are a helpful assistant in a voice conversation. Your responses will be spoken aloud, so avoid emojis, bullet points, or other formatting that can't be spoken. Respond to what the user said in a creative, helpful, and brief way."

    llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        settings=OpenAILLMService.Settings(
            system_instruction=system_instruction,
        ),
    )

    # llm = AnthropicLLMService(
    #     api_key=os.getenv("ANTHROPIC_API_KEY"),
    #     settings=AnthropicLLMService.Settings(
    #         system_instruction=system_instruction,
    #     ),
    # )

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    # Pipeline - assembled from reusable components
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=getattr(runner_args, "pipeline_idle_timeout_secs", None),
    )

    @worker.rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        # Kick off the conversation once the browser is ready — but first make
        # sure someone is actually subscribed to our audio track (see
        # wait_for_audio_subscriber for why RTVI client-ready isn't enough).
        await wait_for_audio_subscriber(
            transport, timeout=_env_float("MOQ_SUBSCRIBER_TIMEOUT", 15.0)
        )
        context.add_message(
            {"role": "developer", "content": "Start by concisely introducing yourself."}
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport):
        logger.info("Client connected")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport):
        logger.info("Client disconnected")
        await worker.cancel()

    # INFO-level breadcrumbs: the transport's own connect/publish logs are DEBUG,
    # which Pipecat Cloud doesn't show by default.
    @transport.event_handler("on_connected")
    async def on_connected(transport):
        p = transport._params
        logger.info(
            f"MoQ: connected to relay {p.relay_url}; publishing "
            f"{p.namespace}/{p.participant_id}, waiting for {p.namespace}/{p.peer_id} "
            f"(up to {p.connection_timeout:.0f}s)"
        )

    @transport.event_handler("on_track_subscribed")
    async def on_track_subscribed(transport, track):
        logger.info(f"MoQ: subscribed to peer track {track!r}")

    @transport.event_handler("on_disconnected")
    async def on_disconnected(transport):
        logger.info("Disconnected from MoQ relay")
        await worker.cancel()

    @transport.event_handler("on_error")
    async def on_error(transport, message, exception):
        logger.error(f"MoQ transport error: {message} ({exception!r})")

    runner = WorkerRunner(handle_sigint=getattr(runner_args, "handle_sigint", False))

    await runner.add_workers(worker)
    try:
        await runner.run()
    finally:
        await transport.disconnect()


def _moq_transport_from_body(body: dict) -> MOQTransport:
    """Build a client-mode MoQ transport from a Pipecat Cloud session body.

    ``body["moq"]`` is written by the client (``client/proxy.py``) and carries the
    per-session namespace both sides rendezvous on at the relay.
    """
    moq = body["moq"]
    params = _moq_params()
    params.relay_url = moq.get("relayUrl") or os.environ["MOQ_RELAY_URL"]
    params.namespace = moq["namespace"]
    params.participant_id = moq.get("botId", params.participant_id)
    params.peer_id = moq.get("clientId", params.peer_id)
    logger.info(
        f"MoQ client mode: relay={params.relay_url} namespace={params.namespace} "
        f"publish={params.participant_id} subscribe={params.peer_id}"
    )
    # host/port/path are ignored when relay_url is set.
    return MOQTransport(params=params)


async def bot(runner_args: RunnerArguments):
    """Main bot entry point."""

    body = getattr(runner_args, "body", None) or {}

    if isinstance(runner_args, MOQRunnerArguments):
        # Dev runner: `uv run bot.py --moq-connect <relay>` (or `-t moq` serve mode).
        # create_transport is typed BaseTransport; with only a "moq" entry in
        # transport_params it can only ever hand back an MOQTransport.
        transport = cast(MOQTransport, await create_transport(runner_args, transport_params))
    elif isinstance(body.get("moq"), dict):
        # Pipecat Cloud session: rendezvous info arrives in body.moq.
        transport = _moq_transport_from_body(body)
    else:
        logger.error(
            f"Unsupported runner arguments type: {type(runner_args)} (body keys: {list(body)})"
        )
        return

    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
