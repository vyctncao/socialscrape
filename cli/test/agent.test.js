import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
const bin=new URL("../bin/socialscrape.js",import.meta.url).pathname;
const cases={claude:".claude/claude_desktop_config.json",codex:".codex/mcp.json",cursor:".cursor/mcp.json",vscode:".vscode/mcp.json"};
for(const [target,rel] of Object.entries(cases)) test(`agent add writes ${target}`,()=>{
 const home=mkdtempSync(join(tmpdir(),"socialscrape-home-")); const cwd=mkdtempSync(join(tmpdir(),"socialscrape-project-"));
 const r=spawnSync(process.execPath,[bin,"--api-key","secret-key","agent","add",target],{env:{...process.env,HOME:home},cwd,encoding:"utf8"}); assert.equal(r.status,0,r.stderr);
 const root=target==="claude"||target==="codex"?home:cwd; const cfg=JSON.parse(readFileSync(join(root,rel)));
 const entry=target==="vscode"?cfg.servers.socialscrape:cfg.mcpServers.socialscrape;
 assert.equal(entry.url,"https://scrape.mimic.host/mcp"); assert.equal(entry.headers["x-api-key"],"secret-key");
});
test("unknown agent exits nonzero",()=>{const r=spawnSync(process.execPath,[bin,"--api-key","x","agent","add","unknown"]);assert.equal(r.status,1)});
