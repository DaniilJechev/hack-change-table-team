import { describe, expect, it } from "vitest";
import {
  panelBounds,
  serverURL,
  shouldNotify,
  safeExternal,
  visibleInDesktop,
} from "../src/main/policy";
describe("DESKTOP-01 window", () => {
  it("opens in right third in logical pixels including negative monitors", () => {
    expect(panelBounds({ x: -1920, y: 0, width: 1920, height: 1040 })).toEqual({
      x: -640,
      y: 0,
      width: 640,
      height: 1040,
    });
  });
  it("clamps restored sizes to the work area", () => {
    expect(
      panelBounds(
        { x: 0, y: 0, width: 800, height: 500 },
        { width: 5000, height: 5000 },
      ),
    ).toEqual({ x: 0, y: 0, width: 800, height: 500 });
  });
  it("fits tiny screens", () => {
    expect(panelBounds({ x: 0, y: 0, width: 300, height: 400 })).toEqual({
      x: 0,
      y: 0,
      width: 300,
      height: 400,
    });
  });
});
describe("DESKTOP-05 address and links", () => {
  it("accepts HTTPS ngrok and rejects unsafe addresses", () => {
    expect(serverURL("https://abc.ngrok.app/")).toBe("https://abc.ngrok.app");
    for (const s of [
      "http://example.com",
      "https://name:password@host",
      "https://host/api",
      "file:///tmp/a",
      "https://host/?token=x",
    ])
      expect(() => serverURL(s)).toThrow();
  });
  it("permits HTTP loopback only during development", () => {
    expect(serverURL("http://127.0.0.1:8081", true)).toBe(
      "http://127.0.0.1:8081",
    );
    expect(() => serverURL("http://127.0.0.1:8081")).toThrow();
  });
  it("only opens HTTP links externally", () => {
    expect(safeExternal("https://github.com")).toBe(true);
    for (const s of ["file:///etc/passwd", "javascript:alert(1)", "bad"])
      expect(safeExternal(s)).toBe(false);
  });
});
it("DESKTOP-04 suppresses focused, replay and own notifications", () => {
  expect(shouldNotify(false, false, "assistant")).toBe(true);
  expect(shouldNotify(true, false, "assistant")).toBe(false);
  expect(shouldNotify(false, true, "assistant")).toBe(false);
  expect(shouldNotify(false, false, "user")).toBe(false);
});
it("DESKTOP-07 hides only Telegram history and restores it without deleting messages", () => {
  const records = ["telegram", "desktop", "background"].map((channel) => ({
    channel,
  }));
  expect(records.filter((r) => visibleInDesktop(r, false))).toEqual(
    records.slice(1),
  );
  expect(records.filter((r) => visibleInDesktop(r, true))).toEqual(records);
});
