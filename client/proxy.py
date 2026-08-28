#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Local ``/start`` proxy that serves the web UI against a Pipecat Cloud MoQ bot.

Why this exists
---------------
The prebuilt UI hardcodes ``POST /start`` on its own origin and expects the
*dev runner's* response shape. Pipecat Cloud's ``/start`` only understands
``transport: daily|webrtc|websocket`` and, for anything else, returns just
``{"sessionId": ...}``. For MoQ the browser and the bot must rendezvous on a
shared namespace at the relay, so *something* on the client side has to mint
that namespace, hand it to the bot (via ``body.moq``) and hand the UI a ``moq``
config block. That something is this proxy.

- ``POST /start`` with ``transport: "moq"`` -> we mint ``pipecat-<hex>`` (or honor
  a caller-supplied ``namespace``), start the bot on Pipecat Cloud with
  ``body.moq = {...}`` and return the same ``moq`` block the dev runner would.
- serves ``ui/dist`` (the MoQ-only console in ``ui/``) at ``/client``; falls back
  to the stock ``pipecat-ai-prebuilt`` package if the UI isn't built.
- optional ``ACCESS_KEY``: when set, every route except ``/healthz`` requires it.
  Open the UI as ``/client/?key=<ACCESS_KEY>`` once; the key is exchanged for a
  cookie (and stripped from the URL) so the SPA's later requests just work. The
  UI also sends it as ``X-Access-Key``. Meant for keeping a public demo deployment
  from being a free relay to your Pipecat Cloud agent — not a security boundary.

Run::

    uv run proxy.py            # http://localhost:7861
"""

import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

load_dotenv(override=True)

# Participant ids must match the bot's MOQParams defaults (participant_id="response",
# peer_id="request"); the JS transport's own defaults are client0/bot0, so we are explicit.
MOQ_BOT_ID = "response"
MOQ_CLIENT_ID = "request"
MOQ_TRANSCRIPT_TRACK = "transcript.json.z"

PCC_API_BASE = "https://api.pipecat.daily.co/v1/public"
ACCESS_COOKIE = "access_key"
UI_DIST = Path(__file__).parent / "ui" / "dist"


def _new_session_namespace() -> str:
    """Unguessable per-session namespace, same shape as the dev runner's."""
    return f"pipecat-{secrets.token_hex(8)}"


class Settings:
    """Runtime configuration, read from the environment at construction."""

    def __init__(self) -> None:
        self.agent_name = os.getenv("PCC_AGENT_NAME", "")
        self.public_api_key = os.getenv("PCC_PUBLIC_API_KEY", "")
        self.moq_relay_url = os.getenv("MOQ_RELAY_URL", "")
        self.start_url = os.getenv("PCC_START_URL") or f"{PCC_API_BASE}/{self.agent_name}/start"
        self.access_key = os.getenv("ACCESS_KEY") or None

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("PCC_AGENT_NAME", self.agent_name),
                ("PCC_PUBLIC_API_KEY", self.public_api_key),
                ("MOQ_RELAY_URL", self.moq_relay_url),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
        relay_path = urlparse(self.moq_relay_url).path.strip("/")
        if not relay_path:
            # Both the bot and the browser dial exactly this URL; the path is the
            # relay's root for publishing/subscribing (e.g. `/anon` on moq-relay).
            # Without it the two sides land in different roots and never meet.
            raise RuntimeError(
                f"MOQ_RELAY_URL={self.moq_relay_url!r} has no path. Include the relay's "
                "publish root, e.g. https://cdn.moq.dev/anon"
            )


async def pcc_start(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to Pipecat Cloud's public /start endpoint and return its JSON."""
    headers = {
        "Authorization": f"Bearer {settings.public_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(settings.start_url, json=payload, headers=headers)
    if resp.status_code >= 400:
        # Surface Pipecat Cloud's error verbatim so the UI can show it.
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp.json()


def build_start_payload(request_data: dict[str, Any], settings: Settings) -> tuple[str, dict]:
    """Translate a dev-runner-style /start request into a Pipecat Cloud /start payload.

    Returns ``(transport, pcc_payload)``. Only ``transport: "moq"`` is supported.
    """
    transport = request_data.get("transport")
    body = dict(request_data.get("body") or {})

    if transport == "moq":
        namespace = request_data.get("namespace") or _new_session_namespace()
        body["moq"] = {
            "namespace": namespace,
            "relayUrl": settings.moq_relay_url,
            "clientId": MOQ_CLIENT_ID,
            "botId": MOQ_BOT_ID,
        }
        # No `transport` key: Pipecat Cloud doesn't know MoQ and would reject it.
        return transport, {"body": body}

    raise HTTPException(
        status_code=400,
        detail=f"Unsupported transport {transport!r}; this proxy serves 'moq' only.",
    )


def build_moq_client_config(namespace: str, settings: Settings) -> dict[str, Any]:
    """The ``moq`` block the prebuilt UI / MoqTransport expects from /start."""
    return {
        "relayUrl": settings.moq_relay_url,
        "certHash": None,  # public TLS on the relay; nothing to pin
        "serve": False,
        "namespace": namespace,
        "clientId": MOQ_CLIENT_ID,
        "botId": MOQ_BOT_ID,
        "transcriptTrack": MOQ_TRANSCRIPT_TRACK,
    }


def _presented_key(request: Request) -> str | None:
    return (
        request.headers.get("x-access-key")
        or request.query_params.get("key")
        or request.cookies.get(ACCESS_COOKIE)
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. ``settings`` is injectable for tests."""
    settings = settings or Settings()
    app = FastAPI(title="pipecat-moq-example proxy")
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def access_key_gate(request: Request, call_next):
        """Shared-passcode gate (only when ACCESS_KEY is set). ``?key=`` is
        exchanged for a cookie so the SPA's subsequent asset/API requests pass."""
        if settings.access_key is None or request.url.path == "/healthz":
            return await call_next(request)
        if _presented_key(request) != settings.access_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or wrong access key. Open the link with ?key=<key>."},
            )
        if request.query_params.get("key") and request.method == "GET":
            # Strip the key from the URL and persist it as a cookie.
            resp = RedirectResponse(
                url=str(request.url.remove_query_params("key")), status_code=302
            )
            resp.set_cookie(
                ACCESS_COOKIE,
                settings.access_key,
                httponly=True,
                samesite="lax",
                max_age=30 * 86400,
            )
            return resp
        resp = await call_next(request)
        if request.cookies.get(ACCESS_COOKIE) != settings.access_key:
            resp.set_cookie(
                ACCESS_COOKIE,
                settings.access_key,
                httponly=True,
                samesite="lax",
                max_age=30 * 86400,
            )
        return resp

    @app.middleware("http")
    async def ui_cache_headers(request: Request, call_next):
        """Vite hashes every chunk, so /client/assets/* can be cached forever, but
        index.html must always be revalidated — otherwise a tab that loads a stale
        index.html after a redeploy asks for chunk names that no longer exist
        ("Failed to fetch dynamically imported module …")."""
        resp = await call_next(request)
        path = request.url.path
        if path.startswith("/client/assets/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path.startswith("/client"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "agent": settings.agent_name,
            "relay": settings.moq_relay_url,
            "ui": "fork" if UI_DIST.is_dir() else "prebuilt",
        }

    @app.post("/start")
    async def start(request: Request) -> dict[str, Any]:
        try:
            request_data = await request.json()
        except ValueError:
            request_data = {}
        if not isinstance(request_data, dict):
            raise HTTPException(status_code=400, detail="JSON body must be an object")

        transport, payload = build_start_payload(request_data, settings)
        pcc = await pcc_start(settings, payload)

        namespace = payload["body"]["moq"]["namespace"]
        result: dict[str, Any] = {
            "sessionId": pcc.get("sessionId"),
            "moq": build_moq_client_config(namespace, settings),
        }
        logger.info(
            "start transport={} sessionId={} namespace={}",
            transport,
            result["sessionId"],
            namespace,
        )
        return result

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/client/")

    if UI_DIST.is_dir():
        app.mount("/client", StaticFiles(directory=UI_DIST, html=True))
        logger.info(f"Serving UI from {UI_DIST}")
    else:
        try:
            from pipecat_ai_prebuilt.frontend import PipecatPrebuiltUI

            app.mount("/client", PipecatPrebuiltUI)
            logger.warning(
                "ui/dist not found; serving the stock pipecat-ai-prebuilt UI "
                "(choose 'Media over QUIC' in its transport dropdown)"
            )
        except ImportError:  # pragma: no cover - only when the prebuilt package is absent
            logger.warning("No UI available: build client/ui or install pipecat-ai-prebuilt")

    return app


if __name__ == "__main__":
    import uvicorn

    _settings = Settings()
    _settings.validate()
    port = int(os.getenv("PORT", "7861"))
    logger.info(f"Proxying /start -> {_settings.start_url}")
    logger.info(f"MoQ relay: {_settings.moq_relay_url}")
    logger.info(f"Access key: {'set' if _settings.access_key else 'NOT SET (open access)'}")
    logger.info(f"Open http://localhost:{port}/client/")
    uvicorn.run(create_app(_settings), host="0.0.0.0", port=port)
