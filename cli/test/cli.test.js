import test from "node:test"; import assert from "node:assert/strict"; import { spawnSync } from "node:child_process";
const bin=new URL("../bin/socialscrape.js",import.meta.url).pathname;
test("top-level help and endpoint help",()=>{let r=spawnSync(process.execPath,[bin,"--help"],{encoding:"utf8"});assert.equal(r.status,0);assert.match(r.stdout,/youtube\s+youtube endpoints/);r=spawnSync(process.execPath,[bin,"youtube","video","--help"],{encoding:"utf8"});assert.equal(r.status,0);assert.match(r.stdout,/--url <url>/)});
test("missing required option exits nonzero",()=>{const r=spawnSync(process.execPath,[bin,"--api-key","x","youtube","video"]);assert.notEqual(r.status,0)});
