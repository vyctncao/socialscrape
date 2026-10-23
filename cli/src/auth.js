import config from "./config.js";
import { chmodSync } from "node:fs";
export function resolveApiKey(opts = {}) { return opts.apiKey || process.env.SOCIALSCRAPE_API_KEY || config.get("apiKey") || null; }
export function storeApiKey(key) { config.set("apiKey", key); chmodSync(config.path, 0o600); }
export function clearApiKey() { config.delete("apiKey"); }
export function getStoredApiKey() { return config.get("apiKey") || null; }
export function maskKey(key) { if (!key) return "(not set)"; return key.length <= 12 ? key.slice(0,2)+"…"+key.slice(-2) : key.slice(0,6)+"…"+key.slice(-4); }
