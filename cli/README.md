# SocialScrape CLI

CLI for the [SocialScrape API](https://scrape.mimic.host) — ScrapeCreators-compatible YouTube, TikTok, and Instagram endpoints from the terminal and MCP configuration for AI agents.

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

The dynamic catalog provides all 66 SocialScrape YouTube, TikTok, and Instagram endpoints, plus account routes. Run `socialscrape list [platform]` and `socialscrape <platform> <command> --help` for details.

```bash
socialscrape balance
socialscrape config set defaultFormat table
socialscrape agent add cursor
socialscrape agent add claude
socialscrape agent add codex
socialscrape agent add vscode
```

Agent configuration writes a `socialscrape` MCP server using `${SOCIALSCRAPE_BASE_URL:-https://scrape.mimic.host}/mcp` with the `x-api-key` header, preserving unrelated servers.

## Security

Prefer the environment or stored config over putting secrets in shell history. Config and generated MCP files are mode `0600`; redirects are refused and responses are capped at 50 MiB.
