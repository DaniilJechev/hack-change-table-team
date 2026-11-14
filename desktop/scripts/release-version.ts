import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

export function validateReleaseVersion(tag: string, version: string): void {
  if (!/^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(tag))
    throw new Error("Release tag must be stable vMAJOR.MINOR.PATCH");
  if (tag !== `v${version}`)
    throw new Error(
      `Release tag ${tag} does not match desktop version ${version}`,
    );
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const { version } = JSON.parse(
    readFileSync(new URL("../package.json", import.meta.url), "utf8"),
  );
  validateReleaseVersion(process.env.RELEASE_TAG || "", version);
  console.log(`Release version validated: ${version}`);
}
