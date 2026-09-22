#!/usr/bin/env python3
"""
Asimov installer (Linux): sets up a local Telegram bot on top of Ollama,
starting from a model that fits the hardware and letting the user decide,
based on real measured timing, whether to drop to a lighter one.

Platform-specific here: installing Ollama via the official curl|sh script,
detecting the timezone from /etc/timezone, and generating a systemd unit.
Everything else lives in install_common.py, shared with install_windows.py.
"""
import os
import platform
import shutil
import subprocess
import sys

import install_common as common


def ensure_ollama_installed():
    if shutil.which("ollama"):
        print("Ollama is already installed.")
        return
    system = platform.system()
    if system == "Linux":
        print("Installing Ollama (official ollama.com script)...")
        subprocess.run("curl -fsSL https://ollama.com/install.sh | sh", shell=True, check=True)
    elif system == "Darwin":
        print("Install Ollama manually from https://ollama.com/download and re-run this installer.")
        sys.exit(1)
    else:
        print(f"This installer doesn't support this OS: {system}.")
        print("On Windows, run install_windows.py instead.")
        sys.exit(1)


def detect_timezone():
    try:
        with open("/etc/timezone") as f:
            return f.read().strip() or "Europe/Madrid"
    except OSError:
        return "Europe/Madrid"


def write_systemd_service():
    workdir = os.getcwd()
    python_bin = os.path.join(workdir, "venv", "bin", "python")
    unit = f"""[Unit]
Description=Asimov Telegram bot
After=network.target

[Service]
Type=simple
WorkingDirectory={workdir}
ExecStart={python_bin} {workdir}/bot.py
Restart=on-failure
User={os.environ.get("USER", "")}

[Install]
WantedBy=multi-user.target
"""
    unit_path = os.path.join(workdir, "asimov.service")
    with open(unit_path, "w") as f:
        f.write(unit)
    print(f"\nGenerated {unit_path}. To enable it as a service:")
    print(f"  sudo cp {unit_path} /etc/systemd/system/asimov.service")
    print("  sudo systemctl daemon-reload")
    print("  sudo systemctl enable --now asimov")


def main():
    print("=== Asimov installer (Linux) ===\n")

    if platform.system() != "Linux":
        print(f"This installer only supports Linux (detected: {platform.system()}).")
        print("On Windows, run install_windows.py instead.")
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

    common.write_env(token, chat_model, response_style, detect_timezone())
    common.setup_venv()

    as_service = input("\nSet it up as a systemd service so it starts on its own? [y/N]: ").strip().lower()
    if as_service == "y":
        write_systemd_service()

    print("\nInstallation complete. To start the bot manually:")
    print("  source venv/bin/activate")
    print("  python bot.py")


if __name__ == "__main__":
    main()
