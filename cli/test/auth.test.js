import test from "node:test";
import assert from "node:assert/strict";
import config from "../src/config.js";
import { resolveApiKey, resolveApiKeyWithSource } from "../src/auth.js";

test("auth precedence is flag, stored config, environment", (t) => {
  const old = process.env.SOCIALSCRAPE_API_KEY;
  t.after(() => {
    config.delete("apiKey");
    if (old === undefined) delete process.env.SOCIALSCRAPE_API_KEY;
    else process.env.SOCIALSCRAPE_API_KEY = old;
  });

  config.set("apiKey", "stored");
  process.env.SOCIALSCRAPE_API_KEY = "env";
  assert.deepEqual(resolveApiKeyWithSource({ apiKey: "flag" }), { key: "flag", source: "flag" });
  assert.deepEqual(resolveApiKeyWithSource(), { key: "stored", source: "stored config" });
  assert.equal(resolveApiKey(), "stored");
  config.delete("apiKey");
  assert.deepEqual(resolveApiKeyWithSource(), { key: "env", source: "environment variable" });
  assert.equal(resolveApiKey(), "env");
});
