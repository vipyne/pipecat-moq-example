# pipecat-moq-example

A [Pipecat](https://pipecat.ai) voice agent that talks to the browser via **Media over QUIC (MoQ)**, 
deployed to **Pipecat Cloud**, with a small web client you can run anywhere `docker compose` runs.

- **Pipeline**: cascade — Deepgram (STT) → OpenAI or Anthropic (LLM) → Cartesia (TTS);
  As always, Pipecat supports [many](https://docs.pipecat.ai/api-reference/server/services/supported-services#speech-to-text), [many](https://docs.pipecat.ai/api-reference/server/services/supported-services#large-language-models), [many](https://docs.pipecat.ai/api-reference/server/services/supported-services#text-to-speech) services. Take your pick!
- **Transport**: MoQ, via [`pipecat.transports.moq`](https://github.com/pipecat-ai/pipecat/tree/main/src/pipecat/transports/moq)
  on the bot and [`@pipecat-ai/moq-transport`](https://github.com/pipecat-ai/pipecat-client-web-transports/tree/main/transports/moq-transport)
  in the browser
- **Relay**: any MoQ relay; this example uses the public `https://cdn.moq.dev/anon`

## How it works

The bot and the browser never talk to each other directly. Both dial the same MoQ relay and
rendezvous on a per-session **namespace**: the browser publishes `<namespace>/request`, the bot
publishes `<namespace>/response`, and each subscribes to the other. Control messages (RTVI) ride a
JSON "transcript" track on the same session. Because everything is outbound from both sides, this
works behind NAT and needs no inbound ports on the bot.

```
Browser (client/ui) ──/start──▶ client/proxy.py ──/start (body.moq)──▶ Pipecat Cloud ──▶ server/bot.py
        │                                                                                    │
        └────────── WebTransport ─────────▶  MoQ relay (cdn.moq.dev/anon)  ◀──── QUIC ───────┘
```

Two details are specific to running MoQ on Pipecat Cloud:

- **Why `client/proxy.py` exists.** Pipecat Cloud's `/start` endpoint knows `daily | webrtc | websocket`;
  for anything else it just starts the bot and returns a session id. So the client side has to mint the
  namespace, pass it to the bot in the request `body` (`body.moq = {namespace, relayUrl, clientId, botId}`)
  and hand the UI the same `moq` connection block the Pipecat dev runner would return. The proxy is
  a small FastAPI shim that does exactly that and serves the UI.
- **Why the bot waits before its intro.** RTVI `client-ready` reaches the bot as soon as the browser's
  relay session is up — which can be before the browser has subscribed to the bot's audio track. MoQ is
  live media with no replay, so audio published in that window is lost. `bot.py` waits for a subscriber
  on its audio track (bounded by `MOQ_SUBSCRIBER_TIMEOUT`) before it starts talking.

Locally you don't need any of that: the Pipecat dev runner mints the namespace per `/start` and serves a
prebuilt UI with a "Media over QUIC" option.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) for Python; Node 22 only for UI development
- API keys: Deepgram, Cartesia, and OpenAI (or Anthropic — swap the commented block in `server/bot.py`)
- A MoQ relay. `https://cdn.moq.dev/anon` is public and anonymous and works out of the box; for anything
  beyond a demo run your own [`moq-relay`](https://github.com/kixelated/moq). The URL **must include the
  publish root path** (the `/anon` part) — both sides dial exactly that URL.
- A Chromium-based browser (Chrome, Edge). MoQ uses WebTransport, which Safari doesn't ship.

## Run locally

```bash
cd server
uv sync
cp env.example .env        # add your API keys
uv run bot.py --moq-connect https://cdn.moq.dev/anon
```

Open http://localhost:7860, choose **Media over QUIC** in the transport dropdown, and connect.

Without a relay at all — the bot serves its own QUIC socket with a self-signed cert the browser pins:

```bash
uv run bot.py -t moq
```

Tests: `uv run pytest`.

## Deploy the server to Pipecat Cloud

```bash
uv tool install "pipecat-ai[cli]"
pipecat cloud auth login

cd server
pipecat cloud secrets set pipecat-moq-example-secrets --file .env   # must include MOQ_RELAY_URL
pipecat cloud deploy                                                # builds ./Dockerfile in the cloud
```

Agent name, secret set, profile and scaling live in [`server/pcc-deploy.toml`](server/pcc-deploy.toml).

Smoke-test the MoQ path without a browser, then check the logs for the relay connection:

```bash
pipecat cloud agent start pipecat-moq-example \
  --data '{"moq":{"namespace":"pipecat-moq-your-namespace","relayUrl":"https://cdn.moq.dev/anon"}}'
pipecat cloud agent logs pipecat-moq-example
```

You should see `MoQ: connected to relay …` followed (once a browser joins that namespace) by
`MoQ: audio subscriber attached after …ms`.

## Run the client

The client is `proxy.py` (the `/start` shim) plus the built UI in `ui/`. It needs your Pipecat Cloud
**public** API key and the same relay URL as the bot.

```bash
cd client
cp env.example .env        # set PCC_PUBLIC_API_KEY (and PCC_AGENT_NAME if you renamed the agent)
```

Docker builds the UI and the proxy in one image:

```bash
docker compose up --build
```

Open http://localhost:7861 and click Connect (`GET /healthz` is the health check).

For UI development: run `uv run proxy.py` in one terminal and `npm run dev` in `ui/` in another.
Vite serves the UI with hot reload and proxies `/start` to the proxy on :7861.

**Gating a public demo.** Set `ACCESS_KEY` in `client/.env` and share the UI as
`https://<host>/client/?key=<ACCESS_KEY>`. The proxy exchanges the key for a cookie and strips it from
the URL; every route except `/healthz` requires it. It keeps strangers from burning your Pipecat Cloud
sessions — it is not a security boundary.

**HTTPS.** Browsers only expose the microphone and WebTransport in a secure context. `localhost` counts;
any other hostname needs TLS, so put whatever TLS-terminating reverse proxy you already use (Caddy,
nginx, Traefik, a cloud load balancer) in front of port 7861. Nothing in this repo assumes a particular
host or platform.

Tests: `uv run pytest` (Pipecat Cloud is mocked).

## Environment variables

**Server** (`server/env.example` → the Pipecat Cloud secret set):

| Variable | Purpose |
|---|---|
| `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | AI services |
| `MOQ_RELAY_URL` | Relay the bot dials on Pipecat Cloud (client mode), e.g. `https://cdn.moq.dev/anon` |
| `MOQ_CONNECTION_TIMEOUT` | Seconds to wait for the browser's broadcast at the relay (default 60) |
| `MOQ_SUBSCRIBER_TIMEOUT` | Seconds to wait for a subscriber on the bot's audio before speaking (default 15) |
| `PIPECAT_LOG_LEVEL` | `DEBUG` shows the transport's connect/publish lines |

**Client** (`client/env.example`):

| Variable | Purpose |
|---|---|
| `PCC_AGENT_NAME` | Must match `agent_name` in `server/pcc-deploy.toml` |
| `PCC_PUBLIC_API_KEY` | Pipecat Cloud public API key (used only by the proxy, never sent to the browser) |
| `MOQ_RELAY_URL` | Must match the bot's, so both sides meet at the same relay |
| `ACCESS_KEY` | Optional shared passcode; when set, open the UI with `?key=…` once |
| `PCC_START_URL`, `PORT` | Optional overrides |

## Project structure

```
pipecat-moq-example/
├── server/                 # The Pipecat bot (deployed to Pipecat Cloud)
│   ├── bot.py              # Pipeline + MoQ transport (dev runner or Pipecat Cloud body.moq)
│   ├── pyproject.toml      # pipecat-ai[...,moq,runner,...]
│   ├── uv.lock             # Required by the Dockerfile (uv sync --locked)
│   ├── env.example         # API keys + MOQ_RELAY_URL
│   ├── Dockerfile          # Pipecat Cloud image (dailyco/pipecat-base)
│   ├── pcc-deploy.toml     # Pipecat Cloud deployment config
│   └── tests/              # Subscriber-gate tests
├── client/                 # /start proxy + web UI (runs anywhere: docker compose)
│   ├── proxy.py            # Translates the UI's /start into a Pipecat Cloud session with body.moq
│   ├── ui/                 # Vite + React console using @pipecat-ai/moq-transport (forked from small-webrtc-prebuilt)
│   ├── Dockerfile          # Two-stage: build ui/ with Node, run proxy.py with uv
│   ├── docker-compose.yml  # One service on :7861
│   ├── env.example         # PCC_AGENT_NAME, PCC_PUBLIC_API_KEY, MOQ_RELAY_URL
│   └── tests/              # Proxy tests (Pipecat Cloud mocked)
└── README.md
```

## Learn more

- [Pipecat MoQ transport example](https://github.com/pipecat-ai/pipecat/blob/main/examples/transports/transports-moq.py)
- [`@pipecat-ai/moq-transport`](https://github.com/pipecat-ai/pipecat-client-web-transports/tree/main/transports/moq-transport)
- [Media over QUIC](https://moq.dev) and [`moq-relay`](https://github.com/kixelated/moq)
- [Pipecat Cloud documentation](https://docs.pipecat.ai/pipecat-cloud/introduction)
- [Pipecat documentation](https://docs.pipecat.ai/)
