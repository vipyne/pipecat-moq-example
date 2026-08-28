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

function Home() {
  const hasWebTransport = "WebTransport" in window;
  // The proxy knows the relay both sides dial; show it in the header.
  const [relay, setRelay] = useState<string | null>(null);
  useEffect(() => {
    fetch("/healthz")
      .then((r) => (r.ok ? r.json() : null))
      .then((h) => setRelay(typeof h?.relay === "string" && h.relay ? h.relay : null))
      .catch(() => setRelay(null));
  }, []);
  const headers = useMemo(() => {
    const h = new Headers();
    const key = readAccessKey();
    if (key) h.set("X-Access-Key", key);
    return h;
  }, []);

  return (
    <ThemeProvider>
      <FullScreenContainer className="items-stretch justify-start">
        <ConsoleTemplate
          titleText={relay ? `${TITLE} | ${relay}` : TITLE}
          transportType="moq"
          startBotParams={{
            endpoint: "/start",
            headers,
            requestData: { transport: "moq" },
          }}
          noUserVideo={true}
        />
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
