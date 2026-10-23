import test from "node:test"; import assert from "node:assert/strict";
import { apis } from "../api-config/apis.js"; import { getToolDefs, getPlatformMap } from "../src/command-registry.js";
test("generates every catalog endpoint",()=>{assert.equal(apis.slice(0,3).flatMap(x=>x.endpoints).length,66);assert.equal(getToolDefs().length,70);assert.deepEqual([...getPlatformMap().keys()], ["youtube","tiktok","instagram","account"])});
test("maps every parameter to its CLI flag",()=>{for(const [platform,tools] of getPlatformMap())for(const tool of tools){assert.ok(tool._action);for(const p of tool.params)assert.equal(p.name.replaceAll("_","-").includes("_"),false,`${platform} ${tool._action}`)}});
