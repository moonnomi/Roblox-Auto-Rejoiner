import discord
from discord import app_commands
from discord.ext import tasks
import socket
import json
import os
from datetime import datetime, timezone

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE" # Get this from the Discord Developer Portal when you create your bot
GUILD_ID = None          # Set to your server's integer ID for instant slash cmd updates,
                         # or leave None for global (takes up to 1 hour to propagate)
CHANNEL_ID = None        # Set to the channel ID where crash announcements should be sent (integer)
SOCKET_HOST = "127.0.0.1"
SOCKET_PORT = 45678
# ──────────────────────────────────────────────────────────────────────────────

# ─── COLORS ───────────────────────────────────────────────────────────────────
COLOR_OK      = 0x57F287   # green
COLOR_WARN    = 0xFEE75C   # yellow
COLOR_ERR     = 0xED4245   # red
COLOR_INFO    = 0x5865F2   # blurple
# ──────────────────────────────────────────────────────────────────────────────


def query_monitor(command: str = "GET_STATE") -> dict | str | None:
    """Send a command to the monitor socket and return the response."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((SOCKET_HOST, SOCKET_PORT))
            s.sendall(command.encode("utf-8"))
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            response = data.decode("utf-8")
            if command == "GET_STATE":
                return json.loads(response)
            return response
    except ConnectionRefusedError:
        return None
    except Exception as e:
        return None


def fmt_time(iso: str | None) -> str:
    if not iso:
        return "Never"
    try:
        dt = datetime.fromisoformat(iso)
        return f"<t:{int(dt.timestamp())}:R>"   # Discord relative timestamp
    except Exception:
        return iso


def uptime_str(iso: str | None) -> str:
    if not iso:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(iso)
        delta = datetime.now() - dt
        hours, rem = divmod(int(delta.total_seconds()), 3600)
        mins, secs = divmod(rem, 60)
        return f"{hours}h {mins}m {secs}s"
    except Exception:
        return "Unknown"


# ─── BOT SETUP ────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

guild_obj = discord.Object(id=GUILD_ID) if GUILD_ID else None


@client.event
async def on_ready():
    if guild_obj:
        tree.copy_global_to(guild=guild_obj)
        await tree.sync(guild=guild_obj)
    else:
        await tree.sync()
    print(f"Bot online as {client.user}")
    
    if not check_crashes.is_running():
        check_crashes.start()


last_known_crash_time = None

@tasks.loop(seconds=5)
async def check_crashes():
    global last_known_crash_time
    if not CHANNEL_ID:
        return
        
    state = query_monitor("GET_STATE")
    if not isinstance(state, dict):
        return
        
    current_crash_time = state.get("last_crash")
    if current_crash_time and current_crash_time != last_known_crash_time:
        if last_known_crash_time is not None:
            # A new crash occurred!
            channel = client.get_channel(CHANNEL_ID)
            embed = discord.Embed(
                title="💥 Roblox Crashed!",
                description=f"Crash detected for **{state.get('game_name', 'Unknown')}** (`{state.get('place_id', 'Unknown')}`).\nRejoining in progress...",
                color=COLOR_ERR
            )
            embed.add_field(name="Total Crashes", value=str(state.get("crash_count", 0)))
            embed.add_field(name="Time", value=fmt_time(current_crash_time))
            
            if channel:
                try:
                    await channel.send(embed=embed)
                except Exception as e:
                    print(f"Failed to send crash announcement: {e}")
            else:
                try:
                    channel = await client.fetch_channel(CHANNEL_ID)
                    if channel:
                        await channel.send(embed=embed)
                except Exception as e:
                    print(f"Could not fetch or send to channel ID {CHANNEL_ID}: {e}")
        last_known_crash_time = current_crash_time

@check_crashes.before_loop
async def before_check_crashes():
    await client.wait_until_ready()


# ─── SLASH COMMANDS ───────────────────────────────────────────────────────────

@tree.command(name="status", description="Show the full monitor status")
async def cmd_status(interaction: discord.Interaction):
    state = query_monitor("GET_STATE")

    if state is None:
        embed = discord.Embed(
            title="❌ Monitor Offline",
            description="Cannot reach the monitor script. Make sure `roblox_monitor.py` is running.",
            color=COLOR_ERR
        )
        await interaction.response.send_message(embed=embed)
        return

    roblox_icon = "🟢" if state["roblox_running"] else "🔴"
    monitor_icon = "✅" if state["monitoring"] else "⏸️"

    embed = discord.Embed(title="📊 Monitor Status", color=COLOR_OK if state["roblox_running"] else COLOR_WARN)
    embed.add_field(name="Roblox", value=f"{roblox_icon} {'Running' if state['roblox_running'] else 'Not Running'}", inline=True)
    embed.add_field(name="Monitor", value=f"{monitor_icon} {state['status_message']}", inline=True)
    embed.add_field(name="Crashes", value=f"💥 {state['crash_count']}", inline=True)
    embed.add_field(name="Last Crash", value=fmt_time(state["last_crash"]), inline=True)
    embed.add_field(name="Last Rejoin", value=fmt_time(state["last_rejoin"]), inline=True)
    embed.add_field(name="Monitor Uptime", value=uptime_str(state["monitor_start"]), inline=True)
    embed.set_footer(text="Roblox Auto-Rejoin Monitor")
    await interaction.response.send_message(embed=embed)


@tree.command(name="current_game", description="Show the game being monitored")
async def cmd_current_game(interaction: discord.Interaction):
    state = query_monitor("GET_STATE")

    if state is None:
        await interaction.response.send_message("❌ Monitor is offline!", ephemeral=True)
        return

    embed = discord.Embed(title="🎮 Current Game", color=COLOR_INFO)
    embed.add_field(name="Name", value=state["game_name"], inline=False)
    embed.add_field(name="Place ID", value=f"`{state['place_id']}`", inline=True)
    embed.add_field(name="Link", value=f"[Open in Roblox]({state['game_url']})", inline=True)
    await interaction.response.send_message(embed=embed)


@tree.command(name="placeid", description="Get the Place ID of the monitored game")
async def cmd_placeid(interaction: discord.Interaction):
    state = query_monitor("GET_STATE")

    if state is None:
        await interaction.response.send_message("❌ Monitor is offline!", ephemeral=True)
        return

    await interaction.response.send_message(
        f"🎯 Place ID: `{state['place_id']}` — **{state['game_name']}**"
    )


@tree.command(name="crashes", description="Show the crash count and history")
async def cmd_crashes(interaction: discord.Interaction):
    state = query_monitor("GET_STATE")

    if state is None:
        await interaction.response.send_message("❌ Monitor is offline!", ephemeral=True)
        return

    embed = discord.Embed(title="💥 Crash Report", color=COLOR_WARN if state["crash_count"] > 0 else COLOR_OK)
    embed.add_field(name="Total Crashes", value=str(state["crash_count"]), inline=True)
    embed.add_field(name="Last Crash", value=fmt_time(state["last_crash"]), inline=True)
    embed.add_field(name="Last Rejoin", value=fmt_time(state["last_rejoin"]), inline=True)
    await interaction.response.send_message(embed=embed)


@tree.command(name="uptime", description="Show how long the monitor has been running")
async def cmd_uptime(interaction: discord.Interaction):
    state = query_monitor("GET_STATE")

    if state is None:
        await interaction.response.send_message("❌ Monitor is offline!", ephemeral=True)
        return

    embed = discord.Embed(title="⏱️ Uptime", color=COLOR_INFO)
    embed.add_field(name="Monitor Started", value=fmt_time(state["monitor_start"]), inline=False)
    embed.add_field(name="Running For", value=uptime_str(state["monitor_start"]), inline=False)
    await interaction.response.send_message(embed=embed)


@tree.command(name="pause", description="Pause the auto-rejoin monitor")
async def cmd_pause(interaction: discord.Interaction):
    result = query_monitor("PAUSE")
    if result is None:
        await interaction.response.send_message("❌ Monitor is offline!", ephemeral=True)
    else:
        await interaction.response.send_message("⏸️ Monitor paused. Use `/resume` to restart it.")


@tree.command(name="resume", description="Resume the auto-rejoin monitor")
async def cmd_resume(interaction: discord.Interaction):
    result = query_monitor("RESUME")
    if result is None:
        await interaction.response.send_message("❌ Monitor is offline!", ephemeral=True)
    else:
        await interaction.response.send_message("▶️ Monitor resumed! Watching for crashes.")


@tree.command(name="rejoin", description="Manually force a rejoin right now")
async def cmd_rejoin(interaction: discord.Interaction):
    result = query_monitor("REJOIN_NOW")
    if result is None:
        await interaction.response.send_message("❌ Monitor is offline!", ephemeral=True)
    else:
        await interaction.response.send_message("🔄 Rejoin command sent! Roblox should open shortly.")


# ─── RUN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("ERROR: Please set your BOT_TOKEN in discord_bot.py before running!")
        exit(1)
    client.run(BOT_TOKEN)
