/**
 * Pipecat console UI using Media over QUIC as the transport. Talks to
 * client/proxy.py, which turns the UI's `/start` into a Pipecat Cloud session
 * and hands back the `moq` connection block the transport needs.
 *
 * Forked from pipecat-ai/small-webrtc-prebuilt client/src/index.tsx and
 * trimmed to a single transport.
 */

import { ConsoleTemplate, FullScreenContainer, ThemeProvider } from "@pipecat-ai/voice-ui-kit";
import { StrictMode, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";

const LS_KEY = "pipecat-moq.access-key";
const LS_RELAY_KEY = "pipecat-moq.relay";

/**
 * Optional access key for a gated proxy (ACCESS_KEY in client/.env). `?key=…`
 * in the URL wins and is remembered; the proxy also sets a cookie, so this is
 * mostly for `npm run dev` and for the first /start before the cookie lands.
 */
function readAccessKey(): string | null {
  const url = new URL(window.location.href);
  const fromUrl = url.searchParams.get("key");
  if (fromUrl) {
    localStorage.setItem(LS_KEY, fromUrl);
    url.searchParams.delete("key");
    window.history.replaceState({}, "", url.toString());
    return fromUrl;
  }
  return localStorage.getItem(LS_KEY);
}

const TITLE = "Pipecat | MoQ transport";

/**
 * Connect-time relay choice: `?relay=…` wins and is remembered, then a
 * remembered pick, then the proxy's default. Off-list values ignored (proxy
 * rejects them anyway).
 */
function resolveRelay(relays: string[]): string {
  const fromUrl = new URL(window.location.href).searchParams.get("relay");
  if (fromUrl && relays.includes(fromUrl)) {
    localStorage.setItem(LS_RELAY_KEY, fromUrl);
    return fromUrl;
  }
  const remembered = localStorage.getItem(LS_RELAY_KEY);
  if (remembered && relays.includes(remembered)) return remembered;
  return relays[0];
}

const RELAY_LABEL_MAX = 32;

/** Compact display label: host + path, no scheme, hard-capped at
 * RELAY_LABEL_MAX chars. Full URL stays the value. */
function relayLabel(url: string): string {
  let label = url;
  try {
    const u = new URL(url);
    label = u.host + (u.pathname === "/" ? "" : u.pathname);
  } catch {
    // keep raw string
  }
  return label.length > RELAY_LABEL_MAX ? `${label.slice(0, RELAY_LABEL_MAX)}…` : label;
}

function Home() {
  const hasWebTransport = "WebTransport" in window;
  // Proxy's relay allowlist; first entry is the default.
  const [relays, setRelays] = useState<string[]>([]);
  const [relay, setRelay] = useState<string | null>(null);
  useEffect(() => {
    fetch("/healthz")
      .then((r) => (r.ok ? r.json() : null))
      .then((h) => {
        const list: string[] = Array.isArray(h?.relays)
          ? h.relays.filter((u: unknown): u is string => typeof u === "string" && !!u)
          : typeof h?.relay === "string" && h.relay
            ? [h.relay]
            : [];
        setRelays(list);
        setRelay(list.length ? resolveRelay(list) : null);
      })
      .catch(() => setRelay(null));
  }, []);
  const headers = useMemo(() => {
    const h = new Headers();
    const key = readAccessKey();
    if (key) h.set("X-Access-Key", key);
    return h;
  }, []);
  const startBotParams = useMemo(() => {
    const requestData: Record<string, string> = { transport: "moq" };
    if (relay) requestData.relayUrl = relay;
    return { endpoint: "/start", headers, requestData };
  }, [headers, relay]);

  const pickRelay = (url: string) => {
    localStorage.setItem(LS_RELAY_KEY, url);
    setRelay(url);
  };

  return (
    <ThemeProvider>
      <FullScreenContainer className="items-stretch justify-start">
        <ConsoleTemplate
          titleText={relay ? `${TITLE} | ${relayLabel(relay)}` : TITLE}
          transportType="moq"
          startBotParams={startBotParams}
          noUserVideo={true}
        />
        {relays.length > 1 && (
          <div className="notice relay-picker">
            <label>
              Relay{" "}
              <select value={relay ?? ""} onChange={(e) => pickRelay(e.target.value)}>
                {relays.map((u) => (
                  <option key={u} value={u} title={u}>
                    {relayLabel(u)}
                  </option>
                ))}
              </select>
            </label>{" "}
            — applies on next connect
          </div>
        )}
        {!hasWebTransport && (
          <div className="notice warn">
            This browser has no WebTransport — Media over QUIC won't connect. Use Chrome or Edge.
          </div>
        )}
      </FullScreenContainer>
    </ThemeProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Home />
  </StrictMode>,
);
