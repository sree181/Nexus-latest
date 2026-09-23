import { useEffect, useState } from "react";
import type { MemoryEvent, RunStreamEvent, RunSummary } from "./api";
import { socketProtocols } from "./auth";

export type RunStreamStatus =
  | "idle"
  | "connecting"
  | "reconnecting"
  | "streaming"
  | "offline"
  | "done"
  | "error";

export interface RunStream {
  /** memory writes so far, in the order the run made them */
  events: MemoryEvent[];
  /** what the stream said about itself, e.g. what it is recording */
  notices: string[];
  status: RunStreamStatus;
  error: string | null;
  /** the run's final state, once the stream is done */
  run: RunSummary | null;
}

const IDLE: RunStream = {
  events: [],
  notices: [],
  status: "idle",
  error: null,
  run: null,
};

export const MAX_STREAM_RECONNECTS = 5;

/** Bounded exponential backoff: quick enough to repair a brief proxy restart,
 * capped so an unavailable service does not keep a browser busy indefinitely. */
export function streamReconnectDelay(attempt: number): number {
  return Math.min(4_000, 250 * 2 ** Math.max(0, attempt - 1));
}

function socketUrl(runId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const path = `/api/runs/${encodeURIComponent(runId)}/stream`;
  return `${proto}//${window.location.host}${path}`;
}

/** Subscribes to a run's memory-forming stream.
 *
 *  The API drives this: in engine mode each `memory` frame is a hyperedge that
 *  already exists by the time it arrives, so what accumulates here is memory,
 *  not a prediction of it. Closing the socket does not abort the run — the
 *  server finishes recording — so a remount replays what was written. */
export function useRunStream(
  runId: string | null | undefined,
  enabled = true,
): RunStream {
  const [state, setState] = useState<RunStream>(IDLE);

  useEffect(() => {
    if (!runId || !enabled) {
      setState(IDLE);
      return;
    }
    let closed = false;
    let attempt = 0;
    let retryTimer: ReturnType<typeof window.setTimeout> | undefined;
    let socket: WebSocket | undefined;
    let terminal = false;

    const clearRetry = () => {
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      retryTimer = undefined;
    };

    const scheduleReconnect = () => {
      if (closed || terminal || retryTimer !== undefined) return;
      if (!window.navigator.onLine) {
        setState((prev) => ({
          ...prev,
          status: "offline",
          error: "You are offline. Waiting to reconnect the run stream.",
        }));
        return;
      }
      if (attempt >= MAX_STREAM_RECONNECTS) {
        terminal = true;
        setState((prev) => ({
          ...prev,
          status: "error",
          error: "The run stream could not reconnect. Replay the stream to try again.",
        }));
        return;
      }
      attempt += 1;
      setState((prev) => ({ ...prev, status: "reconnecting", error: null }));
      retryTimer = window.setTimeout(() => {
        retryTimer = undefined;
        connect();
      }, streamReconnectDelay(attempt));
    };

    const connect = () => {
      if (closed || terminal) return;
      if (!window.navigator.onLine) {
        scheduleReconnect();
        return;
      }
      setState((prev) => ({
        ...prev,
        status: attempt === 0 ? "connecting" : "reconnecting",
        error: null,
      }));
      try {
        // the access token rides as a subprotocol: a browser cannot put an
        // Authorization header on a WebSocket
        socket = new WebSocket(socketUrl(runId), socketProtocols());
      } catch {
        scheduleReconnect();
        return;
      }

      socket.onopen = () => undefined;
      socket.onmessage = (message: MessageEvent<string>) => {
        if (closed || terminal) return;
        let frame: RunStreamEvent;
        try {
          frame = JSON.parse(message.data) as RunStreamEvent;
        } catch {
          return;
        }
        // A socket that merely opens can be a captive portal or a proxy that
        // accepts then drops upgrades. A valid stream frame is the recovery
        // signal that earns a fresh retry budget.
        attempt = 0;
        setState((prev) => {
          switch (frame.type) {
            case "memory":
              return frame.memory
                ? {
                    ...prev,
                    status: "streaming",
                    events: [...prev.events, frame.memory],
                  }
                : prev;
            case "notice":
              return frame.detail
                ? {
                    ...prev,
                    status: "streaming",
                    notices: [...prev.notices, frame.detail],
                  }
                : prev;
            case "done":
              terminal = true;
              clearRetry();
              return { ...prev, status: "done", run: frame.run, error: null };
            case "error":
              terminal = true;
              clearRetry();
              return { ...prev, status: "error", error: frame.detail };
          }
        });
      };

      // Browsers normally follow onerror with onclose. Keep error passive and
      // make onclose the sole reconnect path so one failed socket schedules one
      // retry rather than two.
      socket.onerror = () => undefined;
      socket.onclose = () => {
        if (!closed && !terminal) scheduleReconnect();
      };
    };

    const offline = () => {
      clearRetry();
      setState((prev) => ({
        ...prev,
        status: "offline",
        error: "You are offline. Waiting to reconnect the run stream.",
      }));
      socket?.close();
    };
    const online = () => {
      if (closed || terminal) return;
      clearRetry();
      connect();
    };

    window.addEventListener("offline", offline);
    window.addEventListener("online", online);
    setState({ ...IDLE, status: window.navigator.onLine ? "connecting" : "offline" });
    if (window.navigator.onLine) connect();

    return () => {
      closed = true;
      clearRetry();
      window.removeEventListener("offline", offline);
      window.removeEventListener("online", online);
      socket?.close();
    };
  }, [runId, enabled]);

  return state;
}
