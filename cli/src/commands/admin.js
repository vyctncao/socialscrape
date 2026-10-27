import * as prompts from "@clack/prompts";
import chalk from "chalk";
import Table from "cli-table3";
import ora from "ora";
import { closeSync, fstatSync, openSync, readSync } from "node:fs";
import { resolveApiKey } from "../auth.js";
import { callApi } from "../api-client.js";
import { formatOutput, getSuggestion, safePath, sanitizeData, stripAnsi, writeOutputFile } from "../output.js";

export const ADMIN_PLATFORMS = ["youtube", "tiktok", "instagram", "facebook", "google"];
export const DIRECTIONS = ["up", "down"];
export const MAX_COOKIE_BYTES = 2 * 1024 * 1024;
export const MAX_PROXY_BYTES = 8 * 1024;
const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
const KIND_LABEL = { proxies: "proxy", accounts: "account" };

// Exit codes: 0 success; 1 usage, network, or HTTP error; 2 the request completed but the test/check reported failure.
export const EXIT_ERROR = 1;
export const EXIT_CHECK_FAILED = 2;

function defaultContext() {
  return {
    fetchImpl: globalThis.fetch,
    stdin: process.stdin,
    stdinIsTTY: Boolean(process.stdin.isTTY),
    stdoutIsTTY: Boolean(process.stdout.isTTY),
    out: (line) => console.log(line),
    err: (line) => console.error(line),
    exit: (code) => { process.exitCode = Math.max(process.exitCode || 0, code); },
    promptSecret: async (message) => {
      const value = await prompts.password({ message });
      return prompts.isCancel(value) ? null : value;
    },
    confirm: async (message) => {
      const value = await prompts.confirm({ message, initialValue: false });
      return prompts.isCancel(value) ? false : value;
    },
    spinner: (text) => ora({ text, stream: process.stderr }).start(),
  };
}

// Strip credentials from any URL-looking text before it reaches the terminal, a file, or JSON output.
export function redactSecrets(value) {
  if (typeof value === "string") return value.replace(/(\b[a-z][a-z0-9+.-]*:\/\/)[^\s/@"'<>]+@/gi, "$1***@");
  if (Array.isArray(value)) return value.map(redactSecrets);
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, redactSecrets(v)]));
  return value;
}

const clean = (data) => redactSecrets(sanitizeData(data));

// Never echo user input that could be (or contain) a secret; only echo short identifier-like tokens.
function shown(value) {
  const text = String(value ?? "");
  return /^[A-Za-z0-9_.-]{1,32}$/.test(text) ? `'${text}'` : "(value hidden)";
}

class UsageError extends Error {}

function validatePlatform(platform) {
  if (!ADMIN_PLATFORMS.includes(platform)) {
    throw new UsageError(`Unknown platform ${shown(platform)}. Choose one of: ${ADMIN_PLATFORMS.join(", ")}.`);
  }
  return platform;
}

function validateId(id) {
  if (!ID_RE.test(String(id ?? ""))) throw new UsageError(`Invalid id ${shown(id)}. Run the list command to see ids.`);
  return String(id);
}

function validateDirection(direction) {
  if (!DIRECTIONS.includes(direction)) throw new UsageError(`Invalid direction ${shown(direction)}. Use 'up' or 'down'.`);
  return direction;
}

function refuseExtraArgs(cmd, expected, hint) {
  if ((cmd.args?.length ?? 0) > expected) throw new UsageError(hint);
}

const SECRET_ARG_HINT = {
  proxies: "A proxy URL cannot be passed as an argument or option because it would land in your shell history and process list.\nPipe it instead:  printf '%s' \"$PROXY_URL\" | socialscrape proxies add <platform> --stdin\nor run 'socialscrape proxies add <platform>' in a terminal for a hidden prompt.",
  accounts: "Cookies cannot be passed as an argument or option because they would land in your shell history and process list.\nUse --file <path> or --stdin.",
};

async function readLimitedStream(stream, limit) {
  const chunks = [];
  let total = 0;
  for await (const chunk of stream) {
    const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    total += buf.length;
    if (total > limit) throw new UsageError(`Input exceeds the ${formatBytes(limit)} limit.`);
    chunks.push(buf);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function readLimitedFile(path, limit) {
  let fd;
  try {
    fd = openSync(path, "r");
    const stat = fstatSync(fd);
    if (!stat.isFile()) throw new UsageError("--file must point to a regular file.");
    if (stat.size > limit) throw new UsageError(`File exceeds the ${formatBytes(limit)} limit.`);
    const buf = Buffer.alloc(limit + 1);
    let total = 0;
    for (;;) {
      const n = readSync(fd, buf, total, buf.length - total, null);
      if (n === 0) break;
      total += n;
      if (total > limit) throw new UsageError(`File exceeds the ${formatBytes(limit)} limit.`);
    }
    return buf.subarray(0, total).toString("utf8");
  } catch (err) {
    if (err instanceof UsageError) throw err;
    throw new UsageError(`Cannot read file (${err.code || "error"}).`);
  } finally {
    if (fd !== undefined) closeSync(fd);
  }
}

function formatBytes(n) {
  return n >= 1024 * 1024 ? `${n / 1024 / 1024} MiB` : `${n / 1024} KiB`;
}

function isHumanMode(c, opts) {
  if (opts.json || opts.pretty) return false;
  const fmt = opts.format && opts.format !== "auto" ? opts.format : null;
  if (fmt) return fmt === "table";
  return c.stdoutIsTTY;
}

function emitData(c, data, opts) {
  const fmt = opts.pretty ? "pretty" : opts.json ? "json" : opts.format && opts.format !== "auto" ? opts.format : "json";
  const output = formatOutput(clean(data), fmt, opts);
  if (opts.output) {
    writeOutputFile(safePath(opts.output), output);
    c.out(opts.output);
  } else {
    c.out(output);
  }
}

function reportHttpError(c, result, opts) {
  const data = clean(result.data);
  let message = typeof data === "object" && data ? data.message || data.error || JSON.stringify(data) : String(data);
  let suggestion = getSuggestion(result.status);
  if (result.status === 403 && data?.error === "owner_api_key_required") {
    message = "This command needs an owner API key (owner_api_key_required).";
    suggestion = "Use the workspace owner's key via --api-key, SOCIALSCRAPE_API_KEY, or 'socialscrape auth login'.";
  } else if (result.status === 404 && data?.error === "not_found") {
    suggestion = "Run 'socialscrape proxies list' or 'socialscrape accounts list' to see current ids.";
  }
  const machine = opts.json || opts.pretty || (opts.format && opts.format !== "auto" && opts.format !== "table") || !c.stdoutIsTTY;
  if (machine) {
    c.err(JSON.stringify({ error: true, code: `HTTP_${result.status}`, message, status: result.status, suggestion }, null, opts.pretty ? 2 : 0));
  } else {
    c.err(chalk.red(`Error ${result.status}: ${message}`));
    if (suggestion) c.err(chalk.yellow(`Suggestion: ${suggestion}`));
  }
  c.exit(EXIT_ERROR);
}

async function call(c, opts, method, path, body) {
  try {
    const result = await callApi(c.apiKey, method, path, body ?? {}, { fetchImpl: c.fetchImpl });
    if (!result.ok) { reportHttpError(c, result, opts); return null; }
    if (opts.verbose && c.stdoutIsTTY) c.err(chalk.dim(`${result.status} ${method} ${path} (${result.elapsed}ms)`));
    return result;
  } catch (err) {
    c.err(chalk.red("Request failed."));
    if (opts.verbose) c.err(chalk.dim(redactSecrets(String(err.message))));
    c.exit(EXIT_ERROR);
    return null;
  }
}

async function withSpinner(c, opts, text, fn) {
  const spinner = c.stdoutIsTTY && isHumanMode(c, opts) && c.spinner ? c.spinner(text) : null;
  try { return await fn(); } finally { spinner?.stop(); }
}

function setup(c, opts) {
  const apiKey = resolveApiKey(opts);
  if (!apiKey) {
    c.err(chalk.red("No API key found."));
    c.err(chalk.yellow("Run 'socialscrape auth login' or set SOCIALSCRAPE_API_KEY (an owner key is required)."));
    c.exit(EXIT_ERROR);
    return false;
  }
  c.apiKey = apiKey;
  return true;
}

// ---------- rendering ----------

function entryStatus(kind, entry) {
  if (kind !== "accounts") return "-";
  const h = entry.health;
  if (!h) return "unknown";
  return h.account ? `${h.status} (${h.account})` : String(h.status ?? "unknown");
}

export function flattenPlatforms(platforms, kinds = ["proxies", "accounts"], only) {
  const rows = [];
  for (const platform of ADMIN_PLATFORMS) {
    if (only && platform !== only) continue;
    const group = platforms?.[platform] || {};
    for (const kind of kinds) {
      const entries = [...(group[kind] || [])].sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
      for (const e of entries) rows.push({ platform, kind: KIND_LABEL[kind], position: e.position, id: e.id, status: entryStatus(kind, e), updated_at: e.updated_at ?? "" });
    }
  }
  return rows;
}

function filterPlatforms(data, kinds, only) {
  const platforms = {};
  for (const platform of ADMIN_PLATFORMS) {
    if (only && platform !== only) continue;
    const group = data.platforms?.[platform] || {};
    platforms[platform] = Object.fromEntries(kinds.map((k) => [k, group[k] || []]));
  }
  return { ...data, platforms };
}

function renderTable(rows) {
  rows = clean(rows);
  const table = new Table({ head: ["platform", "kind", "position", "id", "status", "updated"].map((h) => chalk.cyan(h)) });
  const when = (value) => (typeof value === "number" ? new Date(value * 1000).toLocaleString() : String(value ?? ""));
  for (const r of rows) table.push([r.platform, r.kind, String(r.position ?? ""), r.id, r.status, when(r.updated_at)]);
  return table.toString();
}

function describeCheck(raw) {
  const res = clean(raw);
  const parts = [];
  if (res.status !== undefined && res.status !== null) parts.push(`status ${res.status}`);
  if (res.connected !== undefined) parts.push(res.connected ? "connected" : "not connected");
  if (res.blocked) parts.push("blocked");
  if (res.account) parts.push(`account ${res.account}`);
  if (res.limited) parts.push("limited");
  if (res.exit_ip) parts.push(`exit ip ${res.exit_ip}`);
  if (res.latency_ms !== undefined && res.latency_ms !== null) parts.push(`${res.latency_ms} ms`);
  if (res.reason) parts.push(String(res.reason));
  if (res.error) parts.push(`error ${res.error}`);
  if (res.message) parts.push(String(res.message));
  return parts.join(", ");
}

function formatOutcome(label, res) {
  const detail = describeCheck(res);
  const head = res.ok ? chalk.green(`${label}: OK`) : chalk.red(`${label}: FAILED`);
  return detail ? `${head} (${detail})` : head;
}

// ---------- commands ----------

export async function adminList(kind, platform, opts, c) {
  if (platform !== undefined) validatePlatform(platform);
  if (!setup(c, opts)) return;
  const result = await withSpinner(c, opts, "Fetching...", () => call(c, opts, "GET", "/v1/admin/platforms"));
  if (!result) return;
  const data = result.data;
  const kinds = [kind];
  const fmt = opts.pretty ? "pretty" : opts.json ? "json" : opts.format && opts.format !== "auto" ? opts.format : null;
  if (fmt === "json" || fmt === "pretty" || (!fmt && !c.stdoutIsTTY)) {
    emitData(c, filterPlatforms(data, kinds, platform), opts);
    return;
  }
  const rows = flattenPlatforms(data.platforms, kinds, platform);
  if (fmt && fmt !== "table") { emitData(c, rows, opts); return; }
  if (!rows.length) {
    c.out(chalk.dim(`No ${kind} configured${platform ? ` for ${platform}` : ""}.`));
    return;
  }
  c.out(renderTable(rows));
  if (data.max_entries) c.out(chalk.dim(`Up to ${data.max_entries} ${kind} per platform. Order is priority: position 1 is tried first.`));
}

async function addEntry(kind, platform, body, opts, c) {
  const label = KIND_LABEL[kind];
  const result = await withSpinner(c, opts, `Adding ${label}...`, () => call(c, opts, "POST", `/v1/admin/platforms/${platform}/${kind}`, body));
  if (!result) return;
  const outcome = kind === "proxies" ? result.data.test : result.data.check;
  const failed = outcome && outcome.ok === false;
  if (!isHumanMode(c, opts)) {
    emitData(c, result.data, opts);
  } else {
    c.out(chalk.green(`Added ${label} ${result.data.id} for ${platform} at position ${result.data.position}.`));
    if (outcome) c.out(formatOutcome(kind === "proxies" ? "Test" : "Check", outcome));
    if (failed) c.out(chalk.yellow(`The ${label} was saved but the ${kind === "proxies" ? "test" : "check"} failed.`));
  }
  if (failed) c.exit(EXIT_CHECK_FAILED);
}

export async function proxiesAdd(platform, options, opts, cmd, c) {
  validatePlatform(platform);
  refuseExtraArgs(cmd, 1, SECRET_ARG_HINT.proxies);
  let proxy;
  if (options.stdin) {
    proxy = (await readLimitedStream(c.stdin, MAX_PROXY_BYTES)).trim();
  } else if (c.stdinIsTTY) {
    proxy = (await c.promptSecret("Proxy URL (e.g. http://user:pass@host:port)"))?.trim();
    if (proxy === undefined || proxy === null) throw new UsageError("Cancelled.");
  } else {
    throw new UsageError("No terminal available for the hidden prompt. Pipe the proxy URL with --stdin.");
  }
  if (!proxy) throw new UsageError("The proxy URL is empty.");
  if (/[\r\n]/.test(proxy)) throw new UsageError("The proxy input must be a single line.");
  if (!setup(c, opts)) return;
  await addEntry("proxies", platform, { proxy, test: options.test !== false }, opts, c);
}

export async function accountsAdd(platform, options, opts, cmd, c) {
  validatePlatform(platform);
  refuseExtraArgs(cmd, 1, SECRET_ARG_HINT.accounts);
  if (Boolean(options.file) === Boolean(options.stdin)) throw new UsageError("Provide exactly one of --file <path> or --stdin.");
  const cookies = options.file ? readLimitedFile(options.file, MAX_COOKIE_BYTES) : await readLimitedStream(c.stdin, MAX_COOKIE_BYTES);
  if (!cookies.trim()) throw new UsageError("The cookie input is empty.");
  if (!setup(c, opts)) return;
  await addEntry("accounts", platform, { cookies }, opts, c);
}

async function allIds(kind, platform, opts, c) {
  const result = await withSpinner(c, opts, "Fetching...", () => call(c, opts, "GET", "/v1/admin/platforms"));
  if (!result) return null;
  const entries = [...(result.data.platforms?.[platform]?.[kind] || [])].sort((a, b) => (a.position ?? 0) - (b.position ?? 0));
  return entries.map((e) => e.id);
}

// Runs `test` (proxies) or `check` (accounts) for one id or every id on the platform.
export async function adminVerify(kind, platform, id, options, opts, c) {
  validatePlatform(platform);
  const action = kind === "proxies" ? "test" : "check";
  if (options.all && id !== undefined) throw new UsageError(`Pass either an id or --all, not both.`);
  if (!options.all && id === undefined) throw new UsageError(`Pass an id or --all. Example: socialscrape ${kind} ${action} ${platform} --all`);
  if (id !== undefined) validateId(id);
  if (!setup(c, opts)) return;
  const ids = options.all ? await allIds(kind, platform, opts, c) : [id];
  if (!ids) return;
  const human = isHumanMode(c, opts);
  if (!ids.length) {
    if (human) c.out(chalk.dim(`No ${kind} configured for ${platform}.`));
    else emitData(c, { success: true, results: [] }, opts);
    return;
  }
  const results = [];
  let requestFailed = false;
  for (const entryId of ids) {
    const path = `/v1/admin/platforms/${platform}/${kind}/${encodeURIComponent(entryId)}/${action}`;
    const result = await withSpinner(c, opts, `${action === "test" ? "Testing" : "Checking"} ${entryId}...`, () => call(c, opts, "POST", path));
    if (!result) { requestFailed = true; results.push({ id: entryId, ok: false, error: "request_failed" }); continue; }
    const { success: _success, ...rest } = result.data;
    results.push({ id: entryId, ...rest });
    if (human) c.out(formatOutcome(`${platform} ${KIND_LABEL[kind]} ${entryId}`, rest));
    if (rest.ok === false) c.exit(EXIT_CHECK_FAILED);
  }
  if (!human) emitData(c, options.all ? { success: !requestFailed, results } : { success: !requestFailed, ...results[0] }, opts);
  else if (options.all) {
    const failed = results.filter((r) => r.ok === false).length;
    c.out(failed ? chalk.red(`${failed} of ${results.length} failed.`) : chalk.green(`All ${results.length} passed.`));
  }
}

export async function adminRemove(kind, platform, id, options, opts, c) {
  validatePlatform(platform);
  validateId(id);
  const label = KIND_LABEL[kind];
  if (!options.yes) {
    if (!c.stdinIsTTY) throw new UsageError(`Refusing to remove without confirmation. Re-run with --yes to remove ${label} ${id} from ${platform}.`);
    if (!(await c.confirm(`Remove ${label} ${id} from ${platform}?`))) { c.err("Cancelled."); c.exit(EXIT_ERROR); return; }
  }
  if (!setup(c, opts)) return;
  const result = await withSpinner(c, opts, "Removing...", () => call(c, opts, "DELETE", `/v1/admin/platforms/${platform}/${kind}/${encodeURIComponent(id)}`));
  if (!result) return;
  if (!isHumanMode(c, opts)) emitData(c, result.data, opts);
  else c.out(chalk.green(`Removed ${label} ${id} from ${platform}.`));
}

export async function adminMove(kind, platform, id, direction, opts, c) {
  validatePlatform(platform);
  validateId(id);
  validateDirection(direction);
  if (!setup(c, opts)) return;
  const result = await withSpinner(c, opts, "Moving...", () => call(c, opts, "POST", `/v1/admin/platforms/${platform}/${kind}/${encodeURIComponent(id)}/move`, { direction }));
  if (!result) return;
  if (!isHumanMode(c, opts)) emitData(c, result.data, opts);
  else c.out(chalk.green(`Moved ${KIND_LABEL[kind]} ${id} ${direction} on ${platform}.`));
}

// ---------- registration ----------

export function registerAdminCommands(program, getOpts, overrides = {}) {
  const context = () => ({ ...defaultContext(), ...overrides });
  const run = (fn) => async (...actionArgs) => {
    const c = context();
    try {
      await fn(c, getOpts(), ...actionArgs);
    } catch (err) {
      if (!(err instanceof UsageError)) {
        c.err(chalk.red("Command failed."));
        if (getOpts().verbose) c.err(chalk.dim(redactSecrets(stripAnsi(String(err.message)))));
      } else {
        c.err(chalk.red(err.message));
      }
      c.exit(EXIT_ERROR);
    }
  };

  const proxies = program.command("proxies").description("manage per-platform proxies (owner API key required)");
  proxies.command("list [platform]").description("list proxies in priority order").action(run((c, o, platform) => adminList("proxies", platform, o, c)));
  proxies.command("add <platform>").description("add a proxy; the URL is read from a hidden prompt or --stdin, never from arguments")
    .option("--stdin", "read the proxy URL from standard input").option("--no-test", "skip the connectivity test after saving")
    .allowUnknownOption().allowExcessArguments()
    .action(run((c, o, platform, options, cmd) => proxiesAdd(platform, options, o, cmd, c)));
  proxies.command("test <platform> [id]").description("test one proxy, or every proxy for the platform with --all").option("--all", "test every proxy for the platform")
    .action(run((c, o, platform, id, options) => adminVerify("proxies", platform, id, options, o, c)));
  proxies.command("remove <platform> <id>").alias("rm").description("remove a proxy").option("-y, --yes", "skip the confirmation prompt")
    .action(run((c, o, platform, id, options) => adminRemove("proxies", platform, id, options, o, c)));
  proxies.command("move <platform> <id> <direction>").description("change priority: up or down").action(run((c, o, platform, id, direction) => adminMove("proxies", platform, id, direction, o, c)));

  const accounts = program.command("accounts").description("manage signed-in account cookies per platform (owner API key required)");
  accounts.command("list [platform]").description("list accounts with health in priority order").action(run((c, o, platform) => adminList("accounts", platform, o, c)));
  accounts.command("add <platform>").description("add an account from a cookie export (Netscape cookies.txt, JSON, base64 JSON, or Cookie header)")
    .option("--file <path>", "read the cookie export from a file (max 2 MiB)").option("--stdin", "read the cookie export from standard input (max 2 MiB)")
    .allowUnknownOption().allowExcessArguments()
    .action(run((c, o, platform, options, cmd) => accountsAdd(platform, options, o, cmd, c)));
  accounts.command("check <platform> [id]").description("check one account, or every account for the platform with --all").option("--all", "check every account for the platform")
    .action(run((c, o, platform, id, options) => adminVerify("accounts", platform, id, options, o, c)));
  accounts.command("remove <platform> <id>").alias("rm").description("remove an account").option("-y, --yes", "skip the confirmation prompt")
    .action(run((c, o, platform, id, options) => adminRemove("accounts", platform, id, options, o, c)));
  accounts.command("move <platform> <id> <direction>").description("change priority: up or down").action(run((c, o, platform, id, direction) => adminMove("accounts", platform, id, direction, o, c)));
}
