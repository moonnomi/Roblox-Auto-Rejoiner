# Roblox Auto-Rejoiner

> [!NOTE]
> This repository is archived and read-only. The project remains available as-is, including the network-aware recovery added for issue #2, but no further maintenance is planned.

> [!IMPORTANT]
> This project supports the Windows Roblox client only.

Roblox Auto-Rejoiner watches the desktop Roblox client and automatically rejoins a configured Place when the client:

- crashes or closes unexpectedly;
- stops responding;
- reports a connection loss, kick, or idle disconnect in its Player log.

If the connection is offline, recovery waits until a Roblox endpoint is reachable. If Roblox does not start after a launch request, the monitor retries instead of silently giving up.

An optional Discord bot provides remote status, screenshots, incident notifications, pause/resume controls, and manual rejoins.

## Install

Install Python 3.10 or newer, then run:

```cmd
python -m pip install -r requirements.txt
```

## Configure and run

The easiest option is the GUI:

```cmd
python main_gui.py
```

Enter the Roblox Place ID, rejoin delay, Discord bot token, and notification channel ID, then save the configuration. Settings are stored in a local `config.json`; this file contains the bot token and should never be committed or shared.

The monitor and bot can also be run separately:

```cmd
python roblox_monitor.py
python discord_bot.py
```

Running either service for the first time creates `config.json` beside the scripts. At minimum, replace `PUT_PLACE_ID_HERE` with a numeric Place ID. The Discord fields are optional unless the bot is used.

## Discord bot setup

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Add a bot and copy its token into the GUI or `config.json`.
3. Invite it with the `bot` and `applications.commands` scopes and grant permission to send messages and attach files.
4. Set `CHANNEL_ID` for automatic incident notifications. Setting `GUILD_ID` is optional and makes slash-command updates appear immediately in that server.

Available commands:

| Command | Purpose |
| --- | --- |
| `/status` | Show monitor state and optionally attach a screenshot |
| `/current_screen` | Capture the Roblox window |
| `/current_game` | Show the configured experience and link |
| `/placeid` | Show the configured Place ID |
| `/crashes` | Show crash, freeze, and disconnect history |
| `/uptime` | Show monitor uptime |
| `/pause` | Pause automatic monitoring and recovery |
| `/resume` | Resume monitoring |
| `/rejoin` | Force a clean rejoin |

## How disconnect recovery works

The monitor tails only newly written data from the latest Roblox Player log under `%LOCALAPPDATA%\Roblox\logs`. Existing disconnect messages are skipped on startup so an old incident cannot cause a false rejoin. When a new disconnect marker appears, Roblox is closed, the configured delay is applied, connectivity is checked, and launch attempts continue until Roblox starts or monitoring is stopped.

## Build the Windows executable

Run `build.cmd`. PyInstaller writes the executable to `dist\main_gui.exe`.

## Disclaimer

This project is not affiliated with, maintained, authorized, endorsed, or sponsored by Roblox Corporation. Automated rejoining may violate an individual experience's rules or platform policies. Use it at your own risk.
