import { useEffect, useState } from "react";
import type { MemoryEvent, RunStreamEvent, RunSummary } from "./api";
import { socketProtocols } from "./auth";

export type RunStreamStatus =
  | "idle"
  | "connecting"
  | "streaming"
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
    setState({ ...IDLE, status: "connecting" });

    let closed = false;
    // the access token rides as a subprotocol: a browser cannot put an
    // Authorization header on a WebSocket
    const socket = new WebSocket(socketUrl(runId), socketProtocols());

    socket.onmessage = (message: MessageEvent<string>) => {
      if (closed) return;
      let frame: RunStreamEvent;
      try {
        frame = JSON.parse(message.data) as RunStreamEvent;
      } catch {
        return;
      }
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
            return { ...prev, status: "done", run: frame.run };
          case "error":
            return { ...prev, status: "error", error: frame.detail };
        }
      });
    };

    // a socket this effect already abandoned must not report into the state
    // its replacement now owns, which is what StrictMode's double mount does
    socket.onerror = () => {
      if (closed) return;
      setState((prev) =>
        prev.status === "done"
          ? prev
          : { ...prev, status: "error", error: "the run stream failed" },
      );
    };

    socket.onclose = () => {
      if (closed) return;
      setState((prev) =>
        prev.status === "done" || prev.status === "error"
          ? prev
          : {
              ...prev,
              status: "error",
              error: "the run stream closed before the run finished",
            },
      );
    };

    return () => {
      closed = true;
      socket.close();
    };
  }, [runId, enabled]);

  return state;
}
