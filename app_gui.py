import os
import sys
import subprocess
import threading
import queue
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

# Determine base directory
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    IS_FROZEN = True
else:
    APP_DIR = Path(__file__).resolve().parent
    IS_FROZEN = False

APP_NAME = "AskViolet"
RUN_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
ENV_PATH = APP_DIR / ".env"


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_linux() -> bool:
    return sys.platform.startswith("linux")


# ==========================================
# Autostart Helpers (Windows & Linux)
# ==========================================
def get_autostart_command() -> str:
    """Returns the executable command line for startup."""
    if IS_FROZEN:
        return f'"{sys.executable}"'
    else:
        py_binary = sys.executable
        if is_windows() and py_binary.lower().endswith("python.exe"):
            pyw = py_binary[:-10] + "pythonw.exe"
            if os.path.exists(pyw):
                py_binary = pyw
        script_path = APP_DIR / "app_gui.py"
        return f'"{py_binary}" "{script_path}"'


def is_startup_enabled() -> bool:
    """Checks if the app is registered to run on boot."""
    if is_windows():
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_PATH, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, APP_NAME)
                return bool(val)
        except Exception:
            return False
    elif is_linux():
        desktop_file = Path.home() / ".config" / "autostart" / f"{APP_NAME.lower()}.desktop"
        return desktop_file.exists()
    return False


def set_startup_enabled(enable: bool) -> bool:
    """Enables or disables startup in Windows Registry or Linux autostart."""
    if is_windows():
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
                if enable:
                    cmd = get_autostart_command()
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
            return True
        except Exception as e:
            print(f"Error updating Windows startup: {e}")
            return False
    elif is_linux():
        try:
            autostart_dir = Path.home() / ".config" / "autostart"
            autostart_dir.mkdir(parents=True, exist_ok=True)
            desktop_file = autostart_dir / f"{APP_NAME.lower()}.desktop"
            if enable:
                cmd = get_autostart_command().replace('"', "")
                content = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Exec={cmd}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Comment=AskViolet Discord Bot
"""
                desktop_file.write_text(content, encoding="utf-8")
            else:
                if desktop_file.exists():
                    desktop_file.unlink()
            return True
        except Exception as e:
            print(f"Error updating Linux startup: {e}")
            return False
    return False


# ==========================================
# .env Configuration Parser & Writer
# ==========================================
def get_installed_ollama_models(endpoint: str = "http://127.0.0.1:11435") -> list:
    """Fetches real-time list of installed Ollama models."""
    import urllib.request
    import json
    base = endpoint.rstrip("/").removesuffix("/v1")
    try:
        req = urllib.request.Request(f"{base}/api/tags", headers={"User-Agent": "AskViolet"})
        with urllib.request.urlopen(req, timeout=1.5) as r:
            data = json.loads(r.read().decode())
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        pass
    return [
        "qwen-coder-32k:latest",
        "qwen3.8-16k:latest",
        "qwen-caption-32k:latest",
        "hf.co/bartowski/Qwen2.5-Coder-7B-Instruct-abliterated-GGUF:Q8_0",
        "hf.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF:Q4_K_M",
        "hf.co/mradermacher/Qwen2.5-VL-7B-Abliterated-Caption-it-GGUF:Q8_0"
    ]


def read_env_file() -> dict:
    """Reads .env into a dictionary while preserving default values."""
    cfg = {
        "DISCORD_BOT_TOKEN": "",
        "GEMINI_API_KEY": "",
        "GEMINI_MODEL": "gemini-3.5-flash-lite",
        "OLLAMA_HOST": "127.0.0.1:11435",
        "LOCAL_LLM_ENDPOINT": "http://127.0.0.1:11435/v1",
        "LOCAL_VISION_ENDPOINT": "http://127.0.0.1:11435/v1",
        "LOCAL_CHAT_MODEL": "hf.co/bartowski/Qwen2.5-Coder-7B-Instruct-abliterated-GGUF:Q8_0",
        "LOCAL_DEEP_MODEL": "hf.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF:Q4_K_M",
        "LOCAL_VISION_MODEL": "hf.co/mradermacher/Qwen2.5-VL-7B-Abliterated-Caption-it-GGUF:Q8_0",
    }
    if not ENV_PATH.exists():
        return cfg

    try:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip().strip("\"'")
    except Exception as e:
        print(f"Error loading .env: {e}")
    return cfg


def save_env_file(data: dict) -> bool:
    """Saves updated environment variables into .env."""
    try:
        host = data.get("OLLAMA_HOST", "127.0.0.1:11435").strip() or "127.0.0.1:11435"
        chat_model = data.get("LOCAL_CHAT_MODEL", "").strip() or "hf.co/bartowski/Qwen2.5-Coder-7B-Instruct-abliterated-GGUF:Q8_0"
        deep_model = data.get("LOCAL_DEEP_MODEL", "").strip() or "hf.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF:Q4_K_M"
        vision_model = data.get("LOCAL_VISION_MODEL", "").strip() or "hf.co/mradermacher/Qwen2.5-VL-7B-Abliterated-Caption-it-GGUF:Q8_0"

        lines = [
            "# AskViolet Configuration",
            f"DISCORD_BOT_TOKEN={data.get('DISCORD_BOT_TOKEN', '').strip()}",
            f"GEMINI_API_KEY={data.get('GEMINI_API_KEY', '').strip()}",
            f"GEMINI_MODEL={data.get('GEMINI_MODEL', 'gemini-3.5-flash-lite').strip()}",
            "",
            "# Local Ollama / 3-Tier Hardware Inference (Port 11435)",
            f"OLLAMA_HOST={host}",
            f"LOCAL_LLM_ENDPOINT=http://{host}/v1",
            f"LOCAL_VISION_ENDPOINT=http://{host}/v1",
            f"LOCAL_CHAT_MODEL={chat_model}",
            f"LOCAL_DEEP_MODEL={deep_model}",
            f"LOCAL_VISION_MODEL={vision_model}",
            f"LOCAL_TEXT_MODEL={chat_model}",
            "",
        ]
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return True
    except Exception as e:
        print(f"Error saving .env: {e}")
        return False


# ==========================================
# Main GUI Class using TKinterModernThemes
# ==========================================
import TKinterModernThemes as tkm


class AskVioletThemedApp(tkm.ThemedTKinterFrame):
    def __init__(self):
        super().__init__(
            title="AskViolet - Bot & 3-Tier Local LLM Manager",
            theme="sun-valley",
            mode="dark",
            usecommandlineargs=False,
            useconfigfile=False,
        )

        self.root.geometry("920x680")
        self.root.minsize(760, 520)

        # Process management
        self.bot_process: subprocess.Popen = None
        self.log_queue = queue.Queue()
        self.is_running = False

        # Load environment values
        self.env_data = read_env_file()

        # Build UI layout
        self._build_tabs()

        # Start log consumer
        self.root.after(100, self._process_log_queue)

        # Intercept window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_tabs(self):
        self.notebook = self.Notebook("MainNotebook", row=0, col=0, sticky="nsew", padx=10, pady=10)

        # Tab 1: Live Console & Bot Controls
        self.tab_console = self.notebook.addTab("🎮 Bot Dashboard & Logs")
        self._build_console_tab(self.tab_console)

        # Tab 2: Environment Variables & 3-Tier Model Settings
        self.tab_settings = self.notebook.addTab("⚙️ Environment & 3-Tier AI")
        self._build_settings_tab(self.tab_settings)

    # ----------------------------------------------------
    # Tab 1: Dashboard & Console Logs
    # ----------------------------------------------------
    def _build_console_tab(self, tab):
        frame = tab.master

        # Header controls bar
        ctrl_bar = ttk.Frame(frame)
        ctrl_bar.pack(fill=tk.X, padx=10, pady=(10, 5))

        # Start/Stop Button
        self.btn_toggle = tk.Button(
            ctrl_bar,
            text="▶ Start Bot",
            bg="#10b981",
            fg="#ffffff",
            activebackground="#059669",
            activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=14,
            pady=5,
            cursor="hand2",
            command=self.toggle_bot,
        )
        self.btn_toggle.pack(side=tk.LEFT, padx=(0, 8))

        # Test Ollama Button
        btn_ollama = tk.Button(
            ctrl_bar,
            text="⚡ Test Ollama",
            bg="#2e2844",
            fg="#e6e1f5",
            activebackground="#7c3aed",
            activeforeground="#ffffff",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=10,
            pady=5,
            cursor="hand2",
            command=self.test_ollama,
        )
        btn_ollama.pack(side=tk.LEFT, padx=(0, 8))

        # Lore Manager Button
        btn_lore = tk.Button(
            ctrl_bar,
            text="📖 Lore Manager",
            bg="#2e2844",
            fg="#e6e1f5",
            activebackground="#7c3aed",
            activeforeground="#ffffff",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=10,
            pady=5,
            cursor="hand2",
            command=self.launch_lore_manager,
        )
        btn_lore.pack(side=tk.LEFT, padx=(0, 8))

        # Clear Logs Button
        btn_clear = tk.Button(
            ctrl_bar,
            text="🧹 Clear",
            bg="#2e2844",
            fg="#e6e1f5",
            activebackground="#3f3b54",
            activeforeground="#ffffff",
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            padx=10,
            pady=5,
            cursor="hand2",
            command=self.clear_logs,
        )
        btn_clear.pack(side=tk.LEFT, padx=(0, 8))

        # Status badge on the right
        self.status_badge = tk.Label(
            ctrl_bar,
            text="● STOPPED",
            bg="#ef4444",
            fg="#ffffff",
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=4,
        )
        self.status_badge.pack(side=tk.RIGHT)

        # ScrolledText for Console
        self.log_text = scrolledtext.ScrolledText(
            frame,
            bg="#0d0b14",
            fg="#d1cce5",
            insertbackground="#ffffff",
            font=("Consolas", 9),
            relief=tk.FLAT,
            padx=10,
            pady=8,
            wrap=tk.WORD,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))

        # Console color tags
        self.log_text.tag_config("timestamp", foreground="#7c749c")
        self.log_text.tag_config("info", foreground="#60a5fa")
        self.log_text.tag_config("success", foreground="#34d399")
        self.log_text.tag_config("warning", foreground="#fbbf24")
        self.log_text.tag_config("error", foreground="#f87171")

        self.append_log(f"AskViolet UI Initialized. Root Directory: {APP_DIR}", "info")
        self.append_log("Safe Mode Active (DCE Disabled). 3-Tier AI Routing Configured.", "success")

    # ----------------------------------------------------
    # Tab 2: Settings & Environment Configuration
    # ----------------------------------------------------
    def _build_settings_tab(self, tab):
        frame = tab.master

        canvas = tk.Canvas(frame, bg="#1c182b", highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=10)

        # Form Variables
        self.var_discord_token = tk.StringVar(value=self.env_data.get("DISCORD_BOT_TOKEN", ""))
        self.var_gemini_key = tk.StringVar(value=self.env_data.get("GEMINI_API_KEY", ""))
        self.var_gemini_model = tk.StringVar(value=self.env_data.get("GEMINI_MODEL", "gemini-3.5-flash-lite"))
        self.var_ollama_host = tk.StringVar(value=self.env_data.get("OLLAMA_HOST", "127.0.0.1:11435"))
        self.var_chat_model = tk.StringVar(value=self.env_data.get("LOCAL_CHAT_MODEL", "hf.co/bartowski/Qwen2.5-Coder-7B-Instruct-abliterated-GGUF:Q8_0"))
        self.var_deep_model = tk.StringVar(value=self.env_data.get("LOCAL_DEEP_MODEL", "hf.co/JonathanColetti/Qwen3.8-27B-Uncensored-GGUF:Q4_K_M"))
        self.var_vision_model = tk.StringVar(value=self.env_data.get("LOCAL_VISION_MODEL", "hf.co/mradermacher/Qwen2.5-VL-7B-Abliterated-Caption-it-GGUF:Q8_0"))
        self.var_startup = tk.BooleanVar(value=is_startup_enabled())

        # Fetch installed Ollama models for dropdowns
        ollama_models = get_installed_ollama_models(self.var_ollama_host.get())

        gemini_choices = [
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-3.8-flash",
            "gemini-3.6-flash",
            "gemini-3-flash",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite"
        ]

        def add_field(parent, label_text: str, var: tk.Variable, is_secret: bool = False, note: str = "", choices: list = None):
            row = ttk.Frame(parent)
            row.pack(fill=tk.X, padx=12, pady=5)

            lbl = ttk.Label(row, text=label_text, font=("Segoe UI", 9, "bold"))
            lbl.pack(anchor="w")

            if note:
                note_lbl = ttk.Label(row, text=note, font=("Segoe UI", 8), foreground="#9b94b5")
                note_lbl.pack(anchor="w", pady=(0, 2))

            entry_row = ttk.Frame(row)
            entry_row.pack(fill=tk.X)

            if choices:
                combo = ttk.Combobox(entry_row, textvariable=var, values=choices, font=("Segoe UI", 9))
                combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
            else:
                show_char = "•" if is_secret else ""
                ent = ttk.Entry(entry_row, textvariable=var, show=show_char, font=("Segoe UI", 9))
                ent.pack(side=tk.LEFT, fill=tk.X, expand=True)

                if is_secret:
                    def toggle_mask():
                        if ent.cget("show") == "":
                            ent.config(show="•")
                        else:
                            ent.config(show="")
                    btn_eye = tk.Button(entry_row, text="👁", relief=tk.FLAT, bg="#2e2844", fg="#ffffff", command=toggle_mask, padx=6)
                    btn_eye.pack(side=tk.LEFT, padx=(5, 0))

        # Section 1: Core Discord & Cloud AI Credentials
        sec1 = ttk.LabelFrame(scrollable_frame, text="🔑 Core Discord & Cloud Credentials", padding=10)
        sec1.pack(fill=tk.X, padx=10, pady=6)

        add_field(sec1, "Discord Bot Token (DISCORD_BOT_TOKEN)", self.var_discord_token, is_secret=True, note="From Discord Developer Portal -> Bot -> Reset/Copy Token")
        add_field(sec1, "Google Gemini API Key (GEMINI_API_KEY)", self.var_gemini_key, is_secret=True, note="From Google AI Studio (aistudio.google.com)")
        add_field(sec1, "Default Gemini Model (GEMINI_MODEL)", self.var_gemini_model, is_secret=False, note="e.g. gemini-3.5-flash-lite, gemini-3.8-flash, gemini-3.1-flash-lite", choices=gemini_choices)

        # Section 2: 3-Tier Local Hardware AI Models
        sec2 = ttk.LabelFrame(scrollable_frame, text="🖥️ 3-Tier Local Hardware AI (Ollama / RTX 3060 12GB)", padding=10)
        sec2.pack(fill=tk.X, padx=10, pady=6)

        add_field(sec2, "Ollama Server Host (OLLAMA_HOST)", self.var_ollama_host, is_secret=False, note="Default: 127.0.0.1:11435 (avoids Windows port 11434 conflict)")
        add_field(sec2, "⚡ Fast Chat Tier (7B) - Roleplay & Casual Lore", self.var_chat_model, is_secret=False, note="~90 tok/s in VRAM (Select from installed Ollama models or type tag)", choices=ollama_models)
        add_field(sec2, "🧠 Deep Lore Tier (27B) - Timelines & Dossier Audits", self.var_deep_model, is_secret=False, note="High IQ context analysis (Select from installed Ollama models or type tag)", choices=ollama_models)
        add_field(sec2, "👁️ Vision Tier (7B-VL) - Photo & Visual Lore OCR", self.var_vision_model, is_secret=False, note="Multimodal visual analysis (Select from installed Ollama models or type tag)", choices=ollama_models)

        # Section 3: System Startup Options
        sec3 = ttk.LabelFrame(scrollable_frame, text="🚀 System Startup & Autostart", padding=10)
        sec3.pack(fill=tk.X, padx=10, pady=6)

        chk_startup = ttk.Checkbutton(
            sec3,
            text="Launch AskViolet automatically on Windows / Linux boot",
            variable=self.var_startup,
            command=self.toggle_startup,
        )
        chk_startup.pack(anchor="w", padx=12, pady=6)

        # Action Buttons
        btn_frame = ttk.Frame(scrollable_frame)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)

        btn_save = tk.Button(
            btn_frame,
            text="💾 Save Configuration to .env",
            bg="#9333ea",
            fg="#ffffff",
            activebackground="#a855f7",
            activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=16,
            pady=8,
            cursor="hand2",
            command=self.save_configuration,
        )
        btn_save.pack(side=tk.LEFT, padx=5)

    def toggle_startup(self):
        enabled = self.var_startup.get()
        success = set_startup_enabled(enabled)
        if success:
            action = "enabled" if enabled else "disabled"
            self.append_log(f"System startup has been {action}.", "info")
        else:
            self.var_startup.set(not enabled)
            messagebox.showerror("Startup Error", "Failed to register application startup.")

    def save_configuration(self):
        updated = {
            "DISCORD_BOT_TOKEN": self.var_discord_token.get().strip(),
            "GEMINI_API_KEY": self.var_gemini_key.get().strip(),
            "GEMINI_MODEL": self.var_gemini_model.get().strip() or "gemini-3.5-flash-lite",
            "OLLAMA_HOST": self.var_ollama_host.get().strip() or "127.0.0.1:11435",
            "LOCAL_CHAT_MODEL": self.var_chat_model.get().strip(),
            "LOCAL_DEEP_MODEL": self.var_deep_model.get().strip(),
            "LOCAL_VISION_MODEL": self.var_vision_model.get().strip(),
        }
        if save_env_file(updated):
            self.env_data = updated
            self.append_log("Configuration saved successfully to .env", "success")
            messagebox.showinfo("Saved", "Configuration has been saved to .env!\nChanges will take effect on next bot start.")
        else:
            messagebox.showerror("Save Error", "Failed to write updates to .env file.")

    # ----------------------------------------------------
    # Logging & Console Helpers
    # ----------------------------------------------------
    def append_log(self, message: str, tag: str = None):
        t_str = time.strftime("[%H:%M:%S] ")
        self.log_text.insert(tk.END, t_str, "timestamp")
        if tag:
            self.log_text.insert(tk.END, message + "\n", tag)
        else:
            self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    def clear_logs(self):
        self.log_text.delete("1.0", tk.END)

    # ----------------------------------------------------
    # Process Execution
    # ----------------------------------------------------
    def toggle_bot(self):
        if self.is_running:
            self.stop_bot()
        else:
            self.start_bot()

    def start_bot(self):
        if self.is_running:
            return

        bot_script = APP_DIR / "bot_dcedisabled.py"
        if not bot_script.exists():
            messagebox.showerror("Missing Script", f"Could not find {bot_script.name} in {APP_DIR}")
            return

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        creationflags = 0
        if is_windows():
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            self.bot_process = subprocess.Popen(
                [sys.executable, "-u", str(bot_script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=str(APP_DIR),
                env=env,
                creationflags=creationflags,
            )

            self.is_running = True
            self.btn_toggle.configure(text="⏹ Stop Bot", bg="#ef4444", activebackground="#dc2626")
            self.status_badge.configure(text="● RUNNING", bg="#10b981")
            self.append_log(f"Bot process started (PID: {self.bot_process.pid})", "success")

            reader_thread = threading.Thread(target=self._read_process_output, args=(self.bot_process,), daemon=True)
            reader_thread.start()

        except Exception as e:
            self.append_log(f"Failed to start bot process: {e}", "error")
            messagebox.showerror("Execution Error", f"Failed to start bot:\n{e}")

    def _read_process_output(self, proc: subprocess.Popen):
        try:
            for line in iter(proc.stdout.readline, ""):
                if line:
                    self.log_queue.put(line.rstrip())
                elif proc.poll() is not None:
                    break
        except Exception as e:
            self.log_queue.put(f"[Log Reader Error]: {e}")
        finally:
            proc.stdout.close()
            code = proc.wait()
            self.log_queue.put(f"__PROCESS_EXITED__{code}")

    def _process_log_queue(self):
        while not self.log_queue.empty():
            line = self.log_queue.get_nowait()
            if line.startswith("__PROCESS_EXITED__"):
                code = line.replace("__PROCESS_EXITED__", "")
                self.is_running = False
                self.btn_toggle.configure(text="▶ Start Bot", bg="#10b981", activebackground="#059669")
                self.status_badge.configure(text="● STOPPED", bg="#ef4444")
                self.append_log(f"Bot process stopped (Exit Code: {code})", "warning")
            else:
                tag = None
                lower = line.lower()
                if "error" in lower or "failed" in lower or "exception" in lower:
                    tag = "error"
                elif "warn" in lower:
                    tag = "warning"
                elif "success" in lower or "logged in" in lower or "synced" in lower:
                    tag = "success"
                self.append_log(line, tag)

        self.root.after(100, self._process_log_queue)

    def stop_bot(self):
        if not self.is_running or not self.bot_process:
            return

        self.append_log("Stopping bot process...", "warning")
        try:
            self.bot_process.terminate()
            threading.Thread(target=self._wait_and_kill, args=(self.bot_process,), daemon=True).start()
        except Exception as e:
            self.append_log(f"Error stopping process: {e}", "error")

    def _wait_and_kill(self, proc: subprocess.Popen):
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    def launch_lore_manager(self):
        lore_script = APP_DIR / "lore_manager.py"
        if is_windows():
            subprocess.Popen(["start", "cmd", "/k", sys.executable, str(lore_script)], shell=True, cwd=str(APP_DIR))
        else:
            subprocess.Popen(["x-terminal-emulator", "-e", f"{sys.executable} {lore_script}"], cwd=str(APP_DIR))
        self.append_log("Launched Lore Manager CLI terminal.", "info")

    def test_ollama(self):
        def _check():
            import urllib.request
            import json
            endpoint = self.var_ollama_host.get().strip() or "127.0.0.1:11435"
            url = f"http://{endpoint}/api/tags"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "AskViolet"})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    data = json.loads(resp.read().decode())
                    models = [m.get("name") for m in data.get("models", [])]
                    
                    # Check 3-tier status
                    chat_m = self.var_chat_model.get().strip()
                    deep_m = self.var_deep_model.get().strip()
                    vis_m = self.var_vision_model.get().strip()

                    def check_tag(target):
                        return "✅ Ready" if any(target.lower() in m.lower() for m in models) else "⏳ Pending download"

                    msg = (
                        f"Ollama is ONLINE at {endpoint}!\n\n"
                        f"• Fast Chat (7B): {check_tag(chat_m)}\n"
                        f"• Deep Lore (27B): {check_tag(deep_m)}\n"
                        f"• Vision (7B-VL): {check_tag(vis_m)}\n\n"
                        f"Total installed models ({len(models)}):\n" + "\n".join(f"• {m}" for m in models)
                    )
                    self.root.after(0, lambda: messagebox.showinfo("Ollama 3-Tier Status", msg))
                    self.root.after(0, lambda: self.append_log(f"Ollama connected ({len(models)} models available).", "success"))
            except Exception as e:
                err_msg = f"Could not connect to Ollama at {endpoint}:\n{e}"
                self.root.after(0, lambda: messagebox.showwarning("Ollama Offline", err_msg))
                self.root.after(0, lambda: self.append_log(f"Ollama check failed: {e}", "error"))

        threading.Thread(target=_check, daemon=True).start()

    def on_close(self):
        if self.is_running:
            if messagebox.askyesno("Exit Confirmation", "The bot is currently running. Stop the bot and exit?"):
                self.stop_bot()
                self.root.destroy()
        else:
            self.root.destroy()


def main():
    app = AskVioletThemedApp()
    app.run()


if __name__ == "__main__":
    main()
