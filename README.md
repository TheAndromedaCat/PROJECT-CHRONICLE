# PROJECT CHRONICLE
## User-Installable Discord App & Lore Archivist (AskViolet)

A powerful user-installable Discord App powered by Google Gemini and SQLite that indexes, searches, and synthesizes roleplay archives, character backstories, and multi-universe timelines across servers and DMs.

---

> [!CAUTION]
> ### ⚠️ CRITICAL WARNING: RISKS OF USING DISCORD CHAT EXPORTER (DCE) WITH USER TOKENS
>
> Running **DiscordChatExporter (DCE)** using a personal **Discord User Token** (`DISCORD_USER_TOKEN`) carries an **EXTREMELY HIGH RISK** of triggering Discord's automated moderation and anti-scraping systems.
> 
> * **Platform Manipulation Violations:** Discord's automated moderation strictly monitors rapid API calls from third-party tools on user tokens. Automated scraping or rapid channel indexing can trigger multiple instant strikes for *"Platform Manipulation"* (self-botting violations).
> * **Account Suspension / Bans:** Violations can place your Discord account at immediate risk, resulting in temporary suspensions or permanent account bans.
> * **Recommended Safe Mode:** Use **`bot_dcedisabled.py`**, which completely disables DCE and user-token calls. It runs 100% safely on official Discord Bot Gateway credentials and queries your pre-existing local SQLite archive (`violet_archive.db`) without making any external scraping requests.

---

## 📁 Bot Versions in this Repository

| Script | Mode | Risk Level | Description |
| :--- | :--- | :--- | :--- |
| **`bot_dcedisabled.py`** | **Safe Archive Mode (Recommended)** | 🟢 **Zero Risk** | Runs strictly using official bot token (`DISCORD_BOT_TOKEN`). All DM/channel searches, lore syntheses, and character lookups query your local SQLite database (`violet_archive.db`) and Google Gemini. **No DCE or user tokens are ever invoked.** |
| **`bot.py`** | **Live DCE Mode (Legacy)** | 🔴 **High Risk** | Attempts automated live scraping of user DMs via `DiscordChatExporter.Cli`. **Use at your own risk.** Automated scraping with user tokens violates Discord ToS and will trigger anti-abuse actions. |

---

## 🚀 Setup & Installation

### 1. Install Python Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Credentials

Create a `.env` file in the project root directory:

```env
# Discord Bot Token (from Discord Developer Portal -> Bot tab)
DISCORD_BOT_TOKEN=your_discord_bot_token_here

# Google Gemini API Key (from Google AI Studio: https://aistudio.google.com/)
GEMINI_API_KEY=your_gemini_api_key_here

# Gemini Model (Recommended: gemini-3.5-flash-lite for 500 requests/day quota)
GEMINI_MODEL=gemini-3.5-flash-lite
```

> **Credential Details:**
> - **`DISCORD_BOT_TOKEN`**: Found in [Discord Developer Portal](https://discord.com/developers/applications) $\rightarrow$ **Bot** $\rightarrow$ Click **Reset Token** (or Copy Token).
> - **`GEMINI_API_KEY`**: Obtain for free from [Google AI Studio](https://aistudio.google.com/).

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

### Start Safe Mode (Recommended):
```bash
python bot_dcedisabled.py
```

### Launch Interactive Universe Web Manager:
```bash
python universe_manager.py
```
*(Opens a local visual dashboard at `http://localhost:5050` to search, filter, and assign channels/servers to universes).*

### Launch Lore Archivist & Manager (No DCE required):
```bash
python lore_manager.py
```
*(Opens an interactive CLI to flush temporary DM conversations to Markdown files, enter new lore directly, or sync any `.md` notes from the `lore/` folder into the database).*

* **Command-line shortcuts:**
  * `python lore_manager.py --flush`: Flushes today's DM discussions directly into `lore/<Universe>/` dossiers and SQLite.
  * `python lore_manager.py --sync`: Reads all markdown notes in `lore/` and syncs them into SQLite.

### Discord Slash Commands for Lore Management & Local LLM:
* **`/addlore topic: [title] content: [details] universe: [Universe]`**: Record new lore directly from Discord without DCE.
* **`/flushlore universe: [Universe]`**: Flush recent DM discussions into permanent Markdown files and SQLite.
* **`/setlocal enabled: True/False [text_model] [vision_model] [endpoint]`**: Toggle 100% offline local inference via Ollama (or Llama.cpp) vs Google Gemini Cloud. Automatically falls back to cloud if local server is unreachable.






## 🖥️ 3-Tier Local AI Hardware System (RTX 3060 12GB + DDR5)

AskViolet now supports intelligent 3-tier local hardware routing via Ollama (http://127.0.0.1:11435):

1. **⚡ Fast Chat & Roleplay Tier (7B)**:
   - Model: hf.co/bartowski/Qwen2.5-Coder-7B-Instruct-abliterated-GGUF:Q8_0
   - VRAM: ~8.1 GB (100% in VRAM)
   - Performance: ~85–100 tokens/sec instant generation for casual DM chatter, roleplaying, and quick lore queries.

2. **🧠 Deep Lore & Dossier Tier (27B)**:
   - Model: hf.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF:Q4_K_M
   - VRAM + DDR5: ~16.8 GB (Partial VRAM offload + DDR5 6400 MT/s)
   - Performance: ~14–18 tokens/sec deep analytical reasoning for multi-universe lore searches and contradiction audits. Trigger via /ask mode: deep.

3. **👁️ Multimodal Vision Tier (7B-VL)**:
   - Model: hf.co/mradermacher/Qwen2.5-VL-7B-Abliterated-Caption-it-GGUF:Q8_0
   - VRAM: ~8.1 GB (100% in VRAM)
   - Performance: Triggered automatically whenever an image or photo is attached to /ask or DMs.
