# Roblox Auto-Rejoin Monitor + Discord Bot

## 📖 What it is and What it Does
This toolset allows you to AFK farm or play Roblox without worrying about crashes or sudden disconnections. It runs entirely on your local Windows PC and continuously monitors your active Roblox session. 

If Roblox crashes, closes unexpectedly, or disconnects, the **Monitor** will detect this and automatically launch Roblox again, ensuring you instantly rejoin the exact same Place / server you were previously in.

To give you remote control and updates, it comes with a **Discord Bot**. 
- You can check your current AFK status right from Discord on your phone or PC.
- See how many times Roblox has crashed and your total uptime.
- Instantly Force a rejoin or Pause the auto-rejoin system remotely.

---

## 📁 Files Explained
- `roblox_monitor.py` — The core system. It runs in the background, watches the Roblox game client, stores the current Place ID/Job ID, and triggers the auto-rejoin when a crash is detected.
- `discord_bot.py` — The remote-control interface. Connects to your own Discord bot and provides interactive slash commands.
- `requirements.txt` — A simple list of the Python packages needed to run these scripts.

---

## 🛠️ Setup Guide for Windows

### 🐍 Step 1 — Install Python
1. Download from [python.org](https://python.org) (Version 3.10 or newer). 
2. **Important:** When running the installer, make sure to **Check "Add Python to PATH"** at the bottom before clicking Install.

### 📦 Step 2 — Install Dependencies
1. Open the folder containing the downloaded code.
2. Click on the address bar in File Explorer at the top, type `cmd`, and press **Enter**. This opens Command Prompt in that folder.
3. Run the following command to download the required libraries:
```cmd
pip install psutil discord.py requests
```

### 🤖 Step 3 — Create Your Discord Bot
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application**, give it a name like "Roblox Monitor", and agree to the terms.
3. Go to the **Bot** tab in the left sidebar.
4. Click **Reset Token** → **copy the token** and save it somewhere safe (Keep it secret!).
5. Scroll down to "Privileged Gateway Intents" and enable **Message Content Intent** (just in case).
6. Go to **OAuth2 → URL Generator** in the sidebar.
7. Under **Scopes**, check `bot` and `applications.commands`.
8. Under **Bot Permissions**, check `Send Messages` and `Use Slash Commands`.
9. Copy the generated URL at the bottom, paste it into your browser, and invite the bot to your personal Discord server.

### ⚙️ Step 4 — Configure the Bot Files
1. Right-click `discord_bot.py` and select **Edit with Notepad** (or any text editor).
2. Find the line at the top:
```python
BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"
```
3. Replace `"PASTE_YOUR_BOT_TOKEN_HERE"` with the actual token you copied in Step 3.
4. *(Optional)* Find `GUILD_ID` in the same file and set it to your Discord server's ID. This makes slash commands update instantly rather than waiting up to an hour. (To get the ID: Enable Developer Mode in Discord Settings > Advanced, then right-click your server icon and click "Copy Server ID").

### 🚀 Step 5 — Run Everything
You will need two Command Prompt windows running simultaneously in the folder where your scripts are.

**Window 1 — The Monitor:**
```cmd
python roblox_monitor.py
```

**Window 2 — The Discord Bot:**
```cmd
python discord_bot.py
```

Make sure to leave both windows open! As long as they are running, your session is monitored and you can use Discord commands. Finally, simply join a Roblox game normally, and the monitor will pick it up automatically!

---

## 💬 Discord Commands Overview

Once the bot is online in your server, you can type `/` to see its commands:

| Command | Description |
|---|---|
| `/status` | Full monitor status overview (Running, paused, last crash) |
| `/current_game` | Details on your current game, Place ID, and a direct link |
| `/placeid` | Returns just the current Place ID |
| `/crashes` | Shows total crash count and the exact time of the last crash |
| `/uptime` | Displays how long the monitor has been actively running |
| `/pause` | Pauses auto-rejoin. Helpful if you manually want to leave the game without being thrown back in! |
| `/resume` | Resumes auto-rejoin monitoring |
| `/rejoin` | Forces a manual rejoin immediately |

---

## 💡 Pro Tips & Troubleshooting
- **Keep both CMD windows open** while you play or AFK. If you close them, it stops working.
- If Roblox crashes, the monitor waits roughly 5 seconds (to ensure the process is fully closed) before auto-rejoining. 
- You can change the wait time by editing `REJOIN_DELAY` inside `roblox_monitor.py`.
- If slash commands don't show up in Discord right away, either wait a few minutes, restart the bot, or make sure you've set the `GUILD_ID` properly in the code.
