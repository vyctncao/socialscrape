import * as prompts from "@clack/prompts";
import chalk from "chalk";
import { resolveApiKeyWithSource, storeApiKey, clearApiKey, maskKey } from "../auth.js";
import { callApi } from "../api-client.js";
export async function authLogin(options={}) {
 if(options.provider && options.provider!=="api-key"){console.error("Only --provider api-key is supported.");process.exitCode=1;return}
 let key=process.env.SOCIALSCRAPE_API_KEY;
 if(process.stdin.isTTY){key=await prompts.password({message:"Enter your API key",validate:v=>v?.trim().length>=5?undefined:"Enter a valid API key (at least 5 characters)"});if(prompts.isCancel(key))return}
 if(!key){console.error("Set SOCIALSCRAPE_API_KEY for non-interactive login.");process.exitCode=1;return}
 const result=await callApi(key.trim(),"GET","/v1/account/credit-balance"); if(!result.ok){console.error("Invalid API key.");process.exitCode=1;return} storeApiKey(key.trim()); console.log("Authenticated. API key saved.");
}
export async function authStatus(options={}){const {key,source}=resolveApiKeyWithSource(options);if(!key){console.log(chalk.yellow("Not authenticated. Run 'socialscrape auth login'."));return}console.log(`API key: ${maskKey(key)} (from ${source})`)}
export function authLogout(){clearApiKey();console.log(chalk.green("API key removed from stored config."))}
