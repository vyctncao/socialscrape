# SocialScrape CLI

CLI for the [SocialScrape API](https://scrape.mimic.host) — ScrapeCreators-compatible social and ad-library endpoints for YouTube, TikTok, Instagram, Facebook, and Google from the terminal and MCP configuration for AI agents.

## Install

```bash
npm install -g @socialscrape/cli
```

Or run with `npx @socialscrape/cli`.

## Quick start

```bash
export SOCIALSCRAPE_API_KEY=YOUR_KEY
socialscrape youtube video --url https://www.youtube.com/watch?v=dQw4w9WgXcQ
socialscrape list
```

Authentication precedence is `--api-key`, `SOCIALSCRAPE_API_KEY`, then the platform-specific `conf` store. Save a key with `socialscrape auth login --provider api-key`; inspect or remove it with `auth status` and `auth logout`. Override the API origin for self-hosting with `SOCIALSCRAPE_BASE_URL`.

## Output

Global options must precede the command:

```bash
socialscrape --pretty youtube video --url URL
socialscrape --format table tiktok profile --handle HANDLE
socialscrape --format csv --output result.csv instagram profile --handle HANDLE
socialscrape --clean --format markdown youtube search --query cats
```

Formats are compact JSON (default), pretty JSON, table, CSV, and Markdown. `--output` writes only within the current directory and prints the path.

## Commands

The dynamic catalog provides all 74 SocialScrape YouTube, TikTok, Instagram, Facebook, and Google endpoints, plus account routes. Run `socialscrape list [platform]` and `socialscrape <platform> <command> --help` for details.

```bash
socialscrape balance
socialscrape config set defaultFormat table
socialscrape agent add cursor
socialscrape agent add claude
socialscrape agent add codex
socialscrape agent add vscode
```

Agent configuration writes a `socialscrape` MCP server using `${SOCIALSCRAPE_BASE_URL:-https://scrape.mimic.host}/mcp` with the `x-api-key` header, preserving unrelated servers.

## Manage proxies and accounts

Workspace owners can manage per-platform proxies and signed-in account cookies. These commands need an **owner API key** (other keys get `owner_api_key_required`). Platforms: `youtube`, `tiktok`, `instagram`, `facebook`, `google`; up to 5 entries per platform per kind, tried in position order (1 first).

```bash
socialscrape proxies list [platform]
printf '%s' "$PROXY_URL" | socialscrape proxies add instagram --stdin   # or run without --stdin for a hidden prompt
socialscrape proxies add instagram --no-test                            # skip the connectivity test
socialscrape proxies test instagram <id>      # or: proxies test instagram --all
socialscrape proxies move instagram <id> up   # up | down
socialscrape proxies remove instagram <id> [--yes]

socialscrape accounts list [platform]
socialscrape accounts add instagram --file cookies.txt                  # or --stdin
socialscrape accounts check instagram <id>    # or: accounts check instagram --all
socialscrape accounts move instagram <id> down
socialscrape accounts remove instagram <id> [--yes]
```

- Secrets never go on the command line. A proxy URL passed as an argument or option is refused; use `--stdin` or the hidden prompt. Cookie exports (Netscape `cookies.txt`, browser JSON, base64 JSON, or a `Cookie` header) come from `--file` or `--stdin`, up to 2 MiB, and are sent only in the request body. Contents are never printed, and credentials in any URL the server echoes back are masked.
- `list` prints a table (platform, kind, position, id, status, updated) in a terminal and JSON otherwise; `--json`, `--pretty`, `--format csv|markdown` work as usual. `test`/`check` and `add` print JSON when piped.
- `remove` asks for confirmation in a terminal; pass `--yes` to skip it (required when not interactive).
- Exit codes: `0` success; `1` usage errors, network errors, or HTTP errors (including 403 and 404); `2` the request completed but a proxy test or account check reported `ok: false` (also after `add`, where the entry is still saved, and for `--all` if any entry failed).

## Security

Prefer the environment or stored config over putting secrets in shell history. Config and generated MCP files are mode `0600`; redirects are refused and responses are capped at 50 MiB.
