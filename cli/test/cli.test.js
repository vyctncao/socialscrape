import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";

const bin = new URL("../bin/socialscrape.js", import.meta.url).pathname;

function run(...args) {
  return spawnSync(process.execPath, [bin, ...args], { encoding: "utf8" });
}

test("top-level help exposes only supported platform commands", () => {
  const result = run("--help");
  assert.equal(result.status, 0);
  assert.match(result.stdout, /youtube\s+youtube endpoints/);
  assert.doesNotMatch(result.stdout, /^  account\s/m);
});

test("list excludes MCP-only account endpoints", () => {
  const result = run("list");
  assert.equal(result.status, 0);
  assert.doesNotMatch(result.stdout, /account/);
});

test("endpoint help preserves reference descriptions and examples", () => {
  const result = run("youtube", "video", "--help");
  assert.equal(result.status, 0);
  assert.match(result.stdout, /YouTube video or short URL/);
  assert.match(result.stdout, /https:\/\/youtu\.be\/gTCi7PaOPLY/);
  assert.match(result.stdout, /Preferred response language/);
  assert.match(result.stdout, /e\.g\. en/);
  assert.match(result.stdout, /cached_at/);
});

test("missing required option exits nonzero", () => {
  const result = run("--api-key", "x", "youtube", "video");
  assert.notEqual(result.status, 0);
});

test("all 66 endpoint help outputs match adapted golden fixtures", async () => {
  const fixtures = (await import("./fixtures/endpoint-help.json", { with: { type: "json" } })).default;
  assert.equal(Object.keys(fixtures).length, 66);
  for (const [command, expected] of Object.entries(fixtures)) {
    const result = run(...command.split(" "), "--help");
    assert.equal(result.status, 0, command);
    assert.equal(result.stdout, expected, command);
  }
});
