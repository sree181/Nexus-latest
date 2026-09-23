import { describe, expect, it } from "vitest";
import { MAX_STREAM_RECONNECTS, streamReconnectDelay } from "./useRunStream";

describe("run stream recovery", () => {
  it("uses bounded exponential reconnect delays", () => {
    expect(streamReconnectDelay(1)).toBe(250);
    expect(streamReconnectDelay(2)).toBe(500);
    expect(streamReconnectDelay(3)).toBe(1_000);
    expect(streamReconnectDelay(20)).toBe(4_000);
  });

  it("caps automatic reconnect attempts", () => {
    expect(MAX_STREAM_RECONNECTS).toBe(5);
  });
});
