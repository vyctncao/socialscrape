import test from "node:test";
import assert from "node:assert/strict";
import { apis, accountApis } from "../api-config/apis.js";
import { getToolDefs, getPlatformMap } from "../src/command-registry.js";

test("generates exactly the platform endpoint catalog", () => {
  assert.equal(apis.flatMap((api) => api.endpoints).length, 66);
  assert.equal(accountApis.flatMap((api) => api.endpoints).length, 4);
  assert.equal(getToolDefs().length, 66);
  assert.deepEqual([...getPlatformMap().keys()], ["youtube", "tiktok", "instagram"]);
});

test("maps every parameter to its CLI flag", () => {
  for (const [platform, tools] of getPlatformMap()) {
    for (const tool of tools) {
      assert.ok(tool._action);
      for (const param of tool.params) {
        assert.equal(param.name.replaceAll("_", "-").includes("_"), false, `${platform} ${tool._action}`);
      }
    }
  }
});
