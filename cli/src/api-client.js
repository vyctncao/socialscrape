export const MAX_RESPONSE_BYTES = 50 * 1024 * 1024;
export function apiBaseUrl() {
  const raw = process.env.SOCIALSCRAPE_BASE_URL || "https://scrape.mimic.host";
  const url = new URL(raw); if (url.username || url.password || url.hash) throw new Error("Invalid SOCIALSCRAPE_BASE_URL");
  return url.toString().replace(/\/$/, "");
}
export async function readBodyWithLimit(res) {
  const length = Number(res.headers.get("content-length")); if (length > MAX_RESPONSE_BYTES) throw new Error("Response exceeds 50 MiB limit");
  const chunks=[]; let total=0; for await (const chunk of res.body) { total += chunk.length; if(total>MAX_RESPONSE_BYTES) throw new Error("Response exceeds 50 MiB limit"); chunks.push(chunk); } return Buffer.concat(chunks).toString();
}
export async function callApi(apiKey, method, path, params={}, { signal, fetchImpl=globalThis.fetch }={}) {
 const url=new URL(apiBaseUrl()+path); if(method==="GET") for(const [k,v] of Object.entries(params)) if(v!==undefined&&v!==null&&v!=="") url.searchParams.set(k,String(v));
 const options={method,headers:{"x-api-key":apiKey,accept:"application/json"},redirect:"error",signal:signal?AbortSignal.any([signal,AbortSignal.timeout(60000)]):AbortSignal.timeout(60000)};
 if(method!=="GET"&&Object.keys(params).length){options.headers["content-type"]="application/json";options.body=JSON.stringify(params)}
 const start=Date.now(),res=await fetchImpl(url,options),text=await readBodyWithLimit(res); let data; try{data=JSON.parse(text)}catch{data=text} return {ok:res.ok,status:res.status,data,elapsed:Date.now()-start,url:url.toString()};
}
