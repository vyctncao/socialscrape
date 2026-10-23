import test from "node:test"; import assert from "node:assert/strict"; import { formatOutput, resolveFormat } from "../src/output.js";
const data={success:true,empty:"",settings:{x:1},items:[{name:"a",count:2}]};
test("formats json table csv and markdown",()=>{assert.equal(formatOutput(data,"json"),JSON.stringify(data));assert.match(formatOutput(data,"table"),/name/);assert.match(formatOutput(data,"csv"),/name,count/);assert.match(formatOutput(data,"markdown"),/\| Key \| Value \|/)});
test("pretty and clean",()=>{assert.equal(resolveFormat({pretty:true}),"pretty");assert.doesNotMatch(formatOutput(data,"json",{clean:true}),/settings|success|empty/)});

import { chmodSync, mkdtempSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { writeOutputFile } from "../src/output.js";

test("overwriting output restricts an existing file to owner only", (t) => {
  if (process.platform === "win32") t.skip("POSIX file modes are not enforced on Windows");
  const dir = mkdtempSync(join(tmpdir(), "socialscrape-output-"));
  const dest = join(dir, "response.json");
  writeFileSync(dest, "old", { mode: 0o644 });
  chmodSync(dest, 0o644);

  writeOutputFile(dest, '{"ok":true}');

  assert.equal(statSync(dest).mode & 0o777, 0o600);
});
