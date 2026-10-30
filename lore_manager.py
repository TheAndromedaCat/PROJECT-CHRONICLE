"""
Lore Manager & Archivist for AskViolet
======================================
Allows flushing temporary DM conversations to permanent Markdown files and SQLite,
manually adding new lore (without DCE), and auto-syncing Markdown lore files
into Violet's ground-truth database.
"""

import os
import sys
import re
import json
import sqlite3
import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

# Set UTF-8 encoding for console output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "violet_archive.db"
LORE_DIR = BASE_DIR / "lore"
MANUAL_LORE_CHANNEL_ID = "ch_lore_manual"
MANUAL_LORE_CHANNEL_NAME = "📜 Manual Lore Archives"


def init_lore_infrastructure():
    """Ensures database tables and directories exist."""
    LORE_DIR.mkdir(exist_ok=True)
    if not DB_PATH.exists():
        print(f"⚠️ Warning: {DB_PATH.name} not found in {BASE_DIR}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Ensure channel exists for manual lore in channels table
    cursor.execute("""
        INSERT OR IGNORE INTO channels (channel_id, name, total_messages, first_timestamp, last_timestamp)
        VALUES (?, ?, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (MANUAL_LORE_CHANNEL_ID, MANUAL_LORE_CHANNEL_NAME))
    
    # Ensure channel_universes has an entry for the manual lore channel
    cursor.execute("""
        INSERT OR IGNORE INTO channel_universes (channel_id, universe_name, updated_at)
        VALUES (?, 'Main Universe', CURRENT_TIMESTAMP)
    """, (MANUAL_LORE_CHANNEL_ID,))

    conn.commit()
    conn.close()


def sanitize_filename(name: str) -> str:
    """Sanitizes a string to be a safe filesystem filename."""
    s = re.sub(r'[^a-zA-Z0-9_\- ]+', '_', name).strip()
    return re.sub(r'\s+', '_', s)[:60] or "lore_entry"


def flush_history_to_lore_files(
    start_date: Optional[str] = None,
    universe_name: str = "Main Universe",
    output_subfolder: Optional[str] = None
) -> Dict[str, Any]:
    """
    Scans query_history table (where 1-on-1 DM interactions are stored)
    and flushes them into clean Markdown lore files and SQLite lore_overrides.
    """
    if not DB_PATH.exists():
        print(f"❌ Database {DB_PATH} not found.")
        return {"count": 0, "files": []}

    init_lore_infrastructure()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if start_date:
        query = "SELECT id, channel_id, target_name, query, response, timestamp FROM query_history WHERE timestamp >= ? ORDER BY id ASC"
        params = (start_date,)
    else:
        # Default: all entries with [DM] or recent entries
        query = "SELECT id, channel_id, target_name, query, response, timestamp FROM query_history WHERE query LIKE '[DM]%' ORDER BY id ASC"
        params = ()

    cursor.execute(query, params)
    rows = cursor.fetchall()

    if not rows:
        print("ℹ️ No matching query history records found to flush.")
        conn.close()
        return {"count": 0, "files": []}

    uni_dir = LORE_DIR / (output_subfolder or sanitize_filename(universe_name))
    uni_dir.mkdir(parents=True, exist_ok=True)

    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    dossier_filename = uni_dir / f"flushed_lore_{today_str}.md"

    # Assemble comprehensive Markdown dossier
    doc_lines = [
        f"# 📜 Canonical Lore Dossier: {universe_name}",
        f"*Flushed from private DM session on {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        "",
        "---",
        ""
    ]

    inserted_overrides = 0
    inserted_messages = 0

    for r in rows:
        rec_id, ch_id, target_name, user_query, bot_response, ts = r
        clean_user = user_query.replace("[DM] ", "") if user_query.startswith("[DM] ") else user_query
        
        # Add to markdown
        doc_lines.append(f"### 💬 Session Entry #{rec_id} ({ts})")
        doc_lines.append(f"**Archivist / User Directive:**")
        doc_lines.append(f"> {clean_user}")
        doc_lines.append("")
        doc_lines.append(f"**Synthesized Lore Details:**")
        doc_lines.append(bot_response or "*No response recorded.*")
        doc_lines.append("")
        doc_lines.append("---")
        doc_lines.append("")

        # Save meaningful user contributions as lore_overrides
        if len(clean_user) > 30 and not clean_user.lower().startswith("yep") and not clean_user.lower().startswith("if you are able"):
            # Derive topic key from first 6 words
            first_words = " ".join(clean_user.split()[:5])
            cursor.execute("""
                INSERT INTO lore_overrides (channel_id, target_name, topic_key, correction, universe_name, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                MANUAL_LORE_CHANNEL_ID,
                "Violet DM Archivist",
                f"Lore: {first_words}",
                clean_user,
                universe_name,
                ts
            ))
            inserted_overrides += 1

        # Also store into messages table so search_archive_messages finds it
        msg_id_user = f"lore_user_{rec_id}"
        msg_id_bot = f"lore_bot_{rec_id}"
        cursor.execute("""
            INSERT OR REPLACE INTO messages (id, channel_id, timestamp, author, content)
            VALUES (?, ?, ?, ?, ?)
        """, (msg_id_user, MANUAL_LORE_CHANNEL_ID, ts, "Archivist", clean_user))
        
        if bot_response:
            cursor.execute("""
                INSERT OR REPLACE INTO messages (id, channel_id, timestamp, author, content)
                VALUES (?, ?, ?, ?, ?)
            """, (msg_id_bot, MANUAL_LORE_CHANNEL_ID, ts, "Violet (Master Lorekeeper)", bot_response))
        
        inserted_messages += 2

    # Update channel message count
    cursor.execute("""
        UPDATE channels 
        SET total_messages = (SELECT COUNT(*) FROM messages WHERE channel_id = ?),
            last_timestamp = CURRENT_TIMESTAMP
        WHERE channel_id = ?
    """, (MANUAL_LORE_CHANNEL_ID, MANUAL_LORE_CHANNEL_ID))

    conn.commit()
    conn.close()

    # Write Markdown file
    with open(dossier_filename, "w", encoding="utf-8") as f:
        f.write("\n".join(doc_lines))

    print(f"✅ Successfully flushed {len(rows)} dialogue records!")
    print(f"📁 Markdown Dossier created at: {dossier_filename.resolve()}")
    print(f"💾 Inserted {inserted_overrides} ground-truth facts into lore_overrides.")
    print(f"🔍 Indexed {inserted_messages} messages into SQLite messages archive under '{MANUAL_LORE_CHANNEL_NAME}'.")

    return {
        "count": len(rows),
        "dossier_path": str(dossier_filename.resolve()),
        "inserted_overrides": inserted_overrides,
        "inserted_messages": inserted_messages
    }


def add_lore_entry(
    universe_name: str,
    topic_title: str,
    content: str,
    author: str = "Archivist"
) -> str:
    """
    Saves a discrete piece of lore BOTH to a Markdown file in lore/<Universe>/
    AND directly into the SQLite database (lore_overrides and messages).
    """
    init_lore_infrastructure()
    clean_uni = universe_name.strip() or "Main Universe"
    clean_topic = topic_title.strip() or "General Lore"
    clean_content = content.strip()

    if not clean_content:
        raise ValueError("Content cannot be empty.")

    uni_dir = LORE_DIR / sanitize_filename(clean_uni)
    uni_dir.mkdir(parents=True, exist_ok=True)
    file_path = uni_dir / f"{sanitize_filename(clean_topic)}.md"

    # 1. Write or append to Markdown file
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    md_content = f"# {clean_topic}\n"
    md_content += f"**Universe:** {clean_uni}  \n"
    md_content += f"**Author:** {author}  \n"
    md_content += f"**Recorded:** {ts}  \n\n"
    md_content += "---\n\n"
    md_content += clean_content + "\n"

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    # 2. Insert into SQLite
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Lore override for highest prompt priority
    cursor.execute("""
        INSERT INTO lore_overrides (channel_id, target_name, topic_key, correction, universe_name, timestamp)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (MANUAL_LORE_CHANNEL_ID, author, clean_topic, clean_content, clean_uni))

    # Message table for forensic keyword searches
    gen_id = f"lore_manual_{int(datetime.datetime.now().timestamp() * 1000)}"
    cursor.execute("""
        INSERT INTO messages (id, channel_id, timestamp, author, content)
        VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
    """, (gen_id, MANUAL_LORE_CHANNEL_ID, author, f"[{clean_topic}] {clean_content}"))

    # Update channel message count
    cursor.execute("""
        UPDATE channels 
        SET total_messages = (SELECT COUNT(*) FROM messages WHERE channel_id = ?),
            last_timestamp = CURRENT_TIMESTAMP
        WHERE channel_id = ?
    """, (MANUAL_LORE_CHANNEL_ID, MANUAL_LORE_CHANNEL_ID))

    # Link manual lore channel to universe if not yet linked
    cursor.execute("""
        INSERT OR REPLACE INTO channel_universes (channel_id, universe_name, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
    """, (MANUAL_LORE_CHANNEL_ID, clean_uni))

    conn.commit()
    conn.close()

    print(f"✅ Saved lore entry: '{clean_topic}' (Universe: '{clean_uni}')")
    print(f"📄 File: {file_path.resolve()}")
    return str(file_path.resolve())


def sync_all_lore_files(directory: Optional[Path] = None) -> int:
    """
    Scans the lore/ folder for all .md and .txt files and syncs them into SQLite.
    Subfolder names indicate Universe (e.g., lore/Main_Universe/file.md -> Main Universe).
    """
    scan_dir = directory or LORE_DIR
    if not scan_dir.exists():
        print(f"ℹ️ Lore directory {scan_dir} does not exist.")
        return 0

    init_lore_infrastructure()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    synced_count = 0
    for root, _, files in os.walk(scan_dir):
        root_path = Path(root)
        rel = root_path.relative_to(scan_dir)
        uni_name = rel.parts[0].replace("_", " ") if rel.parts else "Main Universe"

        for file in files:
            if not file.endswith((".md", ".txt")):
                continue
            file_path = root_path / file
            topic = file_path.stem.replace("_", " ")

            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read().strip()
                if not content:
                    continue

                # Add to lore_overrides
                cursor.execute("""
                    INSERT OR REPLACE INTO lore_overrides (channel_id, target_name, topic_key, correction, universe_name, timestamp)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (MANUAL_LORE_CHANNEL_ID, "File Sync", topic, content, uni_name))

                # Add to messages
                file_msg_id = f"file_{sanitize_filename(uni_name)}_{sanitize_filename(topic)}"
                cursor.execute("""
                    INSERT OR REPLACE INTO messages (id, channel_id, timestamp, author, content)
                    VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)
                """, (file_msg_id, MANUAL_LORE_CHANNEL_ID, f"Codex ({uni_name})", f"# {topic}\n{content}"))

                synced_count += 1
            except Exception as e:
                print(f"⚠️ Error syncing {file_path.name}: {e}")

    # Update channel message count
    cursor.execute("""
        UPDATE channels 
        SET total_messages = (SELECT COUNT(*) FROM messages WHERE channel_id = ?),
            last_timestamp = CURRENT_TIMESTAMP
        WHERE channel_id = ?
    """, (MANUAL_LORE_CHANNEL_ID, MANUAL_LORE_CHANNEL_ID))

    conn.commit()
    conn.close()
    print(f"✨ Synced {synced_count} lore file(s) into database archive.")
    return synced_count


def list_existing_universes() -> List[str]:
    """Returns list of distinct universe names in the archive."""
    if not DB_PATH.exists():
        return ["Main Universe"]
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT universe_name FROM channel_universes WHERE universe_name != '' ORDER BY universe_name ASC")
    universes = [r[0] for r in cursor.fetchall() if r[0]]
    conn.close()
    if "Main Universe" not in universes:
        universes.insert(0, "Main Universe")
    return universes


def interactive_menu():
    """Interactive command-line interface."""
    init_lore_infrastructure()
    while True:
        print("\n" + "=" * 55)
        print("  📚 AskViolet Lore & Archive Manager")
        print("=" * 55)
        print("1. ⚡ Flush recent DM conversations into Markdown & SQLite")
        print("2. ✍️  Add a new Lore entry manually (with Universe tag)")
        print("3. 🔄 Sync all Markdown files in 'lore/' folder into SQLite")
        print("4. 📋 List saved lore overrides and universes")
        print("5. 🚪 Exit")
        print("-" * 55)

        choice = input("Select an option (1-5): ").strip()

        if choice == "1":
            print("\n--- Flush History to Lore Dossier ---")
            universes = list_existing_universes()
            print("Available Universes:", ", ".join(universes))
            uni = input("Target Universe (default 'Main Universe'): ").strip() or "Main Universe"
            date_filter = input("Start date filter (YYYY-MM-DD or press Enter for today): ").strip()
            if not date_filter:
                date_filter = datetime.datetime.now().strftime("%Y-%m-%d")
            flush_history_to_lore_files(start_date=date_filter, universe_name=uni)

        elif choice == "2":
            print("\n--- Add New Lore Entry ---")
            universes = list_existing_universes()
            print("Existing Universes:", ", ".join(universes))
            uni = input("Universe (default 'Main Universe'): ").strip() or "Main Universe"
            topic = input("Topic / Character / Location Title: ").strip()
            if not topic:
                print("❌ Topic cannot be empty.")
                continue

            print("Enter lore text (enter a line with 'EOF' or press Ctrl+Z/Enter when finished):")
            lines = []
            while True:
                try:
                    line = input()
                    if line.strip() == "EOF":
                        break
                    lines.append(line)
                except EOFError:
                    break
            content = "\n".join(lines).strip()
            if not content:
                print("❌ No content entered.")
                continue

            add_lore_entry(universe_name=uni, topic_title=topic, content=content)

        elif choice == "3":
            print("\n--- Syncing Lore Directory ---")
            sync_all_lore_files()

        elif choice == "4":
            print("\n--- Current Lore Overrides in Database ---")
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id, topic_key, universe_name, timestamp, correction FROM lore_overrides ORDER BY id DESC LIMIT 20")
            rows = cursor.fetchall()
            conn.close()
            if not rows:
                print("No lore overrides currently saved.")
            else:
                for r in rows:
                    print(f"• [ID {r[0]}] ({r[2] or 'Global'} | {r[3][:10]}): {r[1]}")
                    print(f"  Snippet: {r[4][:120]}...\n")

        elif choice in ("5", "q", "exit"):
            print("👋 Exiting Lore Manager.")
            break
        else:
            print("Invalid selection. Please choose 1 to 5.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AskViolet Lore Manager")
    parser.add_argument("--flush", action="store_true", help="Flush recent DM dialogue to markdown & database")
    parser.add_argument("--date", type=str, default=None, help="Start date filter for flush (YYYY-MM-DD)")
    parser.add_argument("--universe", type=str, default="Main Universe", help="Universe name (default: Main Universe)")
    parser.add_argument("--sync", action="store_true", help="Sync all lore/ folder markdown files into SQLite")
    args = parser.parse_args()

    if args.flush:
        flush_history_to_lore_files(start_date=args.date, universe_name=args.universe)
    elif args.sync:
        sync_all_lore_files()
    else:
        interactive_menu()
