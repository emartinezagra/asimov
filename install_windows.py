#!/usr/bin/env python3
"""
Asimov installer (Windows): same flow as install.py (Linux), sharing all the
OS-agnostic logic from install_common.py. What's different here: Ollama has
no safe unattended install path on Windows (its installer is a GUI .exe, so
this guides you to it instead of running it for you), there's no /etc/timezone
to read, and autostart uses Task Scheduler instead of systemd.
"""
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request

import install_common as common

DEFAULT_TIMEZONE = "Europe/Madrid"
OLLAMA_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
# Ollama's Windows installer is Inno Setup-based and supports these silent-
# install flags for a normal per-user install (no admin rights needed — this
# is the same install mode as clicking through the GUI yourself). There's a
# known issue with /ALLUSERS (machine-wide, enterprise deployment) leaving
# registry entries user-scoped, but we don't use that flag, so it doesn't
# apply here.
SILENT_INSTALL_ARGS = ["/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"]


def _default_ollama_dir():
    """Where a per-user install lands — checked because a silent install
    doesn't update the PATH of this already-running process."""
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    return os.path.join(local_appdata, "Programs", "Ollama") if local_appdata else None


def _install_silently():
    installer_path = None
    try:
        installer_path = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
        print("Downloading the Ollama installer...")
        urllib.request.urlretrieve(OLLAMA_INSTALLER_URL, installer_path)
        print("Installing Ollama (silent)...")
        subprocess.run([installer_path, *SILENT_INSTALL_ARGS], check=True, timeout=300)
        return True
    except Exception as e:
        print(f"Automatic installation failed: {e}")
        return False
    finally:
        if installer_path and os.path.exists(installer_path):
            try:
                os.remove(installer_path)
            except OSError:
                pass


def _try_recover_from_default_dir():
    """True if ollama.exe exists at the standard per-user install location,
    in which case it's added to this process's PATH and reported as found."""
    ollama_dir = _default_ollama_dir()
    if ollama_dir and os.path.exists(os.path.join(ollama_dir, "ollama.exe")):
        os.environ["PATH"] = ollama_dir + os.pathsep + os.environ.get("PATH", "")
        return True
    return False


def ensure_ollama_installed():
    if shutil.which("ollama"):
        print("Ollama is already installed.")
        return

    # Not on PATH doesn't necessarily mean not installed: a silent install
    # (ours or a previous manual one) doesn't update the PATH of a terminal
    # that was already open. Check the standard install location BEFORE
    # claiming it's missing and offering to install it again.
    if _try_recover_from_default_dir():
        print("Ollama is already installed (added to PATH for this session).")
        return

    print("Ollama isn't installed.")
    answer = input(
        "Download and install it silently now (official installer from ollama.com)? [Y/n]: "
    ).strip().lower()

    if answer in ("", "y"):
        if not _install_silently():
            print("Download and run it yourself from: https://ollama.com/download/windows")
            input("Press Enter once you've finished installing Ollama to continue...")
    else:
        print("Download and run the installer from: https://ollama.com/download/windows")
        input("Press Enter once you've finished installing Ollama to continue...")

    if shutil.which("ollama"):
        print("Ollama detected.")
        return

    if _try_recover_from_default_dir():
        print("Ollama detected (added to PATH for this session).")
        return

    print("Still can't find 'ollama' on PATH.")
    print("You may need to open a new terminal — PATH changes made by the")
    print("installer only apply to terminals opened after it finished.")
    sys.exit(1)


def detect_timezone():
    # No reliable stdlib way to read the Windows timezone as an IANA name
    # without adding a dependency; defaults here and can be changed with
    # configure.py (option 2) if you're not in this timezone.
    return DEFAULT_TIMEZONE


def write_windows_autostart():
    workdir = os.getcwd()
    bat_path = os.path.join(workdir, "run_asimov.bat")
    with open(bat_path, "w", newline="\r\n") as f:
        f.write("@echo off\n")
        f.write('cd /d "%~dp0"\n')
        f.write('"%~dp0venv\\Scripts\\python.exe" bot.py\n')

    task_name = "Asimov"
    result = subprocess.run([
        "schtasks", "/create", "/tn", task_name,
        "/tr", f'"{bat_path}"',
        "/sc", "onlogon",
        "/rl", "limited",
        "/f",
    ], capture_output=True, text=True)

    if result.returncode == 0:
        print(f"\nScheduled task '{task_name}' created — Asimov will start automatically when you log in.")
        print(f"To start it right now:  schtasks /run /tn {task_name}")
        print(f"To remove it later:     schtasks /delete /tn {task_name} /f")
    else:
        print(f"\nCouldn't create the scheduled task automatically: {result.stderr.strip()}")
        print(f"You can still start Asimov by running: {bat_path}")


def main():
    print("=== Asimov installer (Windows) ===\n")

    if platform.system() != "Windows":
        print(f"This installer only supports Windows (detected: {platform.system()}).")
        print("On Linux, run install.py instead.")
        sys.exit(1)

    ensure_ollama_installed()

    ram_gb = common.detect_ram_gb()
    cpu_cores = common.detect_cpu_cores()
    gpu = common.has_gpu()
    common.print_hardware(ram_gb, cpu_cores, gpu)

    chat_model = common.choose_chat_model(ram_gb, cpu_cores, gpu)
    print(f"\nChosen chat model: {chat_model}\n")

    response_style = common.choose_response_style()
    token = common.ask_telegram_token()

    tz = detect_timezone()
    values = common.build_env_dict(token, chat_model, response_style, tz)
    common.write_env_file(values)
    print(f"(Timezone set to {tz} — change it with configure.py, option 2, if that's not yours.)")
    common.setup_venv()

    print("\nSetting up location, web search, and (optionally) email...")
    subprocess.run([common.venv_python_path(), "configure.py", "--first-time-setup"], check=True)

    as_service = input("\nSet it up to start automatically when you log in? [y/N]: ").strip().lower()
    if as_service == "y":
        write_windows_autostart()

    print("\nInstallation complete. To start the bot manually:")
    print("  venv\\Scripts\\activate")
    print("  python bot.py")


if __name__ == "__main__":
    main()
