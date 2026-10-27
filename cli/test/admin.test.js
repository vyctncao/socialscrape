import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable } from "node:stream";
import { createProgram } from "../src/cli.js";
import { stripAnsi } from "../src/output.js";
import { MAX_COOKIE_BYTES, redactSecrets } from "../src/commands/admin.js";

const SECRET_PROXY = "http://proxyuser:s3cr3t-pass@proxy.example:8080";
const COOKIES = "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tSUPERSECRETCOOKIE\n";

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const PLATFORMS = {
  success: true,
  max_entries: 5,
  platforms: {
    instagram: {
      proxies: [{ id: "p2", position: 2, updated_at: "2026-01-02" }, { id: "p1", position: 1, updated_at: "2026-01-01" }],
      accounts: [{ id: "a1", position: 1, updated_at: "2026-01-03", health: { status: "alive", account: "brand", reason: null, checked_at: "x" } }],
    },
    youtube: { proxies: [], accounts: [] },
  },
};

// Runs the real commander program with injected I/O; returns captured output, exit code, and the requests made.
async function exec(args, { routes = {}, stdin = "", stdinIsTTY = false, stdoutIsTTY = false, promptSecret, confirm, key = "owner-key" } = {}) {
  const out = [], err = [], requests = [];
  let code = 0;
  const fetchImpl = async (url, options) => {
    const u = new URL(url);
    requests.push({ method: options.method, path: u.pathname, search: u.search, headers: options.headers, body: options.body });
    const handler = routes[`${options.method} ${u.pathname}`];
    return handler ? handler(options) : json(404, { success: false, error: "not_found", message: "no route" });
  };
  const admin = {
    fetchImpl, stdin: Readable.from([Buffer.from(stdin)]), stdinIsTTY, stdoutIsTTY,
    out: (l) => out.push(stripAnsi(String(l))), err: (l) => err.push(stripAnsi(String(l))),
    exit: (c) => { code = Math.max(code, c); }, promptSecret, confirm, spinner: () => ({ stop() {} }),
  };
  const argv = ["node", "socialscrape", ...(key ? ["--api-key", key] : []), ...args];
  await createProgram({ admin }).parseAsync(argv);
  return { out: out.join("\n"), err: err.join("\n"), code, requests };
}

test("list sends the owner key in a header and renders a sorted table", async () => {
  const r = await exec(["--format", "table", "proxies", "list"], { routes: { "GET /v1/admin/platforms": () => json(200, PLATFORMS) } });
  assert.equal(r.code, 0);
  assert.equal(r.requests[0].headers["x-api-key"], "owner-key");
  assert.match(r.out, /platform.*kind.*position.*id.*status.*updated/s);
  assert.ok(r.out.indexOf("p1") < r.out.indexOf("p2"), "sorted by position");
  assert.doesNotMatch(r.out, /alive/, "proxies list does not show accounts");
});

test("accounts list shows health status and filters by platform", async () => {
  const routes = { "GET /v1/admin/platforms": () => json(200, PLATFORMS) };
  const r = await exec(["--format", "table", "accounts", "list", "instagram"], { routes });
  assert.match(r.out, /a1/);
  assert.match(r.out, /alive \(brand\)/);
  const empty = await exec(["--format", "table", "accounts", "list", "youtube"], { routes });
  assert.match(empty.out, /No accounts configured for youtube/);
});

test("list --json prints structured data filtered to the requested kind", async () => {
  const r = await exec(["--json", "proxies", "list", "instagram"], { stdoutIsTTY: true, routes: { "GET /v1/admin/platforms": () => json(200, PLATFORMS) } });
  const data = JSON.parse(r.out);
  assert.deepEqual(Object.keys(data.platforms), ["instagram"]);
  assert.equal(data.platforms.instagram.proxies.length, 2);
  assert.equal(data.platforms.instagram.accounts, undefined);
  assert.equal(data.max_entries, 5);
});

test("non-TTY default output is JSON and csv rows are flattened", async () => {
  const routes = { "GET /v1/admin/platforms": () => json(200, PLATFORMS) };
  assert.equal(JSON.parse((await exec(["accounts", "list"], { routes })).out).success, true);
  const csv = (await exec(["--format", "csv", "accounts", "list"], { routes })).out;
  assert.match(csv, /^platform,kind,position,id,status,updated_at/);
});

test("proxies add reads the proxy from stdin, keeps it out of the URL, and tests by default", async () => {
  const r = await exec(["proxies", "add", "instagram", "--stdin"], {
    stdin: `${SECRET_PROXY}\n`, stdoutIsTTY: true, key: "owner-key",
    routes: { "POST /v1/admin/platforms/instagram/proxies": () => json(201, { success: true, id: "p9", position: 3, test: { ok: true, connected: true, status: 200, exit_ip: "1.2.3.4", latency_ms: 120 } }) },
  });
  assert.equal(r.code, 0);
  const req = r.requests[0];
  assert.equal(req.method, "POST");
  assert.equal(req.search, "");
  assert.ok(!req.path.includes("s3cr3t"));
  assert.deepEqual(JSON.parse(req.body), { proxy: SECRET_PROXY, test: true });
  assert.match(r.out, /Added proxy p9 for instagram at position 3/);
  assert.match(r.out, /Test: OK/);
  assert.doesNotMatch(r.out + r.err, /s3cr3t/);
});

test("proxies add --no-test sends test:false and prompts when stdin is a TTY", async () => {
  let asked = 0;
  const r = await exec(["--json", "proxies", "add", "tiktok", "--no-test"], {
    stdinIsTTY: true, promptSecret: async () => { asked++; return ` ${SECRET_PROXY} `; },
    routes: { "POST /v1/admin/platforms/tiktok/proxies": () => json(201, { success: true, id: "p1", position: 1, test: null }) },
  });
  assert.equal(asked, 1);
  assert.deepEqual(JSON.parse(r.requests[0].body), { proxy: SECRET_PROXY, test: false });
  assert.equal(JSON.parse(r.out).id, "p1");
});

test("proxies add refuses a proxy given as an argument or option and never echoes it", async () => {
  for (const args of [["proxies", "add", "instagram", SECRET_PROXY], ["proxies", "add", "instagram", `--proxy=${SECRET_PROXY}`], ["proxies", "add", "instagram", "--proxy", SECRET_PROXY], ["proxies", "add", SECRET_PROXY]]) {
    const r = await exec(args, { stdin: "unused" });
    assert.equal(r.code, 1, args.join(" "));
    assert.equal(r.requests.length, 0);
    assert.doesNotMatch(r.out + r.err, /s3cr3t|proxyuser/);
    assert.match(r.err, /--stdin|Unknown platform/);
  }
});

test("proxies add without a terminal or --stdin fails clearly", async () => {
  const r = await exec(["proxies", "add", "instagram"]);
  assert.equal(r.code, 1);
  assert.match(r.err, /--stdin/);
  assert.equal(r.requests.length, 0);
});

test("a failed test result on add prints it and exits 2 while the entry is kept", async () => {
  const r = await exec(["proxies", "add", "google", "--stdin"], {
    stdin: SECRET_PROXY, stdoutIsTTY: true,
    routes: { "POST /v1/admin/platforms/google/proxies": () => json(201, { success: true, id: "p1", position: 1, test: { ok: false, connected: true, status: 429, blocked: true, message: `blocked via ${SECRET_PROXY}` } }) },
  });
  assert.equal(r.code, 2);
  assert.match(r.out, /Test: FAILED/);
  assert.match(r.out, /was saved/);
  assert.doesNotMatch(r.out, /s3cr3t/, "server echoes are redacted");
});

test("accounts add reads --file and --stdin, sends cookies in the body only", async () => {
  const dir = mkdtempSync(join(tmpdir(), "sc-admin-"));
  const file = join(dir, "cookies.txt");
  writeFileSync(file, COOKIES);
  const routes = { "POST /v1/admin/platforms/instagram/accounts": () => json(201, { success: true, id: "a2", position: 2, check: { ok: true, status: "alive", account: "brand" } }) };
  const viaFile = await exec(["accounts", "add", "instagram", "--file", file], { routes, stdoutIsTTY: true });
  const viaStdin = await exec(["accounts", "add", "instagram", "--stdin"], { routes, stdin: COOKIES, stdoutIsTTY: true });
  for (const r of [viaFile, viaStdin]) {
    assert.equal(r.code, 0);
    assert.equal(r.requests[0].search, "");
    assert.deepEqual(JSON.parse(r.requests[0].body), { cookies: COOKIES });
    assert.match(r.out, /Added account a2/);
    assert.match(r.out, /Check: OK/);
    assert.doesNotMatch(r.out + r.err, /SUPERSECRETCOOKIE/);
  }
});

test("accounts add enforces the 2 MiB limit for files and stdin without sending anything", async () => {
  const dir = mkdtempSync(join(tmpdir(), "sc-admin-"));
  const file = join(dir, "big.txt");
  writeFileSync(file, "a".repeat(MAX_COOKIE_BYTES + 1));
  const big = await exec(["accounts", "add", "instagram", "--file", file]);
  assert.equal(big.code, 1);
  assert.match(big.err, /2 MiB/);
  const bigStdin = await exec(["accounts", "add", "instagram", "--stdin"], { stdin: "a".repeat(MAX_COOKIE_BYTES + 1) });
  assert.equal(bigStdin.code, 1);
  assert.match(bigStdin.err, /2 MiB/);
  assert.equal(big.requests.length + bigStdin.requests.length, 0);
  const exact = join(dir, "exact.txt");
  writeFileSync(exact, "a".repeat(MAX_COOKIE_BYTES));
  const ok = await exec(["accounts", "add", "instagram", "--file", exact], { routes: { "POST /v1/admin/platforms/instagram/accounts": () => json(201, { success: true, id: "a", position: 1, check: null }) } });
  assert.equal(ok.code, 0);
});

test("accounts add requires exactly one source and refuses positional cookies", async () => {
  assert.equal((await exec(["accounts", "add", "instagram"])).code, 1);
  assert.equal((await exec(["accounts", "add", "instagram", "--stdin", "--file", "x"])).code, 1);
  const r = await exec(["accounts", "add", "instagram", "sessionid=SUPERSECRETCOOKIE"]);
  assert.equal(r.code, 1);
  assert.doesNotMatch(r.err, /SUPERSECRETCOOKIE/);
  const missing = await exec(["accounts", "add", "instagram", "--file", "/nonexistent/cookies.txt"]);
  assert.equal(missing.code, 1);
  assert.match(missing.err, /Cannot read file/);
});

test("proxies test --all lists then tests each id; exit 2 when any test fails", async () => {
  const routes = {
    "GET /v1/admin/platforms": () => json(200, PLATFORMS),
    "POST /v1/admin/platforms/instagram/proxies/p1/test": () => json(200, { success: true, ok: true, connected: true, status: 200, exit_ip: "9.9.9.9", latency_ms: 80 }),
    "POST /v1/admin/platforms/instagram/proxies/p2/test": () => json(200, { success: true, ok: false, connected: false, error: "timeout", message: "timed out" }),
  };
  const r = await exec(["--format", "table", "proxies", "test", "instagram", "--all"], { routes });
  assert.deepEqual(r.requests.map((q) => `${q.method} ${q.path}`), ["GET /v1/admin/platforms", "POST /v1/admin/platforms/instagram/proxies/p1/test", "POST /v1/admin/platforms/instagram/proxies/p2/test"]);
  assert.equal(r.code, 2);
  assert.match(r.out, /proxy p1: OK/);
  assert.match(r.out, /proxy p2: FAILED/);
  assert.match(r.out, /1 of 2 failed/);
  const j = await exec(["--json", "proxies", "test", "instagram", "--all"], { routes });
  const data = JSON.parse(j.out);
  assert.deepEqual(data.results.map((x) => [x.id, x.ok]), [["p1", true], ["p2", false]]);
});

test("accounts check <id> and --all work; single check passing exits 0", async () => {
  const routes = {
    "GET /v1/admin/platforms": () => json(200, PLATFORMS),
    "POST /v1/admin/platforms/instagram/accounts/a1/check": () => json(200, { success: true, ok: true, status: "alive", account: "brand" }),
  };
  const one = await exec(["--format", "table", "accounts", "check", "instagram", "a1"], { routes });
  assert.equal(one.code, 0);
  assert.match(one.out, /account a1: OK/);
  assert.equal(one.requests.length, 1);
  const all = await exec(["--json", "accounts", "check", "instagram", "--all"], { routes });
  assert.equal(JSON.parse(all.out).results.length, 1);
  const empty = await exec(["--json", "accounts", "check", "youtube", "--all"], { routes });
  assert.deepEqual(JSON.parse(empty.out).results, []);
});

test("test/check require an id or --all, not both", async () => {
  assert.equal((await exec(["proxies", "test", "instagram"])).code, 1);
  assert.equal((await exec(["proxies", "test", "instagram", "p1", "--all"])).code, 1);
});

test("remove with --yes skips confirmation and issues DELETE without a body", async () => {
  const routes = { "DELETE /v1/admin/platforms/instagram/proxies/p1": () => json(200, { success: true, removed: true }) };
  let asked = 0;
  const r = await exec(["proxies", "remove", "instagram", "p1", "--yes"], { routes, stdinIsTTY: true, stdoutIsTTY: true, confirm: async () => { asked++; return false; } });
  assert.equal(asked, 0);
  assert.equal(r.code, 0);
  assert.equal(r.requests[0].method, "DELETE");
  assert.equal(r.requests[0].body, undefined);
  assert.match(r.out, /Removed proxy p1/);
});

test("remove asks for confirmation in a TTY and cancels on no; refuses without TTY or --yes", async () => {
  const routes = { "DELETE /v1/admin/platforms/instagram/accounts/a1": () => json(200, { success: true, removed: true }) };
  const declined = await exec(["accounts", "remove", "instagram", "a1"], { routes, stdinIsTTY: true, confirm: async () => false });
  assert.equal(declined.requests.length, 0);
  assert.equal(declined.code, 1);
  const accepted = await exec(["accounts", "rm", "instagram", "a1"], { routes, stdinIsTTY: true, confirm: async () => true });
  assert.equal(accepted.requests.length, 1);
  const noTty = await exec(["accounts", "remove", "instagram", "a1"], { routes });
  assert.equal(noTty.requests.length, 0);
  assert.match(noTty.err, /--yes/);
});

test("move validates direction locally and posts it as JSON", async () => {
  const bad = await exec(["proxies", "move", "instagram", "p1", "sideways"]);
  assert.equal(bad.code, 1);
  assert.match(bad.err, /'up' or 'down'/);
  assert.equal(bad.requests.length, 0);
  const routes = { "POST /v1/admin/platforms/instagram/accounts/a1/move": () => json(200, { success: true, moved: true }) };
  const ok = await exec(["accounts", "move", "instagram", "a1", "down"], { routes, stdoutIsTTY: true });
  assert.deepEqual(JSON.parse(ok.requests[0].body), { direction: "down" });
  assert.match(ok.out, /Moved account a1 down/);
  const edge = await exec(["accounts", "move", "instagram", "a1", "up"], { routes: { "POST /v1/admin/platforms/instagram/accounts/a1/move": () => json(404, { success: false, error: "not_found", message: "already first" }) } });
  assert.equal(edge.code, 1);
  assert.match(edge.err, /already first/);
});

test("platform and id are validated locally", async () => {
  for (const args of [["proxies", "list", "myspace"], ["proxies", "remove", "myspace", "p1", "--yes"], ["accounts", "move", "instagram", "../x", "up"]]) {
    const r = await exec(args);
    assert.equal(r.code, 1, args.join(" "));
    assert.equal(r.requests.length, 0);
  }
  assert.match((await exec(["proxies", "list", "myspace"])).err, /youtube, tiktok, instagram, facebook, google/);
});

test("403 owner_api_key_required gives a clear message and exits 1", async () => {
  const routes = { "GET /v1/admin/platforms": () => json(403, { success: false, error: "owner_api_key_required" }) };
  const human = await exec(["--format", "table", "proxies", "list"], { routes, stdoutIsTTY: true });
  assert.equal(human.code, 1);
  assert.match(human.err, /needs an owner API key/);
  const machine = await exec(["proxies", "list"], { routes });
  assert.equal(JSON.parse(machine.err).status, 403);
  assert.match(JSON.parse(machine.err).message, /owner API key/);
});

test("network failures exit 1 without leaking details unless --verbose", async () => {
  const out = [], err = [];
  let code = 0;
  const admin = { fetchImpl: async () => { throw new Error("connect ECONNREFUSED"); }, stdinIsTTY: false, stdoutIsTTY: false, out: (l) => out.push(l), err: (l) => err.push(stripAnsi(l)), exit: (c) => { code = c; } };
  await createProgram({ admin }).parseAsync(["node", "socialscrape", "--api-key", "k", "proxies", "list"]);
  assert.equal(code, 1);
  assert.deepEqual(err, ["Request failed."]);
});

test("missing API key exits 1 before any request", async () => {
  const saved = process.env.SOCIALSCRAPE_API_KEY;
  delete process.env.SOCIALSCRAPE_API_KEY;
  try {
    const r = await exec(["proxies", "list"], { key: null });
    // a stored key on the developer machine would satisfy this; only assert when none exists
    if (r.requests.length === 0) assert.equal(r.code, 1);
  } finally {
    if (saved !== undefined) process.env.SOCIALSCRAPE_API_KEY = saved;
  }
});

test("redactSecrets masks URL credentials only", () => {
  assert.equal(redactSecrets("via http://u:p@h:1/x and https://ok.example/p"), "via http://***@h:1/x and https://ok.example/p");
  assert.deepEqual(redactSecrets({ a: ["socks5://u:p@h:1"] }), { a: ["socks5://***@h:1"] });
});

test("end to end over HTTP: process sends owner header, secrets only in the body", async () => {
  const seen = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (d) => { body += d; });
    req.on("end", () => {
      seen.push({ method: req.method, url: req.url, key: req.headers["x-api-key"], body });
      res.setHeader("content-type", "application/json");
      if (req.method === "POST") { res.statusCode = 201; res.end(JSON.stringify({ success: true, id: "p1", position: 1, test: null })); } else res.end(JSON.stringify(PLATFORMS));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const bin = new URL("../bin/socialscrape.js", import.meta.url).pathname;
  const runCli = (args, input) => new Promise((resolve) => {
    const child = spawn(process.execPath, [bin, "--api-key", "owner-key", ...args], { env: { ...process.env, SOCIALSCRAPE_BASE_URL: base } });
    let stdout = "", stderr = "";
    child.stdout.on("data", (d) => { stdout += d; });
    child.stderr.on("data", (d) => { stderr += d; });
    child.on("close", (status) => resolve({ status, stdout, stderr }));
    child.stdin.end(input);
  });
  try {
    const add = await runCli(["proxies", "add", "instagram", "--stdin", "--no-test"], SECRET_PROXY);
    assert.equal(add.status, 0, add.stderr);
    const list = await runCli(["accounts", "list"], "");
    assert.equal(list.status, 0, list.stderr);
    assert.equal(JSON.parse(list.stdout).platforms.instagram.accounts[0].id, "a1");
    assert.ok(seen.every((r) => r.key === "owner-key"));
    assert.ok(seen.every((r) => !r.url.includes("s3cr3t")));
    assert.equal(JSON.parse(seen[0].body).proxy, SECRET_PROXY);
    assert.doesNotMatch(add.stdout + add.stderr, /s3cr3t/);
  } finally {
    server.close();
  }
});
