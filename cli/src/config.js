import Conf from "conf";
import { chmodSync } from "node:fs";
const config = new Conf({ projectName: "socialscrape", configFileMode: 0o600, schema: {
  apiKey: { type: "string", default: "" },
  defaultFormat: { type: "string", default: "auto", enum: ["auto", "json", "table", "csv", "markdown"] }
}});
try { chmodSync(config.path, 0o600); } catch {}
export default config;
