import os
import sys
import io
import json
import re
import sqlite3
import asyncio
import datetime
from pathlib import Path
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Any, Union

# Configure UTF-8 encoding and line buffering for standard output
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# 1. Initialize API Clients and Environment Variables
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DISCORD_USER_TOKEN = os.environ.get("DISCORD_USER_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
BASE_DIR = Path(__file__).resolve().parent

def _ensure_executable(p: Path):
    if os.name != "nt" and p.exists():
        try:
            st = os.stat(p)
            os.chmod(p, st.st_mode | 0o111)
        except Exception:
            pass

def get_dce_path() -> str:
    import shutil
    env_path = os.environ.get("DCE_PATH")
    if env_path:
        candidate = Path(env_path)
        if not candidate.is_absolute():
            candidate = BASE_DIR / candidate
        if candidate.exists() and candidate.is_file():
            _ensure_executable(candidate)
            return str(candidate.resolve())

    # OS-specific candidates relative to the project directory
    candidates = []
    if sys.platform == "win32" or os.name == "nt":
        candidates = [
            BASE_DIR / "dce" / "win64" / "DiscordChatExporter.Cli.exe",
            BASE_DIR / "dce" / "DiscordChatExporter.Cli.exe",
            BASE_DIR / "DiscordChatExporter.Cli.exe",
        ]
    else:
        candidates = [
            BASE_DIR / "dce" / "linux-amd64" / "DiscordChatExporter.Cli",
            BASE_DIR / "dce" / "DiscordChatExporter.Cli",
            BASE_DIR / "DiscordChatExporter.Cli",
        ]

    for c in candidates:
        if c.exists() and c.is_file():
            _ensure_executable(c)
            return str(c.resolve())

    # Recursive check in project's dce folder
    dce_dir = BASE_DIR / "dce"
    if dce_dir.exists():
        for p in dce_dir.rglob("DiscordChatExporter.Cli*"):
            if p.is_file() and not p.name.endswith((".json", ".pdb", ".dll", ".config")):
                _ensure_executable(p)
                return str(p.resolve())

    # System PATH lookup
    cli_name = "DiscordChatExporter.Cli.exe" if (sys.platform == "win32" or os.name == "nt") else "DiscordChatExporter.Cli"
    which_path = shutil.which(cli_name)
    if which_path:
        return which_path

    # Fallback to local project path
    if sys.platform == "win32" or os.name == "nt":
        fallback = BASE_DIR / "dce" / "win64" / "DiscordChatExporter.Cli.exe"
    else:
        fallback = BASE_DIR / "dce" / "linux-amd64" / "DiscordChatExporter.Cli"
    return str(fallback.resolve())

DCE_PATH = get_dce_path()


def get_guild_info_via_dce(guild_id: Union[str, int]) -> Tuple[str, List[Tuple[str, str]]]:
    """
    Fetches the guild name and all channels (channel_id, channel_name) in the guild using DCE and user token.
    Essential for User-Installed app contexts where Discord.py partial guilds have empty channel lists.
    """
    token = DISCORD_USER_TOKEN or DISCORD_TOKEN
    guild_name = ""
    channels: List[Tuple[str, str]] = []
    
    if not token or not os.path.exists(DCE_PATH) or not guild_id:
        return guild_name, channels

    import subprocess
    gid_str = str(guild_id)

    # 1. Fetch guild name
    try:
        p_guilds = subprocess.run(
            [DCE_PATH, "guilds", "-t", token],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15.0
        )
        for line in p_guilds.stdout.splitlines():
            if "|" in line:
                parts = [x.strip() for x in line.split("|", 1)]
                if parts[0] == gid_str:
                    guild_name = parts[1]
                    break
    except Exception as e:
        print(f"⚠️ Error fetching guild name via DCE: {e}")

    # 2. Fetch guild channels
    try:
        p_channels = subprocess.run(
            [DCE_PATH, "channels", "--guild", gid_str, "-t", token],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25.0
        )
        for line in p_channels.stdout.splitlines():
            if "|" in line:
                parts = [x.strip() for x in line.split("|", 1)]
                cid = parts[0]
                cname = parts[1]
                if cid.isdigit():
                    channels.append((cid, cname))
    except Exception as e:
        print(f"⚠️ Error fetching guild channels via DCE: {e}")

    return guild_name, channels


if not DISCORD_TOKEN or not GEMINI_KEY:
    print("❌ Critical Error: Missing DISCORD_BOT_TOKEN or GEMINI_API_KEY environment variables.")
    print("💡 Please set them in a .env file or your system environment variables.")
    sys.exit(1)

ai_client = genai.Client(api_key=GEMINI_KEY)

# 2. Lightweight SQLite Database for Knowledge & Topic Caching
DB_PATH = Path("violet_archive.db")


def init_db():
    """Initializes a lightweight local SQLite database for messages, channels, topic cache, query history, user lore overrides, and universe groupings."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            channel_id TEXT,
            timestamp TEXT,
            author TEXT,
            content TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_channel ON messages(channel_id)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY,
            name TEXT,
            last_parsed DATETIME DEFAULT CURRENT_TIMESTAMP,
            total_messages INTEGER,
            first_timestamp TEXT,
            last_timestamp TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS topic_cache (
            channel_id TEXT,
            topic_key TEXT,
            summary TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (channel_id, topic_key)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS query_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT,
            target_name TEXT,
            query TEXT,
            response TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_query_channel ON query_history(channel_id)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lore_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT,
            target_name TEXT,
            topic_key TEXT,
            correction TEXT,
            universe_name TEXT COLLATE NOCASE DEFAULT '',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lore_channel ON lore_overrides(channel_id)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channel_universes (
            channel_id TEXT PRIMARY KEY,
            universe_name TEXT COLLATE NOCASE,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_channel_universe ON channel_universes(universe_name)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_active_universe (
            user_id TEXT PRIMARY KEY,
            active_universe TEXT COLLATE NOCASE,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Auto-migrate older schema versions
    try:
        cursor.execute("ALTER TABLE query_history ADD COLUMN response TEXT")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE lore_overrides ADD COLUMN topic_key TEXT")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE lore_overrides ADD COLUMN universe_name TEXT COLLATE NOCASE DEFAULT ''")
    except Exception:
        pass

    conn.commit()
    conn.close()


init_db()


def set_channel_universe(channel_id: str, universe_name: str):
    """Associates a channel/DM with a specific Universe."""
    if not channel_id or not universe_name:
        return
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO channel_universes (channel_id, universe_name, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (channel_id, universe_name.strip())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Could not set channel universe: {e}")


def set_multiple_channels_universe(channel_list: List[Tuple[str, str]], universe_name: str) -> int:
    """Bulk associates a list of (channel_id, channel_name) with a Universe."""
    if not channel_list or not universe_name:
        return 0
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        cursor = conn.cursor()
        rows = [(str(cid), universe_name.strip()) for cid, _ in channel_list]
        cursor.executemany(
            "INSERT OR REPLACE INTO channel_universes (channel_id, universe_name, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            rows
        )
        for cid, cname in channel_list:
            if cname:
                cursor.execute(
                    "INSERT OR IGNORE INTO channels (channel_id, name, total_messages) VALUES (?, ?, 0)",
                    (str(cid), cname)
                )
        conn.commit()
        conn.close()
        return len(rows)
    except Exception as e:
        print(f"⚠️ Could not set multiple channel universes: {e}")
        return 0


def remove_channel_universe(channel_id: str):
    """Removes a channel/DM from any assigned Universe."""
    if not channel_id:
        return
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM channel_universes WHERE channel_id = ?", (channel_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Could not remove channel universe: {e}")


def remove_multiple_channels_universe(channel_ids: List[str]) -> int:
    """Bulk removes a list of channel_ids from their assigned Universes."""
    if not channel_ids:
        return 0
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        cursor = conn.cursor()
        cursor.executemany("DELETE FROM channel_universes WHERE channel_id = ?", [(str(cid),) for cid in channel_ids])
        conn.commit()
        conn.close()
        return len(channel_ids)
    except Exception as e:
        print(f"⚠️ Could not remove multiple channel universes: {e}")
        return 0


def delete_universe(universe_name: str):
    """Deletes a universe mapping for all channels."""
    if not universe_name:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM channel_universes WHERE universe_name = ? COLLATE NOCASE", (universe_name.strip(),))
        cursor.execute("UPDATE user_active_universe SET active_universe = '' WHERE active_universe = ? COLLATE NOCASE", (universe_name.strip(),))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Could not delete universe: {e}")


def get_channel_universe(channel_id: str) -> Optional[str]:
    """Returns the universe name for a specific channel_id."""
    if not channel_id:
        return None
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT universe_name FROM channel_universes WHERE channel_id = ?", (channel_id,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def get_channels_for_universe(universe_name: str) -> List[str]:
    """Returns list of channel_ids assigned to a universe."""
    if not universe_name:
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT channel_id FROM channel_universes WHERE universe_name = ? COLLATE NOCASE", (universe_name.strip(),))
        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def get_universe_names() -> List[str]:
    """Returns distinct list of registered universe names across channel mappings and lore overrides."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT universe_name FROM channel_universes WHERE universe_name IS NOT NULL AND universe_name != ''
            UNION
            SELECT DISTINCT universe_name FROM lore_overrides WHERE universe_name IS NOT NULL AND universe_name != ''
            ORDER BY universe_name ASC
        """)
        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception as e:
        print(f"⚠️ Error getting universe names: {e}")
        return []


def get_all_universes_summary() -> Dict[str, Dict[str, Any]]:
    """Returns a comprehensive summary of all universes with channel counts, total messages, and channel list."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT cu.universe_name, cu.channel_id, COALESCE(c.name, cu.channel_id), COALESCE(c.total_messages, 0)
            FROM channel_universes cu
            LEFT JOIN channels c ON cu.channel_id = c.channel_id
            ORDER BY cu.universe_name ASC, COALESCE(c.total_messages, 0) DESC
        """)
        rows = cursor.fetchall()
        
        # Also find unassigned channels
        cursor.execute("""
            SELECT c.channel_id, c.name, c.total_messages
            FROM channels c
            WHERE c.channel_id NOT IN (SELECT channel_id FROM channel_universes)
            ORDER BY c.total_messages DESC
        """)
        unassigned_rows = cursor.fetchall()
        conn.close()

        summary: Dict[str, Dict[str, Any]] = {}
        for u_name, c_id, c_name, msgs in rows:
            if u_name not in summary:
                summary[u_name] = {"channels": [], "total_messages": 0}
            summary[u_name]["channels"].append({"channel_id": c_id, "name": c_name, "messages": msgs})
            summary[u_name]["total_messages"] += msgs

        if unassigned_rows:
            summary["Unassigned / Global"] = {
                "channels": [{"channel_id": r[0], "name": r[1], "messages": r[2]} for r in unassigned_rows],
                "total_messages": sum(r[2] for r in unassigned_rows)
            }

        return summary
    except Exception as e:
        print(f"⚠️ Error getting universes summary: {e}")
        return {}


def set_user_active_universe(user_id: Union[str, int], universe_name: str):
    """Saves user's currently selected default active universe for DM chats."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO user_active_universe (user_id, active_universe, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (str(user_id), universe_name.strip())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Could not set user active universe: {e}")


def get_user_active_universe(user_id: Union[str, int]) -> Optional[str]:
    """Gets user's currently active universe for DM chats."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT active_universe FROM user_active_universe WHERE user_id = ?", (str(user_id),))
        row = cursor.fetchone()
        conn.close()
        return row[0] if (row and row[0]) else None
    except Exception:
        return None


def save_query_history(channel_id: str, target_name: str, query: str, response: str = ""):
    """Saves search query and response into SQLite history and keeps the last 10 queries for this target/channel."""
    if not channel_id or not query:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO query_history (channel_id, target_name, query, response, timestamp) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (channel_id, target_name, query.strip(), response.strip())
        )
        # Delete older than top 10 for this channel
        cursor.execute("""
            DELETE FROM query_history 
            WHERE id NOT IN (
                SELECT id FROM query_history 
                WHERE channel_id = ? 
                ORDER BY timestamp DESC, id DESC 
                LIMIT 10
            ) AND channel_id = ?
        """, (channel_id, channel_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Could not save query history: {e}")


def get_recent_queries(channel_id: Optional[str] = None, limit: int = 10) -> List[Tuple[str, str, str]]:
    """Returns list of (query, target_name, timestamp) tuples."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        if channel_id:
            cursor.execute(
                "SELECT query, target_name, timestamp FROM query_history WHERE channel_id = ? ORDER BY timestamp DESC, id DESC LIMIT ?",
                (channel_id, limit)
            )
        else:
            cursor.execute(
                "SELECT query, target_name, timestamp FROM query_history ORDER BY timestamp DESC, id DESC LIMIT ?",
                (limit,)
            )
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def get_last_query_and_response(channel_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Retrieves the most recent query and response for a channel/contact."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT query, response FROM query_history WHERE channel_id = ? AND response IS NOT NULL AND response != '' ORDER BY timestamp DESC, id DESC LIMIT 1",
            (channel_id,)
        )
        row = cursor.fetchone()
        conn.close()
        return (row[0], row[1]) if row else (None, None)
    except Exception:
        return (None, None)


def save_lore_override(channel_id: str, target_name: str, topic_key: str, correction: str, universe_name: str = ""):
    """Saves user-confirmed lore facts and corrections permanently in SQLite."""
    if not correction:
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        if not universe_name and channel_id:
            cursor.execute("SELECT universe_name FROM channel_universes WHERE channel_id = ?", (channel_id,))
            row = cursor.fetchone()
            if row:
                universe_name = row[0]

        cursor.execute(
            "INSERT INTO lore_overrides (channel_id, target_name, topic_key, correction, universe_name, timestamp) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (channel_id or "", target_name or "Global", topic_key.strip() if topic_key else "General Lore", correction.strip(), universe_name.strip() if universe_name else "")
        )
        conn.commit()
        conn.close()
        print(f"💾 Saved lore override for '{target_name}' (Universe: '{universe_name or 'General'}'): {correction[:60]}...")
    except Exception as e:
        print(f"⚠️ Could not save lore override: {e}")


def get_lore_overrides(channel_id: Optional[str] = None, universe_name: Optional[str] = None) -> List[Tuple[str, str, str, str]]:
    """Returns list of (correction, topic_key, timestamp, universe_name) tuples."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        if channel_id:
            cursor.execute(
                "SELECT correction, topic_key, timestamp, COALESCE(universe_name, '') FROM lore_overrides WHERE channel_id = ? ORDER BY timestamp ASC",
                (channel_id,)
            )
        elif universe_name and universe_name.lower() not in ("all", "global", "all universes"):
            cursor.execute(
                "SELECT correction, topic_key, timestamp, COALESCE(universe_name, '') FROM lore_overrides WHERE universe_name = ? COLLATE NOCASE OR universe_name = '' ORDER BY timestamp ASC",
                (universe_name.strip(),)
            )
        else:
            cursor.execute(
                "SELECT correction, topic_key, timestamp, COALESCE(universe_name, '') FROM lore_overrides ORDER BY timestamp ASC"
            )
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def build_system_instruction(channel_id: Optional[str] = None, universe_name: Optional[str] = None) -> str:
    """Builds the authoritative forensic archivist system instruction with proactive lore synthesis and universe isolation."""
    overrides = get_lore_overrides(channel_id, universe_name)
    override_section = ""
    if overrides:
        override_lines = "\n".join(f"- {c[0]} (Added: {c[2][:10] if c[2] else 'User'}{f' • Universe: {c[3]}' if len(c) > 3 and c[3] else ''})" for c in overrides)
        override_section = (
            f"\n\n### USER-CONFIRMED LORE OVERRIDES & CORRECTIONS (HIGHEST PRIORITY):\n"
            f"The user has explicitly specified and verified the following canon facts/corrections for this universe/timeline. "
            f"Treat these details as 100% authoritative ground truth and integrate them completely into your analysis:\n"
            f"{override_lines}\n"
        )

    return (
        "You are Violet, a brilliant, authoritative Master Lorekeeper and Forensic Discord Archivist.\n"
        "Your mission is to uncover, synthesize, and explain the rich original roleplay lore, family trees, character backstories, and world history stored across the user's private Discord archives.\n\n"
        "CORE DIRECTIVES:\n"
        "1. PROACTIVE LORE DISCOVERY & SYNTHESIS (DO NOT WAIT TO BE SPOON-FED):\n"
        "• Act like a master detective and historian. Actively connect the dots across chat logs, character dialogue, family trees, and lore references across all channels.\n"
        "• Trace lineages, generations, parentage, sibling relationships (e.g., Carra & Evangel -> Quintuplets: Eliza, Archie, Nixxi, Sunflower, Kali), and character arcs.\n"
        "• Never give surface-level, lazy, or one-sentence brush-offs. Provide comprehensive, rich answers that reconstruct the full picture of the lore.\n"
        "• If the archives contain interesting related connections (e.g. backstory on why Sunflower died at birth and was preserved, how Nixxi and Archie were raised by Evangel, or how Archie became dragon king), proactively present those insights!\n\n"
        "2. PROACTIVE CONFIRMATION & VERIFICATION QUESTIONS:\n"
        "• When reconstructing complex lore where multiple versions or timeline evolutions exist, present the synthesized evidence first, then proactively ask thoughtful, specific confirmation questions to verify active canon with the user (e.g. 'The logs document X and Y in the Main Universe—does this match your current canon vision, or has this relationship changed?').\n\n"
        "3. CONFIDENT, PROFESSIONAL PERSONA (NO SYCOPHANCY OR TIMIDITY):\n"
        "• Speak with confidence, clarity, and intellectual curiosity. NEVER act timid, sycophantic, or excessively apologetic. Never say 'Boss', 'I am so so sorry', or make excuses. Present the lore with authority.\n\n"
        "4. STRICT ORIGINAL LORE ONLY (ZERO BOOK/EXTERNAL FICTION BLEED):\n"
        "• Everything in this archive is private original roleplay and creative lore. Do NOT reference published commercial books or real-world trivia unless explicitly asked.\n\n"
        "5. MULTI-UNIVERSE ISOLATION:\n"
        "• Keep different universes (e.g. Main Universe, Aestiolgel, Alternate Universes) clearly grouped and distinct so variant versions of characters never bleed together.\n\n"
        "6. CITE TIMESTAMPS & SPEAKERS:\n"
        "• Cite exact dates (YYYY-MM-DD), speaker usernames, and channel/universe names where evidence was found.\n\n"
        "7. FILTER NSFW:\n"
        "• Keep discussions clean and focused on narrative lore, family ties, character traits, and world-building."
        + override_section
    )


import time

MODEL_TIER_HIERARCHY = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-3.6-flash",
]

_model_cooldowns: Dict[str, Tuple[float, str]] = {}  # {model_name: (available_at_timestamp, reason_str)}


def get_active_model_pool() -> List[str]:
    """Returns candidate models sorted by priority with active (non-cooldown) models first."""
    now = time.time()
    primary = (os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash").strip()
    candidates = [primary] + [m for m in MODEL_TIER_HIERARCHY if m != primary]
    
    available = []
    cooling_down = []
    seen = set()
    for m in candidates:
        if m in seen:
            continue
        seen.add(m)
        cooldown_tuple = _model_cooldowns.get(m)
        if not cooldown_tuple or cooldown_tuple[0] <= now:
            available.append(m)
        else:
            cooling_down.append(m)
            
    return available + cooling_down


def get_model_pool_status() -> List[str]:
    """Returns formatted list of AI models in the tier hierarchy and their live status."""
    now = time.time()
    primary = (os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash").strip()
    status_list = []
    
    seen = set()
    candidates = [primary] + [m for m in MODEL_TIER_HIERARCHY if m != primary]
    for idx, m in enumerate(candidates):
        if m in seen:
            continue
        seen.add(m)
        cooldown_tuple = _model_cooldowns.get(m)
        is_primary = " *(Configured Primary)*" if m == primary else ""
        if cooldown_tuple and cooldown_tuple[0] > now:
            remaining = int(cooldown_tuple[0] - now)
            mins, secs = divmod(remaining, 60)
            hours, mins = divmod(mins, 60)
            time_str = f"{hours}h {mins}m" if hours > 0 else f"{mins}m {secs}s"
            reason = cooldown_tuple[1]
            status_list.append(f"🛑 `{m}`{is_primary}: **Cooldown** ({reason} — resets in {time_str})")
        else:
            status_list.append(f"🟢 `{m}`{is_primary}: **Active & Available**")
            
    return status_list


def generate_gemini_content_sync(contents: Any, system_instruction: str = "", temperature: float = 0.1) -> str:
    """Generates content via Gemini with automatic fallback across model tiers based on available RPM/TPM/RPD. Supports multimodal inputs."""
    model_pool = get_active_model_pool()
    now = time.time()
    last_err = None

    for model_name in model_pool:
        # Check if in active cooldown
        cooldown_tuple = _model_cooldowns.get(model_name)
        if cooldown_tuple and cooldown_tuple[0] > now:
            rem = int(cooldown_tuple[0] - now)
            print(f"⏳ Skipping '{model_name}' (on cooldown for {rem}s due to {cooldown_tuple[1]})...")
            continue

        try:
            config_kwargs = {"temperature": temperature, "max_output_tokens": 8192}
            if system_instruction:
                config_kwargs["system_instruction"] = system_instruction
                
            response = ai_client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs)
            )
            if response and response.text:
                # Clear cooldown on success
                _model_cooldowns.pop(model_name, None)
                print(f"✅ Gemini successfully generated answer using model '{model_name}'.")
                return response.text
        except Exception as e:
            last_err = e
            err_str = str(e)
            
            # Categorize error & calculate cooldown
            if "GenerateRequestsPerDay" in err_str or "free_tier_requests" in err_str or "limit: 20" in err_str:
                # Daily request limit (RPD)
                cooldown_seconds = 6 * 3600.0
                reason = "Daily RPD Limit"
                print(f"🛑 [Model Router] '{model_name}' exceeded daily quota (RPD). Set cooldown for 6 hours.")
            elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                # Per-minute rate limit (RPM/TPM)
                match = re.search(r"retry\s+(?:in\s+)?([\d\.]+)\s*s", err_str, re.IGNORECASE)
                cooldown_seconds = (float(match.group(1)) + 2.0) if match else 60.0
                reason = "RPM/TPM Rate Limit"
                print(f"⚠️ [Model Router] '{model_name}' hit rate limit ({reason}). Set cooldown for {cooldown_seconds:.1f}s.")
            elif "404" in err_str or "NOT_FOUND" in err_str:
                cooldown_seconds = 24 * 3600.0
                reason = "Model Unavailable (404)"
                print(f"⚠️ [Model Router] '{model_name}' not found / deprecated.")
            else:
                cooldown_seconds = 120.0
                reason = f"API Error: {str(e)[:40]}"
                print(f"⚠️ [Model Router] Error with '{model_name}': {e}.")

            _model_cooldowns[model_name] = (time.time() + cooldown_seconds, reason)
            print(f"🔄 Auto-switching to next best available model in tier hierarchy...")
            continue

    raise RuntimeError(f"All available Gemini models in tier pool failed. Last error: {last_err}")


def save_topic_cache(channel_id: str, topic_key: str, summary: str):
    """Saves a compiled topic summary in tiny SQLite format to save PC memory and speed up searches."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO topic_cache (channel_id, topic_key, summary, timestamp) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (channel_id, topic_key.lower().strip(), summary)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Could not save topic cache: {e}")


def get_cached_topic(channel_id: str, query: str) -> Optional[str]:
    """Retrieves cached topic summary if a direct match exists."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT summary FROM topic_cache WHERE channel_id = ? AND topic_key = ?",
            (channel_id, query.lower().strip())
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def index_batch_in_db(channel_id: str, batch: list):
    """Stores a chunk of messages into local SQLite database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        rows = []
        for m in batch:
            mid = str(m.get("id", ""))
            ts = m.get("timestamp", "")
            author = m.get("author", {}).get("name", "Unknown")
            content = m.get("content", "")
            if mid and (content or m.get("attachments")):
                rows.append((mid, channel_id, ts, author, content))

        cursor.executemany(
            "INSERT OR IGNORE INTO messages (id, channel_id, timestamp, author, content) VALUES (?, ?, ?, ?, ?)",
            rows
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ SQLite index error: {e}")


def save_channel_metadata(channel_id: str, name: str, total_msgs: int, first_ts: str, last_ts: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT OR REPLACE INTO channels (channel_id, name, last_parsed, total_messages, first_timestamp, last_timestamp)
               VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?)""",
            (channel_id, name, total_msgs, first_ts, last_ts)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ Could not save channel metadata: {e}")


def get_db_stats() -> dict:
    """Returns database size, message counts, and channel breakdown."""
    stats = {
        "total_messages": 0,
        "total_channels": 0,
        "total_topics": 0,
        "db_size_mb": 0.0,
        "channels": []
    }
    try:
        if DB_PATH.exists():
            stats["db_size_mb"] = round(DB_PATH.stat().st_size / (1024 * 1024), 2)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages")
        stats["total_messages"] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT channel_id) FROM messages")
        stats["total_channels"] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM topic_cache")
        stats["total_topics"] = cursor.fetchone()[0]

        cursor.execute("SELECT channel_id, name, total_messages, first_timestamp, last_timestamp, last_parsed FROM channels ORDER BY total_messages DESC LIMIT 10")
        for row in cursor.fetchall():
            stats["channels"].append({
                "channel_id": row[0],
                "name": row[1] or row[0],
                "total_messages": row[2] or 0,
                "first_ts": (row[3] or "")[:10],
                "last_ts": (row[4] or "")[:10],
                "last_parsed": (row[5] or "")[:16]
            })
        conn.close()
    except Exception as e:
        print(f"⚠️ Error fetching db stats: {e}")
    return stats


# 3. Background Parse Task Manager
@dataclass
class ParseJob:
    channel_id: str
    channel_name: str
    stage: str  # "queued", "exporting", "indexing", "completed", "failed"
    stage_detail: str
    total_messages: int = 0
    indexed_messages: int = 0
    sections_total: int = 0
    sections_done: int = 0
    started_at: datetime.datetime = field(default_factory=datetime.datetime.now)
    completed_at: Optional[datetime.datetime] = None
    error: Optional[str] = None
    stats: dict = field(default_factory=dict)


active_parse_jobs: Dict[str, ParseJob] = {}
recent_parse_jobs: Dict[str, ParseJob] = {}
dm_cache: Dict[str, str] = {}  # name -> channel_id


def refresh_dm_cache():
    global dm_cache
    if not DISCORD_USER_TOKEN or not os.path.exists(DCE_PATH):
        return

    try:
        import subprocess
        result = subprocess.run(
            [DCE_PATH, "dm", "-t", DISCORD_USER_TOKEN],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )
        lines = result.stdout.strip().splitlines()
        new_cache = {}
        for line in lines:
            parts = line.split("|", 1)
            if len(parts) == 2:
                cid = parts[0].strip()
                name = parts[1].strip()
                if cid.isdigit() and name:
                    new_cache[name] = cid
        dm_cache = new_cache
        print(f" Cached {len(dm_cache)} DM channels for instant search & autocomplete.")
    except Exception as e:
        print(f"⚠️ Could not load DM cache: {e}")


def resolve_target(target: Optional[str], default_channel_id: Optional[int]) -> Tuple[Optional[str], str]:
    """
    Resolves target string to (channel_id, channel_name).
    Handles direct IDs, contact usernames, case-insensitive substring matching.
    """
    if target:
        target_str = target.strip()
        # Direct numeric ID
        if target_str.isdigit():
            name = next((k for k, v in dm_cache.items() if v == target_str), target_str)
            return target_str, name

        # Check dm_cache exact match (case-insensitive)
        for name, cid in dm_cache.items():
            if name.lower() == target_str.lower():
                return cid, name

        # Check dm_cache partial match
        for name, cid in dm_cache.items():
            if target_str.lower() in name.lower():
                return cid, name

        # Check temp_exports folder for matching filenames
        temp_dir = Path("./temp_exports")
        if temp_dir.exists():
            for f in temp_dir.glob("*.json"):
                if target_str.lower() in f.stem.lower():
                    return f.stem, target_str

        return None, target_str

    if default_channel_id:
        cid_str = str(default_channel_id)
        name = next((k for k, v in dm_cache.items() if v == cid_str), cid_str)
        return cid_str, name

    return None, "Unknown"


def run_dce_sync(cmd: list, channel_label: str = "") -> Tuple[int, str]:
    """Executes DCE in a background worker thread, streaming stdout/stderr in real time to the console."""
    import subprocess
    output_lines = []
    prefix = f"[DCE @{channel_label}]" if channel_label else "[DCE]"
    try:
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
            bufsize=1
        )
        for line in iter(p.stdout.readline, ""):
            clean = line.strip()
            if clean:
                print(f"{prefix} {clean}")
                output_lines.append(clean)
        p.stdout.close()
        p.wait()
        return p.returncode, "\n".join(output_lines)
    except Exception as e:
        err = f"❌ Error executing DCE: {e}"
        print(err)
        return 1, err


async def export_channel_via_dce(channel_id: str, channel_name: str, max_retries: int = 2) -> Tuple[Optional[list], Optional[str], Optional[str]]:
    """Exports channel to temp_exports/{channel_id}.json safely using atomic file writes with automatic retry on transient socket drops."""
    temp_dir = Path("./temp_exports")
    temp_dir.mkdir(exist_ok=True)
    out_file = temp_dir / f"{channel_id}.json"
    tmp_file = temp_dir / f"{channel_id}_downloading.json"

    last_err = ""
    for attempt in range(1, max_retries + 1):
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except Exception:
                pass

        cmd = [
            DCE_PATH,
            "export",
            "-c", str(channel_id),
            "-t", DISCORD_USER_TOKEN,
            "-f", "Json",
            "-o", str(tmp_file.resolve()),
            "--fuck-russia", "true",
            "--respect-rate-limits", "true"
        ]

        returncode, stdout_str = await asyncio.get_event_loop().run_in_executor(
            None, lambda: run_dce_sync(cmd, channel_name)
        )

        if tmp_file.exists():
            try:
                with open(tmp_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                messages = data.get("messages", [])
                resolved_name = data.get("channel", {}).get("name", channel_name)
                
                if out_file.exists():
                    try:
                        out_file.unlink()
                    except Exception:
                        pass
                tmp_file.replace(out_file)
                return messages, resolved_name, None
            except Exception as e:
                last_err = f"Failed to read exported JSON (attempt {attempt}/{max_retries}): {e}"
                print(f"⚠️ {last_err}")
                if attempt < max_retries:
                    await asyncio.sleep(3)
        else:
            last_err = stdout_str or f"DCE did not generate export file (attempt {attempt}/{max_retries})"
            if attempt < max_retries and ("JsonReaderException" in last_err or "SocketException" in last_err or returncode != 0):
                print(f"⚠️ DCE transient export error, retrying attempt {attempt + 1}/{max_retries}...")
                await asyncio.sleep(3)

    return None, None, last_err


async def run_background_parse_task(job: ParseJob, force_refresh: bool = False):
    """Executes full export and sectional indexing in background without blocking the bot."""
    channel_id = job.channel_id
    temp_dir = Path("./temp_exports")
    temp_dir.mkdir(exist_ok=True)
    out_file = temp_dir / f"{channel_id}.json"

    try:
        messages = []
        channel_name = job.channel_name

        if out_file.exists() and not force_refresh:
            job.stage = "exporting"
            job.stage_detail = "Loading existing local export..."
            try:
                with open(out_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                messages = data.get("messages", [])
                channel_name = data.get("channel", {}).get("name", channel_name)
            except Exception:
                messages = []

        if not messages:
            job.stage = "exporting"
            job.stage_detail = f"Exporting chat history via DCE ({channel_id})..."
            dce_msgs, resolved_name, dce_err = await export_channel_via_dce(channel_id, channel_name)

            if dce_msgs is not None:
                messages = dce_msgs
                channel_name = resolved_name or channel_name
            else:
                job.stage = "failed"
                job.error = dce_err or "DCE CLI did not create output archive file"
                job.stage_detail = f"Failed: {job.error[:80]}"
                active_parse_jobs.pop(channel_id, None)
                recent_parse_jobs[channel_id] = job
                return

        total_msgs = len(messages)
        job.total_messages = total_msgs
        job.channel_name = channel_name

        if total_msgs == 0:
            job.stage = "completed"
            job.stage_detail = "No messages found in channel"
            job.completed_at = datetime.datetime.now()
            active_parse_jobs.pop(channel_id, None)
            recent_parse_jobs[channel_id] = job
            return

        # Sectional Indexing in chunks of 5,000 messages
        BATCH_SIZE = 5000
        sections = [messages[i:i + BATCH_SIZE] for i in range(0, total_msgs, BATCH_SIZE)]
        job.sections_total = len(sections)
        job.sections_done = 0
        job.indexed_messages = 0
        job.stage = "indexing"

        for idx, section in enumerate(sections, 1):
            job.stage_detail = f"Indexing section {idx}/{job.sections_total} ({job.indexed_messages:,}/{total_msgs:,} msgs)"
            await asyncio.get_event_loop().run_in_executor(None, lambda s=section: index_batch_in_db(channel_id, s))
            job.sections_done = idx
            job.indexed_messages += len(section)
            await asyncio.sleep(0.05)  # Yield to async event loop

        # Compute timeline and stats
        first_msg = messages[0] if messages else {}
        last_msg = messages[-1] if messages else {}
        first_ts = first_msg.get("timestamp", "")
        last_ts = last_msg.get("timestamp", "")
        first_author = first_msg.get("author", {}).get("name", "Unknown")
        last_author = last_msg.get("author", {}).get("name", "Unknown")

        years = Counter(m.get("timestamp", "")[:4] for m in messages if m.get("timestamp"))
        authors = Counter(m.get("author", {}).get("name", "Unknown") for m in messages)

        job.stats = {
            "first_ts": first_ts[:16].replace("T", " "),
            "last_ts": last_ts[:16].replace("T", " "),
            "first_author": first_author,
            "last_author": last_author,
            "first_snippet": first_msg.get("content", "")[:120],
            "last_snippet": last_msg.get("content", "")[:120],
            "years": sorted(years.items()),
            "top_authors": authors.most_common(5)
        }

        # Save metadata in database
        save_channel_metadata(channel_id, channel_name, total_msgs, first_ts, last_ts)

        job.stage = "completed"
        job.stage_detail = f"Completed ({total_msgs:,} messages indexed across {job.sections_total} sections)"
        job.completed_at = datetime.datetime.now()
        active_parse_jobs.pop(channel_id, None)
        recent_parse_jobs[channel_id] = job
        print(f"✅ Background parsing finished for '{channel_name}' ({total_msgs:,} messages).")

    except Exception as e:
        job.stage = "failed"
        job.error = str(e)
        job.stage_detail = f"Error: {e}"
        active_parse_jobs.pop(channel_id, None)
        recent_parse_jobs[channel_id] = job
        print(f"❌ Background parse error for {channel_id}: {e}")


async def run_background_server_parse_task(job: ParseJob, guild_id: str, guild_name: str, force_refresh: bool = False):
    """Exports and indexes an entire Discord server / roleplay guild across all channels and threads."""
    temp_dir = Path("./temp_exports")
    temp_dir.mkdir(exist_ok=True)
    server_dir = temp_dir / f"guild_{guild_id}"
    server_dir.mkdir(parents=True, exist_ok=True)

    token = DISCORD_USER_TOKEN or DISCORD_TOKEN

    if not guild_name or guild_name.strip() in ("", "Server:", "Server: "):
        gname, _ = get_guild_info_via_dce(guild_id)
        if gname:
            guild_name = gname
            job.channel_name = f"Server: {guild_name}"

    job.stage = "exporting"
    job.stage_detail = f"Exporting all text channels in '{guild_name}' via DCE (Parallel x3)..."

    # 1. Run DCE exportguild if DCE available and token present
    if os.path.exists(DCE_PATH) and token:
        output_arg = str(server_dir.resolve()) + ("\\" if os.name == "nt" else "/")
        cmd = [
            DCE_PATH,
            "exportguild",
            "-g", str(guild_id),
            "-t", token,
            "-f", "Json",
            "--parallel", "3",
            "-o", output_arg
        ]

        print(f"🏛️ Starting DCE export for entire guild '{guild_name}' ({guild_id})...")

        def run_dce_guild():
            import subprocess
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            for line in iter(p.stdout.readline, ''):
                line_str = line.strip()
                if line_str:
                    print(f"[DCE Guild @{guild_name}] {line_str}")
                    if "Exporting" in line_str or "Exported" in line_str or "Fetched" in line_str:
                        job.stage_detail = f"DCE: {line_str[:70]}"
            p.wait()
            return p.returncode

        await asyncio.get_event_loop().run_in_executor(None, run_dce_guild)

    # 2. Index all exported JSON files in server_dir
    json_files = list(server_dir.glob("*.json"))
    job.sections_total = len(json_files)
    job.sections_done = 0
    job.total_messages = 0
    job.indexed_messages = 0
    job.stage = "indexing"

    if not json_files:
        job.stage = "failed"
        job.error = "No channel JSON files found after export."
        job.stage_detail = f"Failed: No channel exports found for server '{guild_name}'."
        active_parse_jobs.pop(f"guild_{guild_id}", None)
        recent_parse_jobs[f"guild_{guild_id}"] = job
        return

    for idx, jf in enumerate(json_files, 1):
        try:
            with open(jf, "r", encoding="utf-8") as f:
                data = json.load(f)
            ch_data = data.get("channel", {})
            ch_id = str(ch_data.get("id") or jf.stem)
            ch_name = str(ch_data.get("name") or jf.stem)
            ch_full_label = f"#{ch_name} ({guild_name})"
            msgs = data.get("messages", [])

            job.stage_detail = f"Indexing channel {idx}/{len(json_files)}: #{ch_name} ({len(msgs):,} msgs)"

            if msgs:
                # Also save individual copy in temp_exports for fast individual lookups
                ch_out = temp_dir / f"{ch_id}.json"
                if not ch_out.exists() or force_refresh:
                    try:
                        with open(ch_out, "w", encoding="utf-8") as f_out:
                            json.dump(data, f_out)
                    except Exception:
                        pass

                # Sectional SQLite indexing
                BATCH_SIZE = 5000
                sections = [msgs[i:i + BATCH_SIZE] for i in range(0, len(msgs), BATCH_SIZE)]
                for sec in sections:
                    await asyncio.get_event_loop().run_in_executor(None, lambda s=sec, cid=ch_id: index_batch_in_db(cid, s))
                    job.indexed_messages += len(sec)
                    await asyncio.sleep(0.02)

                # Update channel metadata
                first_ts = msgs[0].get("timestamp", "")
                last_ts = msgs[-1].get("timestamp", "")
                save_channel_metadata(ch_id, ch_full_label, len(msgs), first_ts, last_ts)
                job.total_messages += len(msgs)

            job.sections_done = idx
        except Exception as e:
            print(f"⚠️ Error indexing guild channel file {jf.name}: {e}")

    job.stage = "completed"
    job.stage_detail = f"Completed ({job.sections_total} channels, {job.indexed_messages:,} msgs indexed)"
    job.completed_at = datetime.datetime.now()
    active_parse_jobs.pop(f"guild_{guild_id}", None)
    recent_parse_jobs[f"guild_{guild_id}"] = job
    print(f"✅ Successfully indexed entire server '{guild_name}' ({job.sections_total} channels, {job.indexed_messages:,} total messages).")


# 4. Message Search & Semantic Clustering Utilities
STOP_WORDS = {
    "what", "when", "where", "which", "who", "whom", "whose", "why", "how",
    "the", "and", "or", "but", "in", "on", "at", "to", "for", "with", "about",
    "against", "between", "into", "through", "during", "before", "after",
    "above", "below", "from", "up", "down", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "can", "could",
    "will", "would", "shall", "should", "may", "might", "must", "tell", "show",
    "find", "search", "explain", "describe", "give", "me", "you", "them", "her",
    "him", "they", "this", "that", "these", "those", "their", "there", "some",
    "all", "any", "chat", "message", "messages", "talk", "talked", "said", "say",
    "now", "exactly", "actually", "specifically", "character", "characters",
    "roleplay", "story", "universe", "lore", "know", "remember", "detail", "details",
    "info", "information", "please", "just", "also", "stuff", "thing", "things",
    "first", "last", "again", "like", "mean", "means", "meaning", "ever", "never",
    "always", "today", "yesterday", "time", "times", "much", "many", "someone", "anyone"
}


def extract_search_terms(query: str) -> List[str]:
    words = re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", query.lower())
    terms = [w for w in words if w not in STOP_WORDS]
    return terms if terms else words


def format_single_message(m: dict) -> Optional[str]:
    timestamp = m.get("timestamp", "")
    if len(timestamp) >= 16:
        timestamp = timestamp[:10] + " " + timestamp[11:16]
    author = m.get("author", {}).get("name", "Unknown")
    content = m.get("content", "").strip()
    attachments = [a.get("url", "") for a in m.get("attachments", [])]

    if not content and not attachments:
        return None

    line = f"[{timestamp}] {author}: {content}"
    if attachments:
        line += f" [Attachments: {', '.join(attachments)}]"
    return line


def cluster_relevant_messages(
    messages: list,
    query: str,
    window_before: int = 5,
    window_after: int = 5,
    max_messages: int = 4000
) -> Tuple[List[str], int, int]:
    """Scans full message history and pulls conversation clusters around keywords."""
    terms = extract_search_terms(query)
    total = len(messages)

    if not terms:
        recent = messages[-max_messages:]
        lines = [format_single_message(m) for m in recent]
        return [l for l in lines if l], 0, total

    matched_indices = set()
    for i, m in enumerate(messages):
        content_lower = m.get("content", "").lower()
        if any(term in content_lower for term in terms):
            for offset in range(-window_before, window_after + 1):
                idx = i + offset
                if 0 <= idx < total:
                    matched_indices.add(idx)

    if not matched_indices:
        recent = messages[-max_messages:]
        lines = [format_single_message(m) for m in recent]
        return [l for l in lines if l], 0, total

    sorted_indices = sorted(matched_indices)
    if len(sorted_indices) > max_messages:
        sorted_indices = sorted_indices[-max_messages:]

    formatted = []
    prev_idx = -1
    for i in sorted_indices:
        if prev_idx != -1 and i > prev_idx + 1:
            formatted.append("--- [Jump in Timeline] ---")
        prev_idx = i

        line = format_single_message(messages[i])
        if line:
            formatted.append(line)

    return formatted, len(matched_indices), total


def search_archive_messages(
    query: str,
    channel_id: Optional[str] = None,
    universe_name: Optional[str] = None,
    max_messages: int = 4000,
    window_before: int = 4,
    window_after: int = 4
) -> Tuple[List[str], int, int, str]:
    """
    Searches the SQLite message archive across a specific channel, an entire Universe, or globally across ALL channels and DMs.
    Returns: (formatted_lines, matched_count, total_count, scope_description)
    """
    terms = extract_search_terms(query)
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    cursor = conn.cursor()

    scope_desc = "Global Archive (All Channels & DMs)"
    channel_filter = ""
    params: list = []

    if channel_id:
        channel_filter = "WHERE m.channel_id = ?"
        params.append(str(channel_id))
        cursor.execute("SELECT name FROM channels WHERE channel_id = ?", (str(channel_id),))
        ch_row = cursor.fetchone()
        ch_name = ch_row[0] if ch_row else channel_id
        scope_desc = f"Channel: @{ch_name}"
    elif universe_name and universe_name.lower() not in ("all", "global", "all universes"):
        cursor.execute("SELECT COUNT(*) FROM channel_universes WHERE universe_name = ? COLLATE NOCASE", (universe_name.strip(),))
        uni_ch_count = cursor.fetchone()[0]
        if uni_ch_count > 0:
            channel_filter = "WHERE m.channel_id IN (SELECT channel_id FROM channel_universes WHERE universe_name = ? COLLATE NOCASE)"
            params.append(universe_name.strip())
            scope_desc = f"Universe: '{universe_name.strip()}' ({uni_ch_count} assigned channel(s))"
        else:
            channel_filter = ""
            scope_desc = f"Universe: '{universe_name.strip()}' (Global Archive)"

    # Total message count in scope
    count_query = f"SELECT COUNT(*) FROM messages m {channel_filter}"
    cursor.execute(count_query, params)
    count_row = cursor.fetchone()
    total_in_scope = count_row[0] if count_row else 0

    if total_in_scope == 0 and channel_filter:
        # Fallback to global search if scope had 0 messages
        channel_filter = ""
        params = []
        cursor.execute("SELECT COUNT(*) FROM messages")
        count_row = cursor.fetchone()
        total_in_scope = count_row[0] if count_row else 0

    # Fetch channel metadata & universe mappings
    cursor.execute("SELECT channel_id, name FROM channels")
    ch_names = dict(cursor.fetchall())
    cursor.execute("SELECT channel_id, universe_name FROM channel_universes")
    ch_unis = dict(cursor.fetchall())

    if not terms:
        sql = f"""
            SELECT m.id, m.channel_id, m.timestamp, m.author, m.content
            FROM messages m
            {channel_filter}
            ORDER BY m.timestamp DESC
            LIMIT ?
        """
        cursor.execute(sql, params + [max_messages])
        rows = cursor.fetchall()
        rows.reverse()
        conn.close()

        lines = []
        for mid, cid, ts, author, content in rows:
            c_label = ch_names.get(cid, cid)
            u_label = f" | Universe: {ch_unis[cid]}" if cid in ch_unis else ""
            ts_short = ts[:16].replace("T", " ") if len(ts) >= 16 else ts
            lines.append(f"[{ts_short}] [{c_label}{u_label}] {author}: {content}")
        return lines, 0, total_in_scope, scope_desc

    # 1. Try strict matching (AND) if multiple terms exist
    matched_rows = []
    if len(terms) > 1:
        and_clauses = " AND ".join(["m.content LIKE ?" for _ in terms])
        where_and = f"{channel_filter} {'AND' if channel_filter else 'WHERE'} ({and_clauses})"
        and_params = params + [f"%{t}%" for t in terms]
        match_sql_and = f"""
            SELECT m.id, m.channel_id, m.timestamp, m.author, m.content
            FROM messages m
            {where_and}
            ORDER BY m.timestamp DESC
            LIMIT 500
        """
        cursor.execute(match_sql_and, and_params)
        matched_rows = cursor.fetchall()

    # 2. If fewer than 20 results with AND, search with OR (ordered by timestamp DESC so modern messages are prioritized)
    if len(matched_rows) < 20:
        like_clauses = " OR ".join(["m.content LIKE ?" for _ in terms])
        where_clause = f"{channel_filter} {'AND' if channel_filter else 'WHERE'} ({like_clauses})"
        like_params = params + [f"%{t}%" for t in terms]

        match_sql = f"""
            SELECT m.id, m.channel_id, m.timestamp, m.author, m.content
            FROM messages m
            {where_clause}
            ORDER BY m.timestamp DESC
            LIMIT 800
        """
        cursor.execute(match_sql, like_params)
        or_rows = cursor.fetchall()
        
        seen_ids = {r[0] for r in matched_rows}
        for r in or_rows:
            if r[0] not in seen_ids:
                matched_rows.append(r)
                seen_ids.add(r[0])

    matched_count = len(matched_rows)

    if not matched_rows:
        sql = f"""
            SELECT m.id, m.channel_id, m.timestamp, m.author, m.content
            FROM messages m
            {channel_filter}
            ORDER BY m.timestamp DESC
            LIMIT ?
        """
        cursor.execute(sql, params + [min(max_messages, 1500)])
        rows = cursor.fetchall()
        rows.reverse()
        conn.close()

        lines = []
        for mid, cid, ts, author, content in rows:
            c_label = ch_names.get(cid, cid)
            u_label = f" | Universe: {ch_unis[cid]}" if cid in ch_unis else ""
            ts_short = ts[:16].replace("T", " ") if len(ts) >= 16 else ts
            lines.append(f"[{ts_short}] [{c_label}{u_label}] {author}: {content}")
        return lines, 0, total_in_scope, scope_desc

    channel_matched_ids: Dict[str, List[str]] = {}
    for mid, cid, ts, author, content in matched_rows:
        channel_matched_ids.setdefault(cid, []).append(mid)

    formatted_lines = []
    for cid, mids in channel_matched_ids.items():
        cursor.execute("SELECT id, timestamp, author, content FROM messages WHERE channel_id = ? ORDER BY timestamp ASC", (cid,))
        ch_all = cursor.fetchall()
        id_to_idx = {r[0]: idx for idx, r in enumerate(ch_all)}

        selected_indices = set()
        for mid in mids:
            if mid in id_to_idx:
                idx = id_to_idx[mid]
                for offset in range(-window_before, window_after + 1):
                    target_idx = idx + offset
                    if 0 <= target_idx < len(ch_all):
                        selected_indices.add(target_idx)

        c_label = ch_names.get(cid, cid)
        u_label = f" | Universe: {ch_unis[cid]}" if cid in ch_unis else ""

        prev_idx = -1
        for s_idx in sorted(selected_indices):
            if prev_idx != -1 and s_idx > prev_idx + 1:
                formatted_lines.append(f"--- [Jump in Timeline for {c_label}] ---")
            prev_idx = s_idx

            _, ts, author, content = ch_all[s_idx]
            ts_short = ts[:16].replace("T", " ") if len(ts) >= 16 else ts
            formatted_lines.append(f"[{ts_short}] [{c_label}{u_label}] {author}: {content}")

    conn.close()

    if len(formatted_lines) > max_messages:
        formatted_lines = formatted_lines[-max_messages:]

    return formatted_lines, matched_count, total_in_scope, scope_desc


# 5. Build Discord Client Architecture
dm_conversation_history: Dict[int, List[Dict[str, str]]] = {}


async def send_dm_chunks(
    channel: discord.abc.Messageable,
    text: str,
    filename_slug: str = "violet_dm_answer",
    channel_id: str = "",
    channel_label: str = "",
    query: str = ""
):
    """Sends multi-part response with .md file and interactive action buttons directly inside a DM channel."""
    file_buffer = io.BytesIO(text.encode("utf-8"))
    clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', filename_slug)[:30] or "violet_dm_answer"
    discord_file = discord.File(file_buffer, filename=f"{clean_slug}.md")

    try:
        exports_dir = Path("./exports")
        exports_dir.mkdir(exist_ok=True)
        with open(exports_dir / f"{clean_slug}.md", "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        print(f"⚠️ Could not write local markdown export: {e}")

    view = MarkdownViewerView(
        channel_id=channel_id,
        channel_label=channel_label,
        query=query,
        response_text=text
    )

    if len(text) <= 1950:
        await channel.send(text, file=discord_file, view=view)
        return

    chunks = []
    remaining = text
    while len(remaining) > 1950:
        split_idx = remaining.rfind("\n\n", 0, 1900)
        if split_idx == -1:
            split_idx = remaining.rfind("\n", 0, 1900)
        if split_idx == -1:
            split_idx = 1900
        chunks.append(remaining[:split_idx])
        remaining = remaining[split_idx:].lstrip()
    if remaining:
        chunks.append(remaining)

    total_chunks = len(chunks)
    for idx, chunk in enumerate(chunks, 1):
        is_last = (idx == total_chunks)
        header = f"📄 **[Part {idx}/{total_chunks}]**\n\n" if total_chunks > 1 else ""
        if is_last:
            await channel.send(header + chunk, file=discord_file, view=view)
        else:
            await channel.send(header + chunk)


class AntigravitySearchBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        intents.dm_messages = True
        super().__init__(
            command_prefix="!",
            intents=intents,
            status=discord.Status.online,
            activity=discord.Activity(type=discord.ActivityType.custom, name="custom", state="Dusting the archives")
        )

    async def setup_hook(self):
        init_db()
        print("🚀 Connected as AskViolet#8565. Syncing application commands...")
        try:
            synced = await self.tree.sync()
            cmd_names = [cmd.name for cmd in synced]
            print(f" Synced {len(synced)} command(s) globally: {cmd_names}")
        except Exception as e:
            print(f"❌ Failed to sync slash commands: {e}")

    async def on_ready(self):
        print(f"🚀 Success! Logged in as {self.user}")
        try:
            await self.change_presence(
                status=discord.Status.online,
                activity=discord.Activity(type=discord.ActivityType.custom, name="custom", state="Dusting the archives")
            )
        except Exception as e:
            print(f"⚠️ Could not set presence: {e}")
        if DISCORD_USER_TOKEN:
            print("🔑 Discord User Token active: Full DM search with DCE enabled!")
            asyncio.get_event_loop().run_in_executor(None, refresh_dm_cache)

    async def on_message(self, message: discord.Message):
        # Ignore bot's own messages
        if message.author == self.user:
            return

        # Direct 1-on-1 DM handling
        if isinstance(message.channel, discord.DMChannel):
            user_id = message.author.id
            content = message.content.strip() if message.content else ""
            if not content and not message.attachments:
                return

            print(f"\n💬 [DM Conversation from @{message.author.name}]: '{content[:60]}...'")

            async with message.channel.typing():
                try:
                    active_universe = get_user_active_universe(user_id)
                    uni_filter = active_universe if (active_universe and active_universe.lower() not in ("all", "global", "all universes")) else None

                    # Auto-capture canon lore if user provides corrections/lore in DM
                    lore_triggers = [
                        "is actually", "supposed to be", "the lore is", "correction:", "in main universe",
                        "in aestiolgel", "fell into a black hole", "is canon", "remember that", "is not the same",
                        "lore override", "lore fact", "canon fact"
                    ]
                    content_lower = content.lower()
                    if any(t in content_lower for t in lore_triggers) and len(content) > 20:
                        detected_uni = uni_filter or "Main Universe"
                        if "aestiolgel" in content_lower and "main universe" not in content_lower:
                            detected_uni = "Aestiolgel"
                        elif "main universe" in content_lower:
                            detected_uni = "Main Universe"
                        save_lore_override("", "AskViolet", "DM Canon Lore", content, universe_name=detected_uni)

                    # Fetch conversational memory for this user DM
                    conv_history = dm_conversation_history.setdefault(user_id, [])
                    conv_history.append({"role": "user", "content": content})
                    if len(conv_history) > 12:
                        dm_conversation_history[user_id] = conv_history[-12:]

                    recent_turns = "\n".join(
                        f"{'User' if turn['role'] == 'user' else 'Violet'}: {turn['content']}"
                        for turn in conv_history[-6:]
                    )

                    # Search focused on current question (with fallback to recent turns if query is generic)
                    search_terms = extract_search_terms(content)
                    search_text = content if search_terms else (content + " " + recent_turns)
                    
                    formatted_history_lines, matched_count, total_count, scope_desc = search_archive_messages(
                        query=search_text,
                        channel_id=None,
                        universe_name=uni_filter,
                        max_messages=3500
                    )
                    formatted_history = "\n".join(formatted_history_lines) if formatted_history_lines else "No messages found in archive for this search."

                    system_instruction = build_system_instruction(channel_id=None, universe_name=uni_filter) + (
                        "\n\n### 💬 CONVERSATIONAL DM MODE:\n"
                        "You are chatting directly with the user in a private 1-on-1 Discord DM. "
                        "Talk to the user like a thoughtful, helpful partner and research assistant. "
                        "Maintain the flow of conversation naturally based on recent turns. "
                        "You have full access to the user's entire multi-channel, multi-server, and multi-DM Discord history. "
                        "When referencing facts, characters, or events, cite exact dates (YYYY-MM-DD), speaker usernames, and the source channel/universe. "
                        "If characters or lore exist in multiple universes or channels, clearly explain the distinction between each version."
                    )

                    prompt = (
                        f"### ARCHIVE CONTEXT ({scope_desc} - {total_count:,} total messages indexed):\n"
                        f"{formatted_history}\n\n"
                        f"### RECENT CONVERSATION IN THIS DM:\n"
                        f"{recent_turns}\n\n"
                        f"### USER'S CURRENT MESSAGE:\n"
                        f"{content}"
                    )

                    contents: Any = prompt
                    image_parts = []
                    for att in message.attachments:
                        if att.content_type and att.content_type.startswith("image/"):
                            try:
                                img_bytes = await att.read()
                                image_parts.append(types.Part.from_bytes(data=img_bytes, mime_type=att.content_type))
                            except Exception:
                                pass

                    if image_parts:
                        contents = image_parts + [prompt]

                    final_answer = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: generate_gemini_content_sync(contents, system_instruction, temperature=0.2)
                    )
                    if not final_answer:
                        final_answer = "I searched through your chat archives, but couldn't find a direct record of that. Would you like me to look into a specific character, date, or scene?"

                    conv_history.append({"role": "assistant", "content": final_answer})
                    save_query_history("dm_global", scope_desc, f"[DM] {content}", final_answer)

                    slug = f"violet_dm_{'uni_' + uni_filter if uni_filter else 'global'}"
                    await send_dm_chunks(
                        message.channel,
                        final_answer,
                        filename_slug=slug,
                        channel_id="",
                        channel_label=scope_desc,
                        query=content
                    )
                    print(f"✅ Delivered conversational DM response to @{message.author.name} ({scope_desc}).")

                except Exception as e:
                    print(f"❌ DM error: {e}")
                    await message.channel.send(f"❌ Sorry, I ran into an error while analyzing the chat logs: {str(e)[:250]}")


bot = AntigravitySearchBot()


async def should_be_ephemeral(interaction: discord.Interaction) -> bool:
    """
    Returns False ONLY if the interaction is happening directly inside the bot's 1-on-1 private DM chat with the user.
    In servers, group DMs, or other users' DMs (via User App install), returns True so responses are hidden from everyone else.
    """
    # 1. Any Guild / Server is NEVER the bot's private DM -> Ephemeral
    if interaction.guild_id is not None or interaction.guild is not None:
        return True

    # 2. Group DMs with multiple users are not 1-on-1 bot DM -> Ephemeral
    if isinstance(interaction.channel, discord.GroupChannel):
        return True

    # 3. Check if channel is the bot's direct 1-on-1 DM channel with this user
    try:
        if interaction.user:
            dm_channel = interaction.user.dm_channel
            if dm_channel is None:
                dm_channel = await interaction.user.create_dm()
            if dm_channel and interaction.channel_id == dm_channel.id:
                return False  # This is the bot's direct DM! Not ephemeral.
    except Exception as e:
        print(f"⚠️ Could not verify bot DM channel: {e}")

    # Fallback: keep it ephemeral (safe default for other DMs / Group chats)
    return True


class CorrectionModal(discord.ui.Modal, title="✏️ Fix Inconsistency or Add Lore"):
    correction_input = discord.ui.TextInput(
        label="Confirmed Lore Detail / Inconsistency Fix",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Meadowsong's original clan was named AshClan / EmberClan",
        required=True,
        max_length=2000
    )

    def __init__(self, channel_id: str, channel_label: str, original_query: str, previous_response: str):
        super().__init__()
        self.channel_id = channel_id
        self.channel_label = channel_label
        self.original_query = original_query
        self.previous_response = previous_response

    async def on_submit(self, interaction: discord.Interaction):
        is_ephemeral = await should_be_ephemeral(interaction)
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=is_ephemeral)
        except Exception:
            pass

        try:
            correction_text = self.correction_input.value.strip()
            # 1. Save lore override in SQLite
            save_lore_override(self.channel_id, self.channel_label, self.original_query, correction_text)

            # 2. Re-synthesize with Gemini combining previous response + correction
            formatted_history_lines, _, total_count, scope_desc = search_archive_messages(
                query=self.original_query + " " + correction_text,
                channel_id=self.channel_id if self.channel_id else None,
                max_messages=3000
            )
            formatted_history = "\n".join(formatted_history_lines)

            system_instruction = build_system_instruction(self.channel_id)
            prompt = (
                f"### DISCORD CHAT ARCHIVE ({scope_desc} - {total_count:,} total messages):\n"
                f"{formatted_history}\n\n"
                f"### PREVIOUS QUERY:\n"
                f"{self.original_query}\n\n"
                f"### PREVIOUS REPORT:\n"
                f"{self.previous_response}\n\n"
                f"### USER CORRECTION / LORE UPDATE INSTRUCTION:\n"
                f"{correction_text}\n\n"
                f"### TASK:\n"
                f"Provide an updated, fully unified, and corrected report integrating the user's correction seamlessly into the previous findings."
            )

            updated_answer = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: generate_gemini_content_sync(prompt, system_instruction, temperature=0.1)
            )
            if not updated_answer:
                updated_answer = "Updated report generated."

            # Save to SQLite query_history and topic_cache
            save_query_history(self.channel_id or "global", self.channel_label, f"[Correction] {self.original_query}", updated_answer)
            if self.channel_id:
                save_topic_cache(self.channel_id, self.original_query, updated_answer)

            header = f"## ✅ Lore Inconsistency Corrected & Saved!\n*Added confirmed fact:* **\"{correction_text}\"**\n\n"
            slug = f"violet_{self.channel_label}_corrected"
            await send_ephemeral_chunks(
                interaction,
                header + updated_answer,
                filename_slug=slug,
                channel_id=self.channel_id,
                channel_label=self.channel_label,
                query=self.original_query,
                response_text=updated_answer,
                is_ephemeral=is_ephemeral
            )
        except Exception as e:
            print(f"❌ Error processing lore correction: {e}")
            try:
                err_msg = f"❌ **Error generating corrected report:** {str(e)[:250]}\n\n*Note: Your lore override was still successfully recorded to SQLite!*"
                if interaction.response.is_done():
                    await interaction.followup.send(err_msg, ephemeral=is_ephemeral)
                else:
                    await interaction.response.send_message(err_msg, ephemeral=is_ephemeral)
            except Exception:
                pass


class FollowupQuestionModal(discord.ui.Modal, title="💬 Ask a Follow-up Question"):
    question_input = discord.ui.TextInput(
        label="What would you like to know?",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Did Meadowsong have any living siblings? What happened to Nightshade's family?",
        required=True,
        max_length=2000
    )

    def __init__(self, channel_id: str, channel_label: str, original_query: str, previous_response: str):
        super().__init__()
        self.channel_id = channel_id
        self.channel_label = channel_label
        self.original_query = original_query
        self.previous_response = previous_response

    async def on_submit(self, interaction: discord.Interaction):
        is_ephemeral = await should_be_ephemeral(interaction)
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=is_ephemeral)
        except Exception:
            pass

        try:
            question_text = self.question_input.value.strip()

            search_query = f"{self.original_query} {question_text}"
            formatted_history_lines, _, total_count, scope_desc = search_archive_messages(
                query=search_query,
                channel_id=self.channel_id if self.channel_id else None,
                max_messages=3500
            )
            formatted_history = "\n".join(formatted_history_lines)

            system_instruction = build_system_instruction(self.channel_id)
            prompt = (
                f"### DISCORD CHAT ARCHIVE ({scope_desc} - {total_count:,} total messages):\n"
                f"{formatted_history}\n\n"
                f"### PREVIOUS DOSSIER / REPORT CONTEXT:\n"
                f"{self.previous_response}\n\n"
                f"### USER'S FOLLOW-UP INQUIRY:\n"
                f"{question_text}\n\n"
                f"### TASK:\n"
                f"Answer the user's follow-up inquiry specifically and thoroughly based on the provided Discord chat archive. "
                f"Cite speaker usernames, source channel/universe, and exact dates (YYYY-MM-DD) from the chat log where available. "
                f"If details are not in the chat history or confirmed overrides, clearly state so."
            )

            final_answer = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: generate_gemini_content_sync(prompt, system_instruction, temperature=0.1)
            )
            if not final_answer:
                final_answer = "No response generated for this question."

            save_query_history(self.channel_id or "global", self.channel_label, f"[Q&A] {question_text}", final_answer)

            header = f"## 💬 Follow-up Answer: *\"{question_text}\"*\n\n"
            slug = f"violet_{self.channel_label}_qa"
            await send_ephemeral_chunks(
                interaction,
                header + final_answer,
                filename_slug=slug,
                channel_id=self.channel_id,
                channel_label=self.channel_label,
                query=question_text,
                response_text=final_answer,
                is_ephemeral=is_ephemeral
            )
        except Exception as e:
            print(f"❌ Error processing follow-up question: {e}")
            try:
                err_msg = f"❌ **Error answering question:** {str(e)[:250]}"
                if interaction.response.is_done():
                    await interaction.followup.send(err_msg, ephemeral=is_ephemeral)
                else:
                    await interaction.response.send_message(err_msg, ephemeral=is_ephemeral)
            except Exception:
                pass


class MarkdownViewerView(discord.ui.View):
    def __init__(self, channel_id: str = "", channel_label: str = "", query: str = "", response_text: str = ""):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.channel_label = channel_label
        self.query = query
        self.response_text = response_text

        if channel_id and response_text:
            ask_btn = discord.ui.Button(label="💬 Ask Question", style=discord.ButtonStyle.success)
            ask_btn.callback = self.on_ask_clicked
            self.add_item(ask_btn)

            correct_btn = discord.ui.Button(label="✏️ Fix Lore", style=discord.ButtonStyle.primary)
            correct_btn.callback = self.on_correct_clicked
            self.add_item(correct_btn)

        self.add_item(discord.ui.Button(label="🌐 View in StackEdit", url="https://stackedit.io/app", style=discord.ButtonStyle.link))
        self.add_item(discord.ui.Button(label="🌐 View in Dillinger", url="https://dillinger.io/", style=discord.ButtonStyle.link))

    async def on_ask_clicked(self, interaction: discord.Interaction):
        modal = FollowupQuestionModal(
            channel_id=self.channel_id,
            channel_label=self.channel_label,
            original_query=self.query,
            previous_response=self.response_text
        )
        await interaction.response.send_modal(modal)

    async def on_correct_clicked(self, interaction: discord.Interaction):
        modal = CorrectionModal(
            channel_id=self.channel_id,
            channel_label=self.channel_label,
            original_query=self.query,
            previous_response=self.response_text
        )
        await interaction.response.send_modal(modal)


async def send_ephemeral_chunks(
    interaction: discord.Interaction,
    text: str,
    filename_slug: str = "violet_report",
    channel_id: str = "",
    channel_label: str = "",
    query: str = "",
    response_text: str = "",
    is_ephemeral: Optional[bool] = None
):
    """Sends long text split into <= 2000 character chunks with part numbers, attaches a downloadable .md file, and provides online viewer buttons."""
    if is_ephemeral is None:
        is_ephemeral = await should_be_ephemeral(interaction)
    file_buffer = io.BytesIO(text.encode("utf-8"))
    clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', filename_slug)[:30] or "violet_report"
    discord_file = discord.File(file_buffer, filename=f"{clean_slug}.md")

    try:
        exports_dir = Path("./exports")
        exports_dir.mkdir(exist_ok=True)
        with open(exports_dir / f"{clean_slug}.md", "w", encoding="utf-8") as f:
            f.write(text)
    except Exception as e:
        print(f"⚠️ Could not write local markdown export: {e}")

    view = MarkdownViewerView(
        channel_id=channel_id,
        channel_label=channel_label,
        query=query,
        response_text=response_text or text
    )

    if len(text) <= 1950:
        if interaction.response.is_done():
            await interaction.followup.send(text, file=discord_file, view=view, ephemeral=is_ephemeral)
        else:
            await interaction.response.send_message(text, file=discord_file, view=view, ephemeral=is_ephemeral)
        return

    chunks = []
    remaining = text
    while len(remaining) > 1950:
        split_idx = remaining.rfind("\n\n", 0, 1900)
        if split_idx == -1:
            split_idx = remaining.rfind("\n", 0, 1900)
        if split_idx == -1:
            split_idx = 1900
        chunks.append(remaining[:split_idx])
        remaining = remaining[split_idx:].lstrip()
    if remaining:
        chunks.append(remaining)

    total_parts = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        prefix = f"**[Part {i}/{total_parts}]**\n\n" if total_parts > 1 else ""
        is_last = (i == total_parts)
        try:
            if i == 1:
                if interaction.response.is_done():
                    if is_last:
                        await interaction.followup.send(prefix + chunk, file=discord_file, view=view, ephemeral=is_ephemeral)
                    else:
                        await interaction.followup.send(prefix + chunk, ephemeral=is_ephemeral)
                else:
                    if is_last:
                        await interaction.response.send_message(prefix + chunk, file=discord_file, view=view, ephemeral=is_ephemeral)
                    else:
                        await interaction.response.send_message(prefix + chunk, ephemeral=is_ephemeral)
            else:
                if is_last:
                    await interaction.followup.send(prefix + chunk, file=discord_file, view=view, ephemeral=is_ephemeral)
                else:
                    await interaction.followup.send(prefix + chunk, ephemeral=is_ephemeral)

            if total_parts > 1 and i < total_parts:
                await asyncio.sleep(0.35)
        except Exception as e:
            print(f"⚠️ Warning sending interaction chunk {i}/{total_parts}: {e}")


async def target_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    try:
        choices = []
        current_lower = current.lower().strip()
        for name, cid in dm_cache.items():
            if current_lower in name.lower() or current_lower in cid:
                label = f"{name}" if len(name) <= 90 else f"{name[:87]}..."
                choices.append(app_commands.Choice(name=label, value=cid))
                if len(choices) >= 25:
                    break
        return choices
    except Exception:
        return []


async def universe_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    try:
        unis = get_universe_names()
        choices = [app_commands.Choice(name="🌐 All Universes / Global Archive", value="All Universes")]
        current_clean = (current or "").strip()
        
        # If user is typing a custom universe not in list yet, offer it immediately as a choice
        if current_clean and not any(current_clean.lower() == u.lower() for u in unis) and current_clean.lower() not in ("all", "global", "all universes"):
            choices.append(app_commands.Choice(name=f"✨ Create / Use: '{current_clean}'", value=current_clean))

        for u in unis:
            if not current_clean or current_clean.lower() in u.lower():
                choices.append(app_commands.Choice(name=f"🌌 {u}", value=u))
                if len(choices) >= 25:
                    break
        return choices[:25]
    except Exception as e:
        print(f"⚠️ universe_autocomplete error: {e}")
        return [app_commands.Choice(name="🌐 All Universes / Global Archive", value="All Universes")]


async def query_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    try:
        channel_id = str(interaction.channel_id) if interaction.channel_id else None
        recent = get_recent_queries(channel_id=channel_id, limit=10)
        if not recent:
            recent = get_recent_queries(channel_id=None, limit=10)

        choices = []
        current_lower = current.lower().strip()
        seen = set()
        for q, t_name, ts in recent:
            if not q or q in seen:
                continue
            seen.add(q)
            if not current_lower or current_lower in q.lower():
                label = f"🕒 {q}" if len(q) <= 95 else f"🕒 {q[:92]}..."
                val = q if len(q) <= 100 else q[:100]
                choices.append(app_commands.Choice(name=label, value=val))
                if len(choices) >= 10:
                    break
        return choices
    except Exception:
        return []


# 6. /universe Slash Command Suite (Group Channels & DMs into Isolated Roleplay Universes)
universe_group = app_commands.Group(
    name="universe",
    description="Manage and group roleplay universes, channels, and lore",
    allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
    allowed_installs=app_commands.AppInstallationType(guild=True, user=True)
)


@universe_group.command(name="set", description="Assign a channel, DM contact, or entire server to a specific Universe")
@app_commands.describe(
    name="Name of the Universe (e.g. 'Universe 1', 'Main Roleplay', 'Sci-Fi AU')",
    entire_server="If True and run in a server, assigns all channels & threads across the entire server to this universe (Default: False)",
    target="Optional: Select a specific DM contact or channel to assign (defaults to current chat)"
)
@app_commands.autocomplete(name=universe_autocomplete, target=target_autocomplete)
async def universe_set(interaction: discord.Interaction, name: str, entire_server: bool = False, target: str = None):
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        clean_uni = name.strip()

        if entire_server:
            guild_id = str(interaction.guild_id or (interaction.guild.id if interaction.guild else ""))
            if not guild_id:
                await interaction.followup.send(
                    "❌ **Cannot assign entire server from DMs:** Please run `/universe set entire_server: True` directly inside the Discord server you wish to assign.",
                    ephemeral=is_ephemeral
                )
                return

            guild_name = interaction.guild.name if (interaction.guild and interaction.guild.name) else ""
            channel_tuples = []
            if interaction.guild and interaction.guild.channels:
                for ch in interaction.guild.channels:
                    if isinstance(ch, (discord.TextChannel, discord.ForumChannel, discord.VoiceChannel, discord.StageChannel)):
                        channel_tuples.append((str(ch.id), f"#{ch.name} ({guild_name})"))
                for th in interaction.guild.threads:
                    channel_tuples.append((str(th.id), f"🧵 #{th.name} ({guild_name})"))

            # If user-installed app context has empty guild.channels, query DCE
            if not channel_tuples or not guild_name:
                dce_gname, dce_channels = get_guild_info_via_dce(guild_id)
                guild_name = dce_gname or guild_name or f"Server {guild_id}"
                if dce_channels:
                    channel_tuples = [(cid, f"#{cname} ({guild_name})") for cid, cname in dce_channels]

            count = set_multiple_channels_universe(channel_tuples, clean_uni)
            msg = (
                f"## 🏛️ Entire Server Assigned to Universe!\n\n"
                f"• **Server Name:** **`{guild_name}`** (`{guild_id}`)\n"
                f"• **Assigned Universe:** **`{clean_uni}`**\n"
                f"• **Total Channels & Threads Linked:** **{count}** channels\n\n"
                f"⚡ *All roleplay history and future queries across `{guild_name}` are now linked together under `{clean_uni}`!*"
            )
            await interaction.followup.send(msg, ephemeral=is_ephemeral)
            return

        channel_id, channel_name = resolve_target(target, interaction.channel_id)
        if not channel_id:
            channel_id = str(interaction.channel_id)
            channel_name = "Current Chat"

        set_channel_universe(channel_id, clean_uni)

        msg = (
            f"## 🌌 Channel Assigned to Universe!\n\n"
            f"• **Channel / Contact:** `@{channel_name}` (`{channel_id}`)\n"
            f"• **Assigned Universe:** **`{clean_uni}`**\n\n"
            f"⚡ *All queries and lore under `{clean_uni}` will now bundle this channel's history together without character confusion!*"
        )
        await interaction.followup.send(msg, ephemeral=is_ephemeral)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to set universe: {str(e)}", ephemeral=is_ephemeral)


@universe_group.command(name="switch", description="Switch your active default Universe for private 1-on-1 DM chats")
@app_commands.describe(
    name="Select which Universe to focus on in DM conversations (or 'All Universes' for global search)"
)
@app_commands.autocomplete(name=universe_autocomplete)
async def universe_switch(interaction: discord.Interaction, name: str):
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        clean_uni = name.strip()
        if clean_uni.lower() in ("all", "global", "all universes", "none", "reset"):
            set_user_active_universe(interaction.user.id, "")
            msg = (
                "## 🌐 Switched to Global Archive Mode\n\n"
                "• **Active Focus:** **All Universes & Channels (Global Search)**\n\n"
                "⚡ *In private DM chats, Violet will now search across all indexed channels and automatically distinguish character variants across universes!*"
            )
        else:
            set_user_active_universe(interaction.user.id, clean_uni)
            msg = (
                f"## 🌌 Active DM Universe Switched!\n\n"
                f"• **Active Universe Focus:** **`{clean_uni}`**\n\n"
                f"⚡ *In private DM chats, Violet will now automatically focus queries and lore specifically on the `{clean_uni}` timeline.*"
            )
        await interaction.followup.send(msg, ephemeral=is_ephemeral)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to switch universe: {str(e)}", ephemeral=is_ephemeral)


@universe_group.command(name="list", description="Display all registered universes, assigned channels, and message counts")
async def universe_list(interaction: discord.Interaction):
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        summary = get_all_universes_summary()
        active_uni = get_user_active_universe(interaction.user.id)

        lines = ["# 🌌 Roleplay Universes & Multi-Universe Grouping\n"]
        if active_uni:
            lines.append(f"🎯 **Your Active DM Focus:** **`{active_uni}`**\n")
        else:
            lines.append("🎯 **Your Active DM Focus:** **`Global Archive (All Universes)`**\n")

        if not summary:
            lines.append(
                "ℹ️ *No universes configured yet.*\n\n"
                "Use `/universe set name: [Universe Name]` to assign your channels or DMs to distinct universes!"
            )
        else:
            for u_name, data in summary.items():
                icon = "🌐" if "Unassigned" in u_name else "🌌"
                lines.append(f"### {icon} Universe: **`{u_name}`** ({data['total_messages']:,} msgs)")
                for ch in data["channels"][:10]:
                    lines.append(f"• `@{ch['name']}`: **{ch['messages']:,}** messages (`{ch['channel_id']}`)")
                if len(data["channels"]) > 10:
                    lines.append(f"  *...and {len(data['channels']) - 10} more channels*")
                lines.append("")

        lines.append("---\n💡 *Use `/universe switch` to change your DM focus, or `/universe set` to assign channels!*")
        await send_ephemeral_chunks(interaction, "\n".join(lines), filename_slug="violet_universes", is_ephemeral=is_ephemeral)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to list universes: {str(e)}", ephemeral=is_ephemeral)


@universe_group.command(name="remove", description="Remove a channel, DM, or entire server from its assigned universe")
@app_commands.describe(
    entire_server="If True and run in a server, removes all channels across the entire server from their universes (Default: False)",
    target="Optional: Select a specific DM contact or channel to remove (defaults to current chat)"
)
@app_commands.autocomplete(target=target_autocomplete)
async def universe_remove(interaction: discord.Interaction, entire_server: bool = False, target: str = None):
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        if entire_server:
            guild_id = str(interaction.guild_id or (interaction.guild.id if interaction.guild else ""))
            if not guild_id:
                await interaction.followup.send(
                    "❌ **Cannot remove entire server from DMs:** Please run `/universe remove entire_server: True` directly inside the Discord server you wish to unassign.",
                    ephemeral=is_ephemeral
                )
                return

            guild_name = interaction.guild.name if (interaction.guild and interaction.guild.name) else ""
            channel_ids = []
            if interaction.guild and interaction.guild.channels:
                channel_ids = [str(ch.id) for ch in interaction.guild.channels] + [str(th.id) for th in interaction.guild.threads]

            if not channel_ids:
                dce_gname, dce_channels = get_guild_info_via_dce(guild_id)
                guild_name = dce_gname or guild_name or f"Server {guild_id}"
                channel_ids = [cid for cid, _ in dce_channels]

            count = remove_multiple_channels_universe(channel_ids)
            await interaction.followup.send(
                f"✅ Removed all **{count}** channels across **`{guild_name}`** from their assigned universes. They are now unassigned / global.",
                ephemeral=is_ephemeral
            )
            return

        channel_id, channel_name = resolve_target(target, interaction.channel_id)
        if not channel_id:
            channel_id = str(interaction.channel_id)
            channel_name = "Current Chat"

        remove_channel_universe(channel_id)
        await interaction.followup.send(
            f"✅ Removed `@{channel_name}` (`{channel_id}`) from its assigned universe. It is now unassigned / global.",
            ephemeral=is_ephemeral
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to remove channel from universe: {str(e)}", ephemeral=is_ephemeral)


@universe_group.command(name="delete", description="Delete an entire universe tag")
@app_commands.describe(
    name="Name of the universe to delete"
)
@app_commands.autocomplete(name=universe_autocomplete)
async def universe_delete(interaction: discord.Interaction, name: str):
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        delete_universe(name)
        await interaction.followup.send(
            f"✅ Deleted universe **`{name}`**. All its channels are now unassigned / global.",
            ephemeral=is_ephemeral
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to delete universe: {str(e)}", ephemeral=is_ephemeral)


@universe_group.command(name="unassigned", description="View all channels and DMs that do not have a universe assigned yet")
async def universe_unassigned(interaction: discord.Interaction):
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        summary = get_all_universes_summary()
        unassigned_data = summary.get("Unassigned / Global", {"channels": [], "total_messages": 0})
        channels = unassigned_data.get("channels", [])
        total_msgs = unassigned_data.get("total_messages", 0)

        if not channels:
            await interaction.followup.send("🎉 **All channels and DMs are currently assigned to universes!**", ephemeral=is_ephemeral)
            return

        lines = [
            f"# ⚠️ Unassigned / Global Channels ({len(channels)} total, {total_msgs:,} msgs)\n",
            "These channels are currently not assigned to any specific Universe timeline.\n"
        ]

        for idx, ch in enumerate(channels, 1):
            lines.append(f"**{idx}.** `@{ch['name']}`: **{ch['messages']:,}** msgs (`{ch['channel_id']}`)")

        lines.append("\n---\n💡 *Assign any channel using `/universe set name: [Universe] target: [Contact]` or run `python universe_manager.py` for the interactive visual web manager!*")
        await send_ephemeral_chunks(interaction, "\n".join(lines), filename_slug="violet_unassigned_channels", is_ephemeral=is_ephemeral)
    except Exception as e:
        await interaction.followup.send(f"❌ Error fetching unassigned channels: {str(e)}", ephemeral=is_ephemeral)


@universe_group.command(name="channels", description="List all channels, DMs, and servers assigned to a specific universe")
@app_commands.describe(
    name="Name of the universe to inspect"
)
@app_commands.autocomplete(name=universe_autocomplete)
async def universe_channels(interaction: discord.Interaction, name: str):
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        clean_uni = name.strip()
        summary = get_all_universes_summary()
        u_data = summary.get(clean_uni)

        if not u_data:
            await interaction.followup.send(f"❌ Universe **`{clean_uni}`** was not found or has no assigned channels.", ephemeral=is_ephemeral)
            return

        channels = u_data.get("channels", [])
        total_msgs = u_data.get("total_messages", 0)

        lines = [
            f"# 🌌 Channels in Universe: **`{clean_uni}`** ({len(channels)} channels, {total_msgs:,} msgs)\n"
        ]

        for idx, ch in enumerate(channels, 1):
            lines.append(f"**{idx}.** `@{ch['name']}`: **{ch['messages']:,}** msgs (`{ch['channel_id']}`)")

        lines.append(f"\n---\n💡 *Use `/universe set name: {clean_uni}` to add more channels to this universe!*")
        await send_ephemeral_chunks(interaction, "\n".join(lines), filename_slug=f"violet_channels_{clean_uni[:15]}", is_ephemeral=is_ephemeral)
    except Exception as e:
        await interaction.followup.send(f"❌ Error inspecting universe: {str(e)}", ephemeral=is_ephemeral)


bot.tree.add_command(universe_group)


# 7. /parse Slash Command (Sectional Background Parsing & Server Export)
@bot.tree.command(name="parse", description="Exports, indexes, and caches chat history in sections in the background")
@app_commands.user_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    target="Optional: Select a specific DM contact or channel to parse (defaults to current chat)",
    parse_entire_server="If True and run in a server, indexes all channels and threads across the entire server (Default: False)",
    force_refresh="Set to True to re-download fresh history from Discord instead of using local archive (Default: False)"
)
@app_commands.autocomplete(target=target_autocomplete)
async def parse_channel(
    interaction: discord.Interaction,
    target: str = None,
    parse_entire_server: bool = False,
    force_refresh: bool = False
):
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        if parse_entire_server:
            if not interaction.guild:
                await interaction.followup.send(
                    "❌ **Cannot parse entire server from DMs:** Please run `/parse parse_entire_server: True` directly inside the Discord server you wish to index.",
                    ephemeral=is_ephemeral
                )
                return

            guild_id = str(interaction.guild.id)
            guild_name = interaction.guild.name
            job_key = f"guild_{guild_id}"

            if job_key in active_parse_jobs:
                job = active_parse_jobs[job_key]
                await interaction.followup.send(
                    f"⏳ **Server parsing is already in progress for `{guild_name}`!**\n\n"
                    f"• **Current Stage:** {job.stage_detail}\n"
                    f"• **Progress:** {job.indexed_messages:,} msgs indexed ({job.sections_done}/{job.sections_total} channels)\n\n"
                    "💡 *Run `/status` to monitor live server parsing progress.*",
                    ephemeral=is_ephemeral
                )
                return

            job = ParseJob(
                channel_id=job_key,
                channel_name=f"Server: {guild_name}",
                stage="queued",
                stage_detail=f"Queued full server export and indexing for '{guild_name}'..."
            )
            active_parse_jobs[job_key] = job
            asyncio.create_task(run_background_server_parse_task(job, guild_id, guild_name, force_refresh=force_refresh))

            msg = (
                f"## 🏛️ Full Server Background Parsing Initiated!\n\n"
                f"• **Server Name:** **`{guild_name}`**\n"
                f"• **Server ID:** `{guild_id}`\n"
                f"• **Mode:** Multi-Channel Export & Sectional Indexing across all text channels and threads\n"
                f"• **Force Refresh:** `{'Yes (re-downloading all channels)' if force_refresh else 'No (using cached channels if available)'}`\n\n"
                f"⚡ *This runs completely in the background so your Discord won't freeze. "
                f"Type `/status` anytime to view live server parsing progress, channel counts, and database health!*"
            )
            await interaction.followup.send(msg, ephemeral=is_ephemeral)
            return

        channel_id, channel_name = resolve_target(target, interaction.channel_id)

        if not channel_id:
            await interaction.followup.send(
                f"❌ Could not resolve contact or channel for `{target or 'Current Chat'}`.\n"
                "Please pick a contact from the `/parse target:` autocomplete dropdown.",
                ephemeral=is_ephemeral
            )
            return

        if channel_id in active_parse_jobs:
            job = active_parse_jobs[channel_id]
            await interaction.followup.send(
                f"⏳ **Parsing is already in progress for `@{job.channel_name}`!**\n\n"
                f"• **Current Stage:** {job.stage_detail}\n"
                f"• **Elapsed Time:** {round((datetime.datetime.now() - job.started_at).total_seconds(), 1)}s\n\n"
                "💡 *Run `/status` to monitor live progress.*",
                ephemeral=is_ephemeral
            )
            return

        job = ParseJob(
            channel_id=channel_id,
            channel_name=channel_name,
            stage="queued",
            stage_detail="Queued for background export and sectional indexing..."
        )
        active_parse_jobs[channel_id] = job

        asyncio.create_task(run_background_parse_task(job, force_refresh=force_refresh))

        msg = (
            f"## 🚀 Background Parsing Initiated!\n\n"
            f"• **Target Contact / Channel:** `@{channel_name}`\n"
            f"• **Channel ID:** `{channel_id}`\n"
            f"• **Mode:** Sectional Export (5,000 msgs/batch) & SQLite Indexing\n"
            f"• **Force Refresh:** `{'Yes (re-downloading from Discord)' if force_refresh else 'No (using local cache if available)'}`\n\n"
            f"⚡ *This runs completely in the background so your Discord won't freeze. "
            f"Type `/status` anytime to view real-time parsing progress, statistics, and database health!*"
        )
        await interaction.followup.send(msg, ephemeral=is_ephemeral)

    except Exception as e:
        print(f"❌ /parse command error: {e}")
        await interaction.followup.send(f"❌ /parse error: {str(e)}", ephemeral=is_ephemeral)


# 8. /status Slash Command (Live Progress, Universes, & DB Statistics)
@bot.tree.command(name="status", description="Displays live parsing progress, universe breakdown, and SQLite archive statistics")
@app_commands.user_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def check_status(interaction: discord.Interaction):
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        db_stats = get_db_stats()
        uni_summary = get_all_universes_summary()
        active_uni = get_user_active_universe(interaction.user.id)

        lines = ["# 📊 Violet Archive & Parsing Status Dashboard\n"]

        # 1. Active Background Jobs
        if active_parse_jobs:
            lines.append("### 🔄 Active Parsing Tasks in Progress")
            for cid, job in active_parse_jobs.items():
                elapsed = round((datetime.datetime.now() - job.started_at).total_seconds(), 1)
                pct = ""
                if job.total_messages > 0:
                    percent = int((job.indexed_messages / job.total_messages) * 100)
                    bars = int(percent / 10)
                    pct = f" `[{'█' * bars}{'░' * (10 - bars)}] {percent}%`"
                lines.append(
                    f"• **`@{job.channel_name}`** (`{cid}`)\n"
                    f"  - **Stage:** `{job.stage}` — {job.stage_detail}{pct}\n"
                    f"  - **Progress:** {job.indexed_messages:,} / {job.total_messages:,} msgs ({job.sections_done}/{job.sections_total} sections)\n"
                    f"  - **Elapsed:** {elapsed}s"
                )
            lines.append("")
        else:
            lines.append("### 🔄 Active Parsing Tasks\n*No parsing jobs currently running in background.*\n")

        # 2. Database & System Health
        lines.append("### 💾 Local SQLite Database (`violet_archive.db`)")
        lines.append(f"• **Total Indexed Messages:** **{db_stats['total_messages']:,}** messages")
        lines.append(f"• **Total Parsed Channels / Contacts:** **{db_stats['total_channels']:,}**")
        lines.append(f"• **Cached Topic Summaries:** **{db_stats['total_topics']:,}**")
        lines.append(f"• **Database Size on Disk:** **{db_stats['db_size_mb']} MB**")
        lines.append("")

        # 3. Roleplay Universes
        if uni_summary:
            lines.append("### 🌌 Roleplay Universes")
            if active_uni:
                lines.append(f"• **Active DM Universe Focus:** **`{active_uni}`**")
            else:
                lines.append("• **Active DM Universe Focus:** `Global Archive (All Universes)`")
            for u_name, data in uni_summary.items():
                lines.append(f"• **`{u_name}`**: {len(data['channels'])} channel(s), **{data['total_messages']:,} msgs**")
            lines.append("")

        # 4. Top Indexed Channels
        if db_stats["channels"]:
            lines.append("### 👥 Top Indexed Channels & DMs")
            for ch in db_stats["channels"][:5]:
                t_range = f"{ch['first_ts']} to {ch['last_ts']}" if ch['first_ts'] else "N/A"
                lines.append(f"• **`@{ch['name']}`**: **{ch['total_messages']:,} msgs** ({t_range})")
            lines.append("")

        # 5. AI Model Routing & Quotas
        model_pool_status = get_model_pool_status()
        if model_pool_status:
            lines.append("### 🧠 AI Model Routing & Dynamic Fallbacks")
            for m_info in model_pool_status:
                lines.append(f"• {m_info}")
            lines.append("")

        # 6. Saved Lore Overrides
        overrides = get_lore_overrides(None)
        if overrides:
            lines.append(f"### 📚 Saved Lore Overrides ({len(overrides)} total)")
            for corr, top, ts, u_name in overrides[-3:]:
                u_tag = f" [{u_name}]" if u_name else ""
                lines.append(f"• *\"{corr[:60]}...\"*{u_tag}")
            lines.append("")

        lines.append("---\n💡 *Use `/ask` to search, `/universe` to manage universes, or `/lore` to see all confirmed facts!*")

        status_text = "\n".join(lines)
        await send_ephemeral_chunks(interaction, status_text, filename_slug="violet_status", is_ephemeral=is_ephemeral)

    except Exception as e:
        print(f"❌ /status error: {e}")
        await interaction.followup.send(f"❌ Failed to retrieve status: {str(e)}", ephemeral=is_ephemeral)


# 9. /history Slash Command (Last 10 Stored Queries for Channel / Target)
@bot.tree.command(name="history", description="Displays the last 10 search queries for this channel, universe, or target")
@app_commands.user_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    target="Optional: Select a specific DM contact to view query history for"
)
@app_commands.autocomplete(target=target_autocomplete)
async def view_history(interaction: discord.Interaction, target: str = None):
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        channel_id, channel_name = resolve_target(target, interaction.channel_id)
        queries = get_recent_queries(channel_id=channel_id, limit=10)

        if not queries:
            queries = get_recent_queries(channel_id=None, limit=10)
            channel_name = "Recent Queries"

        if not queries:
            await interaction.followup.send(
                f"ℹ️ **No recent queries recorded yet for `@{channel_name}`.**\n\nRun `/ask query: [topic]` to begin searching!",
                ephemeral=is_ephemeral
            )
            return

        lines = [f"## 🕒 Stored Query History for `@{channel_name}` (Last {len(queries)})\n"]
        for idx, (q, t_name, ts) in enumerate(queries, 1):
            ts_clean = (ts or "")[:16].replace("T", " ")
            lines.append(f"**{idx}.** `{q}`\n   ↳ *Target: @{t_name} • {ts_clean}*")

        lines.append("\n---\n💡 *Click on `/ask` and focus on the `query` field to choose any recent question directly from autocomplete!*")
        await send_ephemeral_chunks(interaction, "\n".join(lines), filename_slug="violet_history", is_ephemeral=is_ephemeral)
    except Exception as e:
        print(f"❌ /history error: {e}")
        await interaction.followup.send(f"❌ Error retrieving history: {str(e)}", ephemeral=is_ephemeral)


# 10. /correct Slash Command (Add Confirmed Lore Override with Image & Universe Support)
@bot.tree.command(name="correct", description="Save a confirmed lore fact, visual trait, or fix an inconsistency in Violet's dataset")
@app_commands.user_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    correction="The exact lore detail or correction (e.g. 'Meadowsong's pelt is tortoiseshell with white paws')",
    universe="Optional: Select which Universe this lore belongs to",
    image="Optional: Reference art, character sheet, or visual proof for this correction",
    image_2="Optional: Second reference image / screenshot",
    image_3="Optional: Third reference image / screenshot",
    image_4="Optional: Fourth reference image / screenshot",
    image_5="Optional: Fifth reference image / screenshot",
    target="Optional: Select a specific DM contact this lore belongs to (defaults to current chat)"
)
@app_commands.autocomplete(universe=universe_autocomplete, target=target_autocomplete)
async def correct_lore(
    interaction: discord.Interaction,
    correction: str,
    universe: str = None,
    image: discord.Attachment = None,
    image_2: discord.Attachment = None,
    image_3: discord.Attachment = None,
    image_4: discord.Attachment = None,
    image_5: discord.Attachment = None,
    target: str = None
):
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        channel_id, channel_name = resolve_target(target, interaction.channel_id)
        if not channel_id:
            channel_id = str(interaction.channel_id) if interaction.channel_id else ""
            channel_name = "Global"

        uni_name = universe.strip() if universe and universe.lower() not in ("all", "global", "all universes") else ""
        if not uni_name and channel_id:
            uni_name = get_channel_universe(channel_id) or ""

        # Process any attached images
        all_attachments = [img for img in [image, image_2, image_3, image_4, image_5] if img is not None]
        image_parts = []
        for idx, img in enumerate(all_attachments, 1):
            if img.content_type and img.content_type.startswith("image/"):
                try:
                    img_bytes = await img.read()
                    image_parts.append(types.Part.from_bytes(data=img_bytes, mime_type=img.content_type))
                except Exception as e:
                    print(f"⚠️ Could not load image attachment #{idx} in /correct: {e}")

        final_lore_entry = correction.strip()
        visual_analysis_text = ""

        if image_parts:
            prompt = (
                f"### USER LORE CORRECTION STATEMENT:\n"
                f"{correction}\n\n"
                f"### TASK:\n"
                f"Analyze the {len(image_parts)} uploaded reference image(s) alongside the user's correction statement.\n"
                f"Extract and describe key visual features, markings, colors, and confirmed details shown in the images that support this correction.\n"
                f"Format as a concise, authoritative canon lore summary."
            )
            system_instruction = build_system_instruction(channel_id=channel_id, universe_name=uni_name)
            contents = image_parts + [prompt]
            
            visual_analysis_text = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: generate_gemini_content_sync(contents, system_instruction, temperature=0.1)
            )
            if visual_analysis_text:
                final_lore_entry = f"{correction.strip()} [Visual Ref: {visual_analysis_text[:400]}...]"

        save_lore_override(channel_id, channel_name, "User Correction", final_lore_entry, universe_name=uni_name)

        uni_tag = f"\n• **Universe Tag:** `{uni_name}`" if uni_name else ""
        img_tag = f"\n• **Multimodal References:** {len(image_parts)} image(s) processed & analyzed" if image_parts else ""

        response_msg = (
            f"## 💾 Confirmed Lore Fact Saved!\n\n"
            f"• **Contact / Channel:** `@{channel_name}`{uni_tag}{img_tag}\n"
            f"• **Correction / Override Added:**\n"
            f"  > *\"{correction}\"*\n\n"
        )
        if visual_analysis_text:
            response_msg += f"### 🎨 Visual Analysis & Ground Truth:\n{visual_analysis_text}\n\n"

        response_msg += "⚡ *This fact is now permanently stored in `violet_archive.db` and will be treated as authoritative ground truth for all future searches and analyses!*"

        slug = f"violet_correct_{re.sub(r'[^a-zA-Z0-9_-]', '_', correction)[:20]}"
        await send_ephemeral_chunks(
            interaction,
            response_msg,
            filename_slug=slug,
            channel_id=channel_id,
            channel_label=channel_name,
            query=correction,
            response_text=response_msg,
            is_ephemeral=is_ephemeral
        )

    except Exception as e:
        print(f"❌ /correct error: {e}")
        await interaction.followup.send(f"❌ Failed to save correction: {str(e)}", ephemeral=is_ephemeral)


# 11. /lore Slash Command (View Stored Lore Overrides by Universe or Channel)
@bot.tree.command(name="lore", description="View all saved lore corrections and custom knowledge overrides")
@app_commands.user_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    universe="Optional: Filter lore by a specific Universe",
    target="Optional: Select a specific DM contact to view lore for"
)
@app_commands.autocomplete(universe=universe_autocomplete, target=target_autocomplete)
async def view_lore(interaction: discord.Interaction, universe: str = None, target: str = None):
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        channel_id, channel_name = resolve_target(target, interaction.channel_id) if target else (None, None)
        uni_filter = universe.strip() if universe and universe.lower() not in ("all", "global", "all universes") else None

        overrides = get_lore_overrides(channel_id=channel_id, universe_name=uni_filter)

        scope_label = f"Universe: '{uni_filter}'" if uni_filter else (f"@{channel_name}" if channel_name else "All Universes & Channels")

        if not overrides:
            await interaction.followup.send(
                f"ℹ️ **No custom lore overrides saved yet for `{scope_label}`.**\n\n"
                "You can add one anytime using `/correct correction: [fact]` or by clicking **`✏️ Fix Lore`** on any Violet report!",
                ephemeral=is_ephemeral
            )
            return

        lines = [f"## 📚 Confirmed Lore Overrides for `{scope_label}` ({len(overrides)} saved)\n"]
        for idx, (corr, topic, ts, u_name) in enumerate(overrides, 1):
            ts_clean = (ts or "")[:10]
            u_tag = f" • *Universe: {u_name}*" if u_name else ""
            lines.append(f"**{idx}.** {corr}\n   ↳ *Recorded on {ts_clean}{u_tag}*")

        lines.append("\n---\n💡 *Violet automatically includes all these confirmed facts in every future search.*")
        await send_ephemeral_chunks(interaction, "\n".join(lines), filename_slug="violet_lore", is_ephemeral=is_ephemeral)
    except Exception as e:
        print(f"❌ /lore error: {e}")
        await interaction.followup.send(f"❌ Error viewing lore: {str(e)}", ephemeral=is_ephemeral)


# 12. /ask Slash Command (Cross-Archive, Universe-Aware Semantic Search)
@bot.tree.command(name="ask", description="Deep forensic search across all past messages in your DMs, channels, or Universes")
@app_commands.user_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    query="What specific insight, character, link, quote, or details are you searching for?",
    universe="Optional: Select a specific Universe to search across (defaults to active universe or global)",
    target="Optional: Select a specific DM contact or channel to search (overrides universe filter)",
    image="Optional: Primary image or reference art to analyze alongside query",
    image_2="Optional: Second reference image / angle",
    image_3="Optional: Third reference image / angle"
)
@app_commands.autocomplete(query=query_autocomplete, universe=universe_autocomplete, target=target_autocomplete)
async def ask_anywhere(
    interaction: discord.Interaction,
    query: str,
    universe: str = None,
    target: str = None,
    image: discord.Attachment = None,
    image_2: discord.Attachment = None,
    image_3: discord.Attachment = None
):
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        channel_id, channel_name = resolve_target(target, None) if target else (None, None)
        uni_filter = universe.strip() if (universe and universe.lower() not in ("all", "global", "all universes")) else None

        # If in DMs and no universe/target passed, check user's active universe
        if not channel_id and not uni_filter and interaction.guild is None:
            user_act = get_user_active_universe(interaction.user.id)
            if user_act and user_act.lower() not in ("all", "global", "all universes"):
                uni_filter = user_act

        attached_imgs = [img for img in [image, image_2, image_3] if img is not None]
        print(f"\n📩 /ask received: query='{query}' | universe='{uni_filter}' | target={target} | images={len(attached_imgs)}")

        # Search across the archive (channel, universe, or global)
        formatted_lines, matched_count, total_count, scope_desc = search_archive_messages(
            query=query,
            channel_id=channel_id,
            universe_name=uni_filter,
            max_messages=4000
        )

        if not formatted_lines and not attached_imgs:
            await interaction.followup.send(
                f"❌ **Could not find any indexed messages for `{scope_desc}`.**\n\n"
                "Please make sure either:\n"
                "1. You run `/parse` on your DMs or servers to index your chats.\n"
                "2. Or choose another universe/target.",
                ephemeral=is_ephemeral
            )
            return

        formatted_history = "\n".join(formatted_lines)
        system_instruction = build_system_instruction(channel_id=channel_id, universe_name=uni_filter)

        prompt = (
            f"### DISCORD ARCHIVE CONTEXT ({scope_desc} - {total_count:,} total messages indexed):\n"
            f"{formatted_history}\n\n"
            f"### SEARCH INQUIRY:\n"
            f"{query}"
        )

        # Prepare multimodal contents if images attached
        contents: Any = prompt
        image_parts = []
        for img in attached_imgs:
            if img.content_type and img.content_type.startswith("image/"):
                try:
                    img_bytes = await img.read()
                    image_parts.append(types.Part.from_bytes(data=img_bytes, mime_type=img.content_type))
                except Exception as e:
                    print(f"⚠️ Could not load image bytes: {e}")

        if image_parts:
            contents = image_parts + [prompt]
            print(f"🖼️ Attached {len(image_parts)} multimodal image(s) to query '{query[:40]}...'")

        print(f"🤖 Querying Gemini ({GEMINI_MODEL}) over {len(formatted_lines)} formatted lines ({scope_desc})...")

        final_answer = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: generate_gemini_content_sync(contents, system_instruction, temperature=0.1)
        )
        if not final_answer:
            final_answer = "No response generated."

        save_query_history(channel_id or "global", scope_desc, query, final_answer)

        slug = f"violet_report_{re.sub(r'[^a-zA-Z0-9_-]', '_', query)[:20]}"
        await send_ephemeral_chunks(
            interaction,
            final_answer,
            filename_slug=slug,
            channel_id=channel_id or "",
            channel_label=scope_desc,
            query=query,
            response_text=final_answer,
            is_ephemeral=is_ephemeral
        )
        print("✅ Response delivered to user.")

    except Exception as e:
        print(f"❌ Pipeline error: {e}")
        await interaction.followup.send(
            f"❌ Execution Failure: Exception thrown during processing pipeline: {str(e)}",
            ephemeral=is_ephemeral
        )


# 13. /character Slash Command (Multimodal Visual Appearance & Multi-Universe Registration)
@bot.tree.command(name="character", description="Upload multiple character art/images to analyze and register visual appearance by Universe")
@app_commands.user_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(
    name="Character's name (e.g. Meadowsong, Umbrapaw)",
    image="Primary reference drawing, character art, or ref sheet",
    description="Short description or context about what the image(s) depict (e.g. 'Meadowsong in Universe 1 with her scar')",
    universe="Optional: Universe this character appearance belongs to",
    image_2="Optional: Second reference image / angle / scene",
    image_3="Optional: Third reference image / angle / scene",
    image_4="Optional: Fourth reference image / angle / scene",
    image_5="Optional: Fifth reference image / angle / scene",
    target="Optional: Select a specific DM contact to link lore with (defaults to active universe or global)",
    save_to_lore="Save the extracted visual profile permanently as canon lore (Default: True)"
)
@app_commands.autocomplete(universe=universe_autocomplete, target=target_autocomplete)
async def character_command(
    interaction: discord.Interaction,
    name: str,
    image: discord.Attachment,
    description: str = "",
    universe: str = None,
    image_2: discord.Attachment = None,
    image_3: discord.Attachment = None,
    image_4: discord.Attachment = None,
    image_5: discord.Attachment = None,
    target: str = None,
    save_to_lore: bool = True
):
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        channel_id, channel_name = resolve_target(target, None) if target else (None, None)
        uni_filter = universe.strip() if (universe and universe.lower() not in ("all", "global", "all universes")) else None

        if not channel_id and not uni_filter and interaction.guild is None:
            user_act = get_user_active_universe(interaction.user.id)
            if user_act and user_act.lower() not in ("all", "global", "all universes"):
                uni_filter = user_act

        all_attachments = [img for img in [image, image_2, image_3, image_4, image_5] if img is not None]
        image_parts = []
        for idx, img in enumerate(all_attachments, 1):
            if img.content_type and img.content_type.startswith("image/"):
                try:
                    img_bytes = await img.read()
                    image_parts.append(types.Part.from_bytes(data=img_bytes, mime_type=img.content_type))
                except Exception as e:
                    print(f"⚠️ Could not load image attachment #{idx}: {e}")

        if not image_parts:
            await interaction.followup.send(
                "❌ **Invalid file type:** Please upload at least one valid image file (PNG, JPG, JPEG, WEBP, GIF).",
                ephemeral=is_ephemeral
            )
            return

        search_query = f"{name} appearance fur eyes colors scars markings {description}".strip()
        formatted_lines, matched_count, total_count, scope_desc = search_archive_messages(
            query=search_query,
            channel_id=channel_id,
            universe_name=uni_filter,
            max_messages=2500
        )
        formatted_history = "\n".join(formatted_lines) if formatted_lines else "No prior chat messages found for this character in archive."

        system_instruction = build_system_instruction(channel_id=channel_id, universe_name=uni_filter)
        user_notes = f"User Notes / Depiction Context: \"{description}\"" if description else "No extra notes provided."

        prompt = (
            f"### CHARACTER NAME:\n"
            f"{name}\n\n"
            f"### USER CONTEXT & NOTES:\n"
            f"{user_notes}\n\n"
            f"### DISCORD ARCHIVE CONTEXT ({scope_desc}):\n"
            f"{formatted_history}\n\n"
            f"### TASK:\n"
            f"Analyze the {len(image_parts)} uploaded reference image(s) of '{name}' and create an authoritative, unified Visual Character Dossier for this Universe/AU.\n\n"
            f"Format your response with the following sections:\n"
            f"# 🎨 Unified Visual Dossier: {name}\n"
            f"**Depiction Context:** {description if description else 'Standard Reference'} *(Synthesizing {len(image_parts)} uploaded reference image(s) | Scope: {scope_desc})*\n\n"
            f"### 🐱 1. Physical Build & Frame\n"
            f"• Body frame, size, posture, ear shape, tail length/fur type across angles.\n\n"
            f"### 🎨 2. Coat Color & Markings\n"
            f"• Base coloration, patterns (tabby, calico, points, tortoiseshell), underbelly/chest/paw markings, facial masks.\n\n"
            f"### 👀 3. Eyes & Facial Features\n"
            f"• Eye coloration, pupil shape, whisker length, nose/pad color.\n\n"
            f"### ⚔️ 4. Distinctive Scars, Marks & Accessories\n"
            f"• Battle scars, notched/torn ears, collars, feathers, unique visual identifiers shown in the drawings.\n\n"
            f"### 📜 5. Chat Lore & Roleplay Reconciliation\n"
            f"• How these visual traits align with, confirm, or enrich details in the chat archive for this universe.\n\n"
            f"### 🌟 6. Canonical Appearance Summary\n"
            f"• Write a concise 2-sentence visual description summarizing {name}'s definitive physical appearance across all reference images."
        )

        contents = image_parts + [prompt]
        print(f"🎨 Analyzing {len(image_parts)} image(s) for '{name}' with Gemini vision across {len(formatted_lines)} archive lines ({scope_desc})...")

        final_answer = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: generate_gemini_content_sync(contents, system_instruction, temperature=0.1)
        )
        if not final_answer:
            final_answer = f"No visual analysis could be generated for {name}."

        if save_to_lore:
            lore_entry = f"Visual Appearance of {name}: "
            if "### 🌟 6. Canonical Appearance Summary" in final_answer:
                summary_part = final_answer.split("### 🌟 6. Canonical Appearance Summary")[-1].strip()
                lore_entry += summary_part.replace("•", "").strip()
            else:
                lore_entry += f"Based on {len(image_parts)} visual art reference(s) ('{description}'): {final_answer[:300]}..."

            save_lore_override(channel_id or "", scope_desc, f"Visual: {name}", lore_entry, universe_name=uni_filter or "")
            save_query_history(channel_id or "global", scope_desc, f"[Visual Profile] {name}", final_answer)

        slug = f"violet_visual_{re.sub(r'[^a-zA-Z0-9_-]', '_', name)[:20]}"
        await send_ephemeral_chunks(
            interaction,
            final_answer,
            filename_slug=slug,
            channel_id=channel_id or "",
            channel_label=scope_desc,
            query=f"Visual appearance and traits of {name}",
            response_text=final_answer,
            is_ephemeral=is_ephemeral
        )
        print(f"✅ Visual dossier for '{name}' with {len(image_parts)} image(s) delivered to user.")

    except Exception as e:
        print(f"❌ /character error: {e}")
        await interaction.followup.send(
            f"❌ Failed to analyze character images: {str(e)}",
            ephemeral=is_ephemeral
        )


# 14. Message Context Menu App ("Apps -> Ask Violet")
@bot.tree.context_menu(name="Ask Violet")
@app_commands.user_install()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def ask_message_context(interaction: discord.Interaction, message: discord.Message):
    """Right-click any message in any DM, group chat, or server -> Apps -> Ask Violet."""
    is_ephemeral = await should_be_ephemeral(interaction)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=is_ephemeral)
    except Exception:
        pass

    try:
        author_name = message.author.name
        timestamp = message.created_at.strftime("%Y-%m-%d %H:%M")
        content = message.clean_content or message.content or "[Non-text message / Attachment]"

        reply_info = ""
        if message.reference and message.reference.resolved and isinstance(message.reference.resolved, discord.Message):
            ref_msg = message.reference.resolved
            ref_time = ref_msg.created_at.strftime("%Y-%m-%d %H:%M")
            reply_info = f"Replying to [{ref_time}] {ref_msg.author.name}: {ref_msg.clean_content}\n\n"

        prompt = (
            f"### TARGET MESSAGE TO ANALYZE:\n"
            f"{reply_info}"
            f"[{timestamp}] {author_name}: {content}\n\n"
            f"### TASK:\n"
            f"Provide a clear, helpful breakdown, summary, or actionable response for this message."
        )

        system_instruction = (
            "You are Violet, a helpful AI assistant. Analyze the provided message context, explain any key points, "
            "and suggest relevant replies or insights. Filter out graphic/lewd details."
        )

        final_answer = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: generate_gemini_content_sync(prompt, system_instruction, temperature=0.2)
        )
        if not final_answer:
            final_answer = "No response generated."
        header = f"**Analysis for message from @{author_name} ({timestamp}):**\n\n"
        await send_ephemeral_chunks(interaction, header + final_answer)

    except Exception as e:
        await interaction.followup.send(
            f"❌ Execution Failure: Exception thrown during processing pipeline: {str(e)}",
            ephemeral=is_ephemeral
        )


# 15. Spin up process instance
if __name__ == "__main__":
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        print(f"❌ Fatal bot crash: {e}")
