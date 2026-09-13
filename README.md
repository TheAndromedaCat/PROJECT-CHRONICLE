# Antigravity User-Installable Discord App & Lore Archivist (AskViolet)

A powerful user-installable Discord App powered by Google Gemini and SQLite that indexes, searches, and synthesizes roleplay archives, character backstories, and multi-universe timelines across servers and DMs.

---

## 🚀 Setup & Installation

### 1. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 2. Install DiscordChatExporter CLI (DCE)
Download the [DiscordChatExporter.Cli release](https://github.com/Tyrrrz/DiscordChatExporter/releases) for your operating system and place the extracted binaries into the `dce/` directory:

* **Windows**:
  Extract into `dce/win64/` (so that `dce/win64/DiscordChatExporter.Cli.exe` exists) or directly into `dce/DiscordChatExporter.Cli.exe`.
* **Linux**:
  Extract into `dce/linux-amd64/` (so that `dce/linux-amd64/DiscordChatExporter.Cli` exists) and ensure execution permissions (`chmod +x dce/linux-amd64/DiscordChatExporter.Cli`).

*(Note: The `dce/` folder itself is tracked via `.gitkeep`, but binaries and dependencies are gitignored).*

### 3. Configure Credentials

Create a `.env` file in the project root directory:

```env
# Discord Bot Token (from Discord Developer Portal -> Bot tab)
DISCORD_BOT_TOKEN=your_discord_bot_token_here

# Discord User Token (enables full DM search and background exports via DCE)
DISCORD_USER_TOKEN=your_discord_user_token_here

# Google Gemini API Key (from Google AI Studio)
GEMINI_API_KEY=your_gemini_api_key_here

# Optional: Preferred default Gemini model
GEMINI_MODEL=gemini-3.6-flash
```

> **Token Distinctions:**
> - **`DISCORD_BOT_TOKEN`**: Found in [Discord Developer Portal](https://discord.com/developers/applications) $\rightarrow$ **Bot** $\rightarrow$ Click **Reset Token** (or Copy Token).
> - **`DISCORD_USER_TOKEN`**: Your personal Discord account authorization token (used strictly by the local `DiscordChatExporter.Cli` subprocess to fetch full private DM history).
> - **`GEMINI_API_KEY`**: Obtain from [Google AI Studio](https://aistudio.google.com/).

---

## ⚙️ Discord Developer Portal Configuration Checklist

1. **Privileged Gateway Intents**:
   - Go to Discord Developer Portal $\rightarrow$ **Bot** tab.
   - Under **Privileged Gateway Intents**, enable **Message Content Intent**, **Server Members Intent**, and **Presence Intent**.
   - Click **Save Changes**.

2. **Global User-Install Config**:
   - Go to **Installation** tab.
   - Under **Installation Contexts**, check **User Install** (allows running directly from your profile anywhere).
   - Under **Integration Types User Install Config**, make sure `applications.commands` is checked in Scopes.
   - Under **Default Install Settings**, select `Guilds`, `Bot DMs`, and `Private Channels`.
   - Click **Save Changes**.

3. **Install to Profile**:
   - Copy the **Discord Provided Link** at the top of the Installation page and open it in your browser to add the app to your personal Discord account.

---

## 🏃 Run the Bot & Management Tools

### Start the Discord Bot:
```bash
python bot.py
```

### Launch Interactive Universe Web Manager:
```bash
python universe_manager.py
```
*(Opens a local visual dashboard at `http://localhost:5050` to search, filter, and reassign channels/servers to universes).*

