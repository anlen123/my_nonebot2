# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A QQ bot built on [NoneBot 2](https://v2.nonebot.dev/) framework, connecting to QQ via the go-cqhttp protocol bridge (OneBot v11 adapter). Provides AI conversations (ChatGPT, DeepSeek, Gemini), entertainment, media search, and game info queries.

## Commands

```bash
# Install dependencies
poetry install

# Run bot (development)
poetry run python bot.py
# or
nb run

# Run bot in background (tmux-based)
bash run.sh

# Kill running bot processes
bash kill.sh

# Docker (production)
docker-compose up -d
```

There is no automated test suite. Testing is done manually by interacting with the bot.

## Architecture

### Entry Point

`bot.py` initializes NoneBot with FastAPI driver and OneBot v11 adapter, then loads plugins two ways:
1. Plugins listed in `pyproject.toml` under `[tool.nonebot]`
2. Direct `nonebot.load_plugin()` calls for local plugins (e.g. `plugins.deepseek_gpt`)

### Plugin Structure

All plugins live in `plugins/`. Each plugin is a directory with:
- `__init__.py` — main logic (event handlers, command definitions)
- `config.py` — plugin config via Pydantic `BaseSettings` (optional)

NoneBot event handler patterns used across plugins:
- `on_regex()` — regex pattern matching
- `on_command()` — command parsing
- `on_keyword()` — keyword detection
- `to_me()` — requires @mention

### Environment Configuration

Two env files control runtime behavior:
- `.env.dev` — development (port 8899, no Redis, Windows data paths)
- `.env.prod` — production (Redis at 127.0.0.1:6379, multiple QQ accounts, OpenAI API keys)

NoneBot loads the env file based on `ENVIRONMENT` variable. Sensitive keys (OpenAI, DeepSeek, Pixiv cookies) live only in `.env.*` files.

### External Services Required

The bot depends on several external services that must be running:
- **go-cqhttp** — QQ protocol bridge (configured via `config.yml`), sign server at `http://127.0.0.1:7701`
- **Meilisearch** — conversation history search for ChatGPT plugin (port 7700)
- **Redis** — production only (port 6379)

### Data Storage

- `chat_history.db` — ChatGPT conversation history (SQLite)
- `chat_gemini_history.db` — Gemini conversation history (SQLite)
- `data/pokemon/` — local Pokemon data (JSON + images), paths configured in `.env`
- `data/custom_emote_data/` — custom emoji storage

### Key Plugins

| Plugin | Commands | Notes |
|--------|----------|-------|
| `deepseek_gpt` | `ds3`, `dsr`, `翻译`/`fy` | Uses ByteDance Ark API |
| `chat_gpt` | `gpt3`, `gpt4`, `chat`, `chat4` | OpenAI API + Meilisearch history |
| `gemini_gpt` | `gm`, `gmt`, `gmi` | Google Gemini, supports image input |
| `nonebot_plugin_masterduel` | `ygo`, `ck` | Yu-Gi-Oh! card query from local DB |
| `nonebot_plugin_pokemon` | — | Pokemon info from local JSON data |
| `nonebot_plugin_pixiv` | `pixiv <PID>` | Requires Pixiv cookie in `.env` |
| `nonebot_plugin_xuanran` | `xr <URL>` | Renders webpage to image via Selenium |
| `jm` | — | Manga downloader, permission-gated |
| `love` | `ll`, `love`, `菜单` | Basic commands + forward message helper |

### Adding a New Plugin

1. Create `plugins/your_plugin/__init__.py` with NoneBot handlers
2. Add `nonebot.load_plugin("plugins.your_plugin")` in `bot.py`
3. If the plugin needs config, add a `config.py` with a `BaseSettings` class and merge it in `__init__.py` via `Config.parse_obj(global_config.dict())`
