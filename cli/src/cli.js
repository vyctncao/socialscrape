import { Command, Option } from "commander";
import { readFileSync } from "node:fs";
import config from "./config.js";
import { registerApiCommands } from "./command-registry.js";
import { authLogin, authStatus, authLogout } from "./commands/auth.js";
import { configSet, configGet, configList } from "./commands/config.js";
import { balanceCommand } from "./commands/balance.js";
import { listCommand } from "./commands/list.js";
import { agentAddCommand } from "./commands/agent.js";
import { runInteractive } from "./interactive.js";
const {version}=JSON.parse(readFileSync(new URL("../package.json",import.meta.url),"utf8"));
export function createProgram(){
 const program=new Command().name("socialscrape").description("CLI for the SocialScrape API — ScrapeCreators-compatible social and ad-library endpoints for YouTube, TikTok, Instagram, Facebook, and Google").version(version)
 .option("--api-key <key>","API key (overrides env and config)").option("--format <format>","output format: json, table, csv, markdown","auto").option("--json","shorthand for compact JSON (default)").option("--pretty","pretty-print JSON with indentation").option("--output <path>","save response to file, print path only").option("--clean","strip noisy fields: booleans, empty values, settings (any format)").option("--no-color","disable ANSI colors").option("--verbose","show request URL, timing, status code");
 const opts=()=>{const o=program.opts();if(o.format==="auto")o.format=config.get("defaultFormat")||"auto";return o}; registerApiCommands(program,opts);
 const auth=program.command("auth").description("manage authentication");auth.command("login").alias("signup").description("sign in or sign up with an existing API key").addOption(new Option("--provider <provider>","api-key").choices(["api-key"])).option("--no-browser","accepted for compatibility; has no effect").action(authLogin);auth.command("status").description("show current auth status").action(()=>authStatus(opts()));auth.command("logout").description("remove stored API key").action(authLogout);
 program.command("balance").description("show SocialScrape credit policy").action(()=>balanceCommand(opts()));const c=program.command("config").description("manage CLI configuration");c.command("set <key> <value>").description("set a config value").action(configSet);c.command("get <key>").description("get a config value").action(configGet);c.command("list").description("show all config values").action(configList);program.command("list [platform]").description("list available platforms and endpoints").action(listCommand);const a=program.command("agent").description("configure AI agent integrations");a.command("add <target>").description("write MCP config into an agent (cursor, claude, codex, vscode)").action(t=>agentAddCommand(t,opts()));return program;
}
export function run(argv){if(argv.length<=2&&process.stdin.isTTY){runInteractive({});return}createProgram().parseAsync(argv)}
