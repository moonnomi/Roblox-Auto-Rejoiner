import asyncio
import json
import os
import socket
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import tasks

from config_manager import load_config


BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"
GUILD_ID = None
CHANNEL_ID = None
SOCKET_HOST = "127.0.0.1"
SOCKET_PORT = 45678
SOCKET_AUTH = ""

COLOR_OK = 0x57F287
COLOR_WARN = 0xFEE75C
COLOR_ERR = 0xED4245
COLOR_INFO = 0x5865F2
MAX_MONITOR_RESPONSE = 1024 * 1024
SOCKET_TIMEOUT = 2


def _optional_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def reload_bot_config() -> None:
    global BOT_TOKEN, GUILD_ID, CHANNEL_ID
    global SOCKET_HOST, SOCKET_PORT, SOCKET_AUTH, guild_obj

    config = load_config()
    BOT_TOKEN = str(config.get("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")).strip()
    GUILD_ID = _optional_int(config.get("GUILD_ID"))
    CHANNEL_ID = _optional_int(config.get("CHANNEL_ID"))
    SOCKET_HOST = str(config.get("SOCKET_HOST", "127.0.0.1"))
    SOCKET_PORT = _optional_int(config.get("SOCKET_PORT")) or 45678
    SOCKET_AUTH = str(config.get("SOCKET_AUTH", ""))
    guild_obj = discord.Object(id=GUILD_ID) if GUILD_ID else None


guild_obj = None
reload_bot_config()


def query_monitor(command: str = "GET_STATE") -> dict | str | None:
    """Send one authenticated local command and return the bounded response."""
    wire_command = f"{SOCKET_AUTH}:{command}" if SOCKET_AUTH else command
    try:
        with socket.create_connection(
            (SOCKET_HOST, SOCKET_PORT), timeout=SOCKET_TIMEOUT
        ) as connection:
            connection.settimeout(SOCKET_TIMEOUT)
            connection.sendall(wire_command.encode("utf-8"))
            chunks = []
            received = 0
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_MONITOR_RESPONSE:
                    raise ValueError("Monitor response exceeded the size limit")
                chunks.append(chunk)
        response = b"".join(chunks).decode("utf-8")
        if response.startswith("ERR:"):
            return response
        return json.loads(response) if command == "GET_STATE" else response
    except (ConnectionRefusedError, TimeoutError, OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None


async def query_monitor_async(command: str = "GET_STATE") -> dict | str | None:
    """Keep blocking socket I/O out of Discord's event loop."""
    return await asyncio.to_thread(query_monitor, command)


def fmt_time(value: str | None) -> str:
    if not value:
        return "Never"
    try:
        timestamp = datetime.fromisoformat(value).timestamp()
        return f"<t:{int(timestamp)}:R>"
    except (TypeError, ValueError):
        return value


def uptime_str(value: str | None) -> str:
    if not value:
        return "Unknown"
    try:
        started = datetime.fromisoformat(value)
        now = datetime.now(tz=started.tzinfo) if started.tzinfo else datetime.now()
        seconds = max(0, int((now - started).total_seconds()))
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        prefix = f"{days}d " if days else ""
        return f"{prefix}{hours}h {minutes}m {seconds}s"
    except (TypeError, ValueError, OverflowError):
        return "Unknown"


def latest_incident(state: dict) -> str | None:
    values = [
        state.get("last_crash"),
        state.get("last_freeze"),
        state.get("last_disconnect"),
    ]
    present = [value for value in values if value]
    return max(present) if present else None


intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
_bot_loop: asyncio.AbstractEventLoop | None = None
_commands_synced = False
_events_initialized = False
_last_events = {"crash": None, "freeze": None, "disconnect": None}


@client.event
async def on_ready():
    global _bot_loop, _commands_synced
    _bot_loop = asyncio.get_running_loop()
    reload_bot_config()
    if not _commands_synced:
        if guild_obj:
            tree.copy_global_to(guild=guild_obj)
            await tree.sync(guild=guild_obj)
        else:
            await tree.sync()
        _commands_synced = True
    print(f"Bot online as {client.user}")
    if not check_updates.is_running():
        check_updates.start()


async def _notification_channel():
    if not CHANNEL_ID:
        return None
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        return channel
    try:
        return await client.fetch_channel(CHANNEL_ID)
    except discord.DiscordException:
        return None


@tasks.loop(seconds=5)
async def check_updates():
    global _events_initialized
    if not CHANNEL_ID:
        return

    monitor_state = await query_monitor_async()
    if not isinstance(monitor_state, dict):
        return

    event_values = {
        "crash": monitor_state.get("last_crash"),
        "freeze": monitor_state.get("last_freeze"),
        "disconnect": monitor_state.get("last_disconnect"),
    }
    if not _events_initialized:
        _last_events.update(event_values)
        _events_initialized = True
        return

    changed = [
        event_name
        for event_name, event_time in event_values.items()
        if event_time and event_time != _last_events[event_name]
    ]
    _last_events.update(event_values)
    if not changed:
        return

    channel = await _notification_channel()
    if not channel:
        return

    definitions = {
        "crash": ("Roblox Crashed", "crash_count", "Total Crashes", COLOR_ERR),
        "freeze": ("Roblox Froze", "freeze_count", "Total Freezes", COLOR_WARN),
        "disconnect": (
            "Roblox Disconnected",
            "disconnect_count",
            "Total Disconnects",
            COLOR_WARN,
        ),
    }
    for event_name in changed:
        title, count_key, count_label, color = definitions[event_name]
        embed = discord.Embed(
            title=title,
            description=(
                f"**{monitor_state.get('game_name', 'Unknown')}** encountered a {event_name}. "
                "Automatic recovery is in progress."
            ),
            color=color,
        )
        embed.add_field(name=count_label, value=str(monitor_state.get(count_key, 0)))
        embed.add_field(name="Time", value=fmt_time(event_values[event_name]))
        if event_name == "disconnect" and monitor_state.get("last_disconnect_reason"):
            embed.add_field(
                name="Detected marker",
                value=f"`{monitor_state['last_disconnect_reason']}`",
                inline=False,
            )
        try:
            await channel.send(embed=embed)
        except discord.DiscordException as exc:
            print(f"Failed to send {event_name} notification: {exc}")


@check_updates.before_loop
async def before_check_updates():
    await client.wait_until_ready()


async def _get_state_or_reply(interaction: discord.Interaction) -> dict | None:
    monitor_state = await query_monitor_async()
    if not isinstance(monitor_state, dict):
        await _send_interaction(
            interaction,
            "Monitor is offline or returned an invalid response.",
            ephemeral=True,
        )
        return None
    return monitor_state


async def _send_interaction(interaction: discord.Interaction, *args, **kwargs):
    if interaction.response.is_done():
        return await interaction.followup.send(*args, **kwargs)
    return await interaction.response.send_message(*args, **kwargs)


@tree.command(name="status", description="Show the full monitor status")
@app_commands.describe(screenshot="Include a screenshot of the Roblox window")
async def cmd_status(interaction: discord.Interaction, screenshot: bool = False):
    if screenshot:
        await interaction.response.defer()
    monitor_state = await _get_state_or_reply(interaction)
    if monitor_state is None:
        return

    running = bool(monitor_state.get("roblox_running"))
    embed = discord.Embed(
        title="Monitor Status",
        color=COLOR_OK if running else COLOR_WARN,
    )
    embed.add_field(name="Roblox", value="Running" if running else "Not Running", inline=True)
    embed.add_field(
        name="Monitor",
        value=monitor_state.get("status_message", "Unknown"),
        inline=True,
    )
    embed.add_field(name="Crashes", value=str(monitor_state.get("crash_count", 0)), inline=True)
    embed.add_field(name="Freezes", value=str(monitor_state.get("freeze_count", 0)), inline=True)
    embed.add_field(
        name="Disconnects",
        value=str(monitor_state.get("disconnect_count", 0)),
        inline=True,
    )
    embed.add_field(name="Last Incident", value=fmt_time(latest_incident(monitor_state)), inline=True)
    embed.add_field(name="Last Rejoin", value=fmt_time(monitor_state.get("last_rejoin")), inline=True)
    embed.add_field(
        name="Monitor Uptime",
        value=uptime_str(monitor_state.get("monitor_start")),
        inline=True,
    )

    if screenshot:
        screenshot_path = await query_monitor_async("GET_SCREENSHOT")
        if isinstance(screenshot_path, str) and not screenshot_path.startswith("ERR:"):
            if os.path.isfile(screenshot_path):
                screenshot_file = discord.File(screenshot_path, filename="roblox_status.png")
                embed.set_image(url="attachment://roblox_status.png")
                await _send_interaction(interaction, file=screenshot_file, embed=embed)
                return
        embed.add_field(
            name="Screenshot",
            value=screenshot_path or "Screenshot unavailable",
            inline=False,
        )
    await _send_interaction(interaction, embed=embed)


@tree.command(name="current_game", description="Show the game being monitored")
async def cmd_current_game(interaction: discord.Interaction):
    monitor_state = await _get_state_or_reply(interaction)
    if monitor_state is None:
        return
    embed = discord.Embed(title="Current Game", color=COLOR_INFO)
    embed.add_field(name="Name", value=monitor_state.get("game_name", "Unknown"), inline=False)
    embed.add_field(name="Place ID", value=f"`{monitor_state.get('place_id', 'Unknown')}`")
    embed.add_field(name="Link", value=f"[Open in Roblox]({monitor_state.get('game_url', '')})")
    await interaction.response.send_message(embed=embed)


@tree.command(name="placeid", description="Get the monitored Place ID")
async def cmd_placeid(interaction: discord.Interaction):
    monitor_state = await _get_state_or_reply(interaction)
    if monitor_state is None:
        return
    await interaction.response.send_message(
        f"Place ID: `{monitor_state.get('place_id', 'Unknown')}` - "
        f"**{monitor_state.get('game_name', 'Unknown')}**"
    )


@tree.command(name="crashes", description="Show crash, freeze, and disconnect history")
async def cmd_crashes(interaction: discord.Interaction):
    monitor_state = await _get_state_or_reply(interaction)
    if monitor_state is None:
        return
    has_incidents = any(
        monitor_state.get(key, 0) > 0
        for key in ("crash_count", "freeze_count", "disconnect_count")
    )
    embed = discord.Embed(
        title="Incident Report",
        color=COLOR_WARN if has_incidents else COLOR_OK,
    )
    for label, singular, count_key, time_key in (
        ("Crashes", "Crash", "crash_count", "last_crash"),
        ("Freezes", "Freeze", "freeze_count", "last_freeze"),
        ("Disconnects", "Disconnect", "disconnect_count", "last_disconnect"),
    ):
        embed.add_field(name=f"Total {label}", value=str(monitor_state.get(count_key, 0)))
        embed.add_field(name=f"Last {singular}", value=fmt_time(monitor_state.get(time_key)))
    embed.add_field(name="Last Rejoin", value=fmt_time(monitor_state.get("last_rejoin")), inline=False)
    await interaction.response.send_message(embed=embed)


@tree.command(name="uptime", description="Show how long the monitor has been running")
async def cmd_uptime(interaction: discord.Interaction):
    monitor_state = await _get_state_or_reply(interaction)
    if monitor_state is None:
        return
    started = monitor_state.get("monitor_start")
    embed = discord.Embed(title="Uptime", color=COLOR_INFO)
    embed.add_field(name="Monitor Started", value=fmt_time(started), inline=False)
    embed.add_field(name="Running For", value=uptime_str(started), inline=False)
    await interaction.response.send_message(embed=embed)


async def _send_control(interaction: discord.Interaction, command: str, success: str):
    result = await query_monitor_async(command)
    if not isinstance(result, str):
        await interaction.response.send_message("Monitor is offline.", ephemeral=True)
    elif result.startswith("ERR:"):
        await interaction.response.send_message(result[4:].strip(), ephemeral=True)
    else:
        await interaction.response.send_message(success)


@tree.command(name="pause", description="Pause automatic monitoring")
async def cmd_pause(interaction: discord.Interaction):
    await _send_control(interaction, "PAUSE", "Monitor paused. Use `/resume` to continue.")


@tree.command(name="resume", description="Resume automatic monitoring")
async def cmd_resume(interaction: discord.Interaction):
    await _send_control(interaction, "RESUME", "Monitor resumed.")


@tree.command(name="rejoin", description="Force a clean rejoin now")
async def cmd_rejoin(interaction: discord.Interaction):
    await _send_control(interaction, "REJOIN_NOW", "Rejoin started.")


@tree.command(name="current_screen", description="Capture the Roblox window")
async def cmd_current_screen(interaction: discord.Interaction):
    await interaction.response.defer()
    screenshot_path = await query_monitor_async("GET_SCREENSHOT")
    if not isinstance(screenshot_path, str):
        await interaction.followup.send("Monitor is offline.", ephemeral=True)
    elif screenshot_path.startswith("ERR:"):
        await interaction.followup.send(screenshot_path[4:].strip(), ephemeral=True)
    elif not os.path.isfile(screenshot_path):
        await interaction.followup.send("Screenshot file was not found.", ephemeral=True)
    else:
        await interaction.followup.send(
            content="Current Roblox view:",
            file=discord.File(screenshot_path, filename="roblox_screen.png"),
        )


def stop_bot_sync(timeout: float = 10.0) -> bool:
    if _bot_loop is None or not _bot_loop.is_running():
        return False
    future = asyncio.run_coroutine_threadsafe(client.close(), _bot_loop)
    try:
        future.result(timeout=timeout)
        return True
    except Exception as exc:
        print(f"Could not stop Discord bot cleanly: {exc}")
        return False


def main() -> None:
    reload_bot_config()
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise SystemExit("Set BOT_TOKEN in config.json before starting the Discord bot.")
    client.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
