import os
import sys
import subprocess
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def check_pyinstaller():
    try:
        import PyInstaller
        return True
    except ImportError:
        return False

def build():
    print("=" * 60)
    print("🚀 AskViolet Executable Builder (Windows / Linux)")
    print("=" * 60)

    if not check_pyinstaller():
        print("❌ PyInstaller is not installed in this Python environment.")
        print("💡 Run the following command in PowerShell / Bash to install it:")
        print(f"   {sys.executable} -m pip install pyinstaller\n")
        sys.exit(1)

    import TKinterModernThemes
    tkm_dir = Path(TKinterModernThemes.__file__).resolve().parent

    print(f"📦 Found TKinterModernThemes at: {tkm_dir}")

    # Separator for PyInstaller --add-data differs on Windows (;) vs Linux (:)
    sep = ";" if sys.platform.startswith("win") else ":"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--windowed",
        "--name", "AskViolet",
        "--add-data", f"{tkm_dir / 'themes'}{sep}TKinterModernThemes/themes",
        "--add-data", f"{BASE_DIR / 'bot_dcedisabled.py'}{sep}.",
        "--add-data", f"{BASE_DIR / 'lore_manager.py'}{sep}.",
        "--hidden-import", "TKinterModernThemes",
        "--hidden-import", "TKinterModernThemes.ThemeStyles",
        "--hidden-import", "discord",
        "--hidden-import", "google.genai",
        "--hidden-import", "dotenv",
        "--hidden-import", "sqlite3",
        "app_gui.py"
    ]

    print("\n🔨 Executing build command:")
    print(" ".join(cmd))
    print("\n⏳ Building package... This may take 1-2 minutes.")

    res = subprocess.run(cmd, cwd=str(BASE_DIR))
    if res.returncode == 0:
        dist_dir = BASE_DIR / "dist" / "AskViolet"
        print("\n" + "=" * 60)
        print("🎉 Build Complete!")
        print(f"📁 Output Location: {dist_dir}")
        if sys.platform.startswith("win"):
            print("🚀 Executable: AskViolet.exe")
        else:
            print("🚀 Executable: AskViolet")
        print("=" * 60)
    else:
        print(f"\n❌ Build failed with return code {res.returncode}")

if __name__ == "__main__":
    build()
