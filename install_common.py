"""Shared, OS-agnostic pieces of the Asimov installer. install.py (Linux)
and install_windows.py (Windows) both import this — hardware detection,
model selection/benchmarking, response style, .env writing, and the venv
setup are identical on every platform; only how Ollama gets installed, how
the timezone is detected, and how the bot is set to autostart differ.
"""
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request

from response_styles import RESPONSE_STYLE_LABELS, RESPONSE_STYLE_ORDER

OLLAMA_URL = "http://127.0.0.1:11434"
BENCHMARK_PROMPT = "Responde solo con la palabra 'ok'."
BENCHMARK_TIMEOUT_SECONDS = 60

# From largest/best to smallest/fastest. The installer picks a starting point
# based on RAM/CPU/GPU and steps down a tier if the user asks for it.
MODEL_TIERS = [
    {"name": "llama3.1:8b",  "min_ram_gb": 16},
    {"name": "mistral:7b",   "min_ram_gb": 12},
    {"name": "llama3.2:3b",  "min_ram_gb": 6},
    {"name": "llama3.2:1b",  "min_ram_gb": 3},
    {"name": "qwen2.5:0.5b", "min_ram_gb": 0},
]


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


def has_amd_gpu():
    """Matches integrated AMD graphics too, not just discrete cards. That's
    deliberate: Ollama's Vulkan backend (on by default, no setup needed on
    Windows) can accelerate on an AMD iGPU even though AMD's own ROCm
    backend explicitly doesn't support Windows APUs — detecting "any AMD
    GPU" is a reasonable starting signal either way, and the real benchmark
    that follows is what actually decides, not this guess."""
    system = platform.system()
    if system == "Windows":
        result = _run_quiet([
            "powershell", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_VideoController).Name"
        ])
        if result is None:
            return False
        output = (result.stdout or "").lower()
        return "amd" in output or "radeon" in output
    if system == "Linux":
        result = _run_quiet(["lspci"])
        if result is None:
            return False
        for line in (result.stdout or "").splitlines():
            lower = line.lower()
            is_gpu_line = "vga" in lower or "3d controller" in lower or "display controller" in lower
            if is_gpu_line and ("amd" in lower or "radeon" in lower or "ati" in lower):
                return True
        return False
    return False


def _run_quiet(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def has_gpu():
    return has_nvidia_gpu() or has_amd_gpu()


def initial_tier_index(ram_gb, cpu_cores, gpu):
    idx = next((i for i, t in enumerate(MODEL_TIERS) if ram_gb >= t["min_ram_gb"]),
               len(MODEL_TIERS) - 1)
    if gpu:
        idx = 0  # with a dedicated GPU, the largest model is usually comfortable
    elif cpu_cores < 4:
        idx = min(idx + 1, len(MODEL_TIERS) - 1)  # few cores: start lighter
    return idx


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

        can_go_lighter = idx < len(MODEL_TIERS) - 1
        can_go_heavier = idx > 0
        if not can_go_lighter and not can_go_heavier:
            return model

        if can_go_lighter and can_go_heavier:
            prompt = "Try a [l]ighter model, a [h]eavier one, or keep this one? [l/h/Enter to keep]: "
        elif can_go_lighter:
            prompt = "This is the heaviest model available. Try a [l]ighter one? [l/Enter to keep]: "
        else:
            prompt = "This is the lightest model available. Try a [h]eavier one? [h/Enter to keep]: "

        answer = input(prompt).strip().lower()
        if answer == "l" and can_go_lighter:
            idx += 1
            continue
        if answer == "h" and can_go_heavier:
            idx -= 1
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


def write_env(telegram_token, chat_model, response_style, timezone):
    with open(".env", "w") as f:
        f.write(f"TELEGRAM_TOKEN={telegram_token}\n")
        f.write(f"OLLAMA_URL={OLLAMA_URL}\n")
        f.write("OLLAMA_KEEP_ALIVE=30m\n")
        f.write(f"CHAT_MODEL={chat_model}\n")
        f.write(f"RESPONSE_STYLE={response_style}\n")
        f.write("DB_PATH=./conversations.db\n")
        f.write(f"TIMEZONE={timezone}\n")
        f.write("CONTACTS=\n")
        f.write("EMAIL_SMTP_HOST=\n")
        f.write("EMAIL_SMTP_PORT=587\n")
        f.write("EMAIL_USER=\n")
        f.write("EMAIL_PASSWORD=\n")
        f.write("EMAIL_FROM=\n")
        f.write("DEFAULT_LOCATION=\n")
        f.write("SEARXNG_URL=\n")
    print(".env created.")
    print("Reminders, calendar, notes, tasks and memory work out of the box.")
    print("To enable sending emails, weather, or web search, edit .env (or run")
    print("configure.py) and fill in CONTACTS/EMAIL_*, DEFAULT_LOCATION, and")
    print("SEARXNG_URL respectively (see README).")


def setup_venv():
    if not os.path.isdir("venv"):
        print("Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", "venv"], check=True)
    pip = os.path.join("venv", "bin", "pip") if platform.system() != "Windows" \
        else os.path.join("venv", "Scripts", "pip.exe")
    print("Installing dependencies...")
    subprocess.run([pip, "install", "-r", "requirements.txt"], check=True)


def ask_telegram_token():
    token = input("Enter your Telegram token (from @BotFather): ").strip()
    if not token:
        print("Empty token, aborting.")
        sys.exit(1)
    return token


def gpu_label():
    if has_nvidia_gpu():
        return "yes (NVIDIA)"
    if has_amd_gpu():
        return "yes (AMD)"
    return "no"


def print_hardware(ram_gb, cpu_cores, gpu):
    print(f"Detected RAM: {ram_gb:.1f} GB | CPU: {cpu_cores} cores | GPU: {gpu_label() if gpu else 'no'}\n")
