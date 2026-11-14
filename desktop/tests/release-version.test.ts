import { expect, it } from "vitest";
import { validateReleaseVersion } from "../scripts/release-version";

it("DESKTOP-REL-01 accepts an exact stable version tag", () => {
  expect(() => validateReleaseVersion("v0.1.1", "0.1.1")).not.toThrow();
  expect(() => validateReleaseVersion("v12.30.4", "12.30.4")).not.toThrow();
});

it("DESKTOP-REL-01 rejects a different package version", () => {
  expect(() => validateReleaseVersion("v0.1.2", "0.1.1")).toThrow(
    "does not match",
  );
});

it("DESKTOP-REL-01 rejects branch names and non-stable or malformed tags", () => {
  for (const tag of [
    "main",
    "",
    "0.1.1",
    "v0.1",
    "v01.1.1",
    "v0.1.1-beta.1",
    "v0.1.1+meta",
    "v0.1.1\n",
  ])
    expect(() => validateReleaseVersion(tag, "0.1.1")).toThrow();
});
