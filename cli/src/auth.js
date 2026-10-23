import config from "./config.js";
import { chmodSync } from "node:fs";

export function resolveApiKeyWithSource(opts = {}) {
  if (opts.apiKey) return { key: opts.apiKey, source: "flag" };
  const stored = config.get("apiKey");
  if (stored) return { key: stored, source: "stored config" };
  if (process.env.SOCIALSCRAPE_API_KEY) {
    return { key: process.env.SOCIALSCRAPE_API_KEY, source: "environment variable" };
  }
  return { key: null, source: null };
}

export function resolveApiKey(opts = {}) { return resolveApiKeyWithSource(opts).key; }
export function storeApiKey(key) { config.set("apiKey", key); chmodSync(config.path, 0o600); }
export function clearApiKey() { config.delete("apiKey"); }
export function getStoredApiKey() { return config.get("apiKey") || null; }
export function maskKey(key) { if (!key) return "(not set)"; return key.length <= 12 ? key.slice(0,2)+"…"+key.slice(-2) : key.slice(0,6)+"…"+key.slice(-4); }
