#!/usr/bin/env python3
"""
Asimov installer: sets up a local Telegram bot on top of Ollama, starting
from a model that fits the hardware and letting the user decide, based on
real measured timing, whether to drop to a lighter one.
"""
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request

from response_styles import RESPONSE_STYLE_LABELS, RESPONSE_STYLE_ORDER, DEFAULT_RESPONSE_STYLE

OLLAMA_URL = "http://127.0.0.1:11434"
BENCHMARK_PROMPT = "Responde solo con la palabra 'ok'."

# From largest/best to smallest/fastest. The installer picks a starting point
# based on RAM/CPU/GPU and steps down a tier if the user asks for it.
MODEL_TIERS = [
    {"name": "llama3.1:8b",  "min_ram_gb": 16},
    {"name": "mistral:7b",   "min_ram_gb": 12},
    {"name": "llama3.2:3b",  "min_ram_gb": 6},
    {"name": "llama3.2:1b",  "min_ram_gb": 3},
    {"name": "qwen2.5:0.5b", "min_ram_gb": 0},
]
BENCHMARK_TIMEOUT_SECONDS = 60


def detect_ram_gb():
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        if platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / (1024 ** 2)
        return 4.0  # conservative estimate if it can't be detected


def detect_cpu_cores():
    try:
        import psutil
        return psutil.cpu_count(logical=True) or 1
    except ImportError:
        return os.cpu_count() or 1


def has_nvidia_gpu():
    return shutil.which("nvidia-smi") is not None


def initial_tier_index(ram_gb, cpu_cores, gpu):
    idx = next((i for i, t in enumerate(MODEL_TIERS) if ram_gb >= t["min_ram_gb"]),
               len(MODEL_TIERS) - 1)
    if gpu:
        idx = 0  # with a dedicated GPU, the largest model is usually comfortable
    elif cpu_cores < 4:
        idx = min(idx + 1, len(MODEL_TIERS) - 1)  # few cores: start lighter
    return idx


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
        print(f"This installer doesn't support this OS yet: {system}.")
        print("Install Ollama manually from https://ollama.com/download.")
        sys.exit(1)


def ollama_pull(model):
    print(f"  Downloading {model}...")
    subprocess.run(["ollama", "pull", model], check=True)


def benchmark(model):
    payload = json.dumps({"model": model, "prompt": BENCHMARK_PROMPT, "stream": False}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", data=payload,
        headers={"Content-Type": "application/json"}
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=BENCHMARK_TIMEOUT_SECONDS) as resp:
            resp.read()
    except Exception as e:
        print(f"  Error testing {model}: {e}")
        return None
    return time.time() - start


def choose_chat_model(ram_gb, cpu_cores, gpu):
    idx = initial_tier_index(ram_gb, cpu_cores, gpu)
    while True:
        model = MODEL_TIERS[idx]["name"]
        ollama_pull(model)
        elapsed = benchmark(model)
        if elapsed is None:
            if idx == len(MODEL_TIERS) - 1:
                print("Couldn't test any model. Check that Ollama is running.")
                sys.exit(1)
            idx += 1
            continue

        print(f"With the current model ({model}) the system takes {elapsed:.1f} seconds to reply.")

        if idx == len(MODEL_TIERS) - 1:
            print("This is already the lightest model available.")
            return model

        answer = input("Try a lighter one? [y/N]: ").strip().lower()
        if answer == "y":
            idx += 1
            continue
        return model


def choose_response_style():
    print("\nChoose the bot's response style:")
    for i, key in enumerate(RESPONSE_STYLE_ORDER, start=1):
        print(f"  {i}. {RESPONSE_STYLE_LABELS[key]}")
    while True:
        choice = input(f"Option [1-{len(RESPONSE_STYLE_ORDER)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(RESPONSE_STYLE_ORDER):
            return RESPONSE_STYLE_ORDER[int(choice) - 1]
        print("Invalid option.")


def detect_timezone():
    try:
        with open("/etc/timezone") as f:
            return f.read().strip() or "Europe/Madrid"
    except OSError:
        return "Europe/Madrid"


def write_env(telegram_token, chat_model, response_style):
    with open(".env", "w") as f:
        f.write(f"TELEGRAM_TOKEN={telegram_token}\n")
        f.write(f"OLLAMA_URL={OLLAMA_URL}\n")
        f.write("OLLAMA_KEEP_ALIVE=30m\n")
        f.write(f"CHAT_MODEL={chat_model}\n")
        f.write(f"RESPONSE_STYLE={response_style}\n")
        f.write("DB_PATH=./conversations.db\n")
        f.write(f"TIMEZONE={detect_timezone()}\n")
        f.write("CONTACTS=\n")
        f.write("EMAIL_SMTP_HOST=\n")
        f.write("EMAIL_SMTP_PORT=587\n")
        f.write("EMAIL_USER=\n")
        f.write("EMAIL_PASSWORD=\n")
        f.write("EMAIL_FROM=\n")
    print(".env created.")
    print("Reminders and calendar work out of the box. To enable sending emails,")
    print("edit .env and fill in CONTACTS and the EMAIL_* SMTP settings (see README).")


def setup_venv():
    if not os.path.isdir("venv"):
        print("Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", "venv"], check=True)
    pip = os.path.join("venv", "bin", "pip") if platform.system() != "Windows" \
        else os.path.join("venv", "Scripts", "pip.exe")
    print("Installing dependencies...")
    subprocess.run([pip, "install", "-r", "requirements.txt"], check=True)


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
    print("=== Asimov installer ===\n")

    if platform.system() != "Linux":
        print(f"This installer only supports Linux for now (detected: {platform.system()}).")
        sys.exit(1)

    ensure_ollama_installed()

    ram_gb = detect_ram_gb()
    cpu_cores = detect_cpu_cores()
    gpu = has_nvidia_gpu()
    print(f"Detected RAM: {ram_gb:.1f} GB | CPU: {cpu_cores} cores | NVIDIA GPU: {'yes' if gpu else 'no'}\n")

    chat_model = choose_chat_model(ram_gb, cpu_cores, gpu)
    print(f"\nChosen chat model: {chat_model}\n")

    response_style = choose_response_style()

    token = input("Enter your Telegram token (from @BotFather): ").strip()
    if not token:
        print("Empty token, aborting.")
        sys.exit(1)

    write_env(token, chat_model, response_style)
    setup_venv()

    as_service = input("\nSet it up as a systemd service so it starts on its own? [y/N]: ").strip().lower()
    if as_service == "y":
        write_systemd_service()

    print("\nInstallation complete. To start the bot manually:")
    print("  source venv/bin/activate")
    print("  python bot.py")


if __name__ == "__main__":
    main()
