#!/usr/bin/env python3
"""
Instalador de Asimov: prepara un bot de Telegram local sobre Ollama,
partiendo de un modelo acorde al hardware y dejando que el usuario decida,
midiendo el tiempo real, si quiere bajar a uno más ligero.
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

# De mayor/mejor a menor/más rápido. El instalador elige un punto de
# partida según RAM/CPU/GPU y va bajando de nivel si el usuario lo pide.
MODEL_TIERS = [
    {"name": "llama3.1:8b",  "min_ram_gb": 16},
    {"name": "mistral:7b",   "min_ram_gb": 12},
    {"name": "llama3.2:3b",  "min_ram_gb": 6},
    {"name": "llama3.2:1b",  "min_ram_gb": 3},
    {"name": "qwen2.5:0.5b", "min_ram_gb": 0},
]
EMBED_MODEL = "nomic-embed-text"


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
        return 4.0  # estimación conservadora si no se puede detectar


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
        idx = 0  # con GPU dedicada, el modelo más grande suele ir sobrado
    elif cpu_cores < 4:
        idx = min(idx + 1, len(MODEL_TIERS) - 1)  # pocos núcleos: empezar más ligero
    return idx


def ensure_ollama_installed():
    if shutil.which("ollama"):
        print("Ollama ya está instalado.")
        return
    system = platform.system()
    if system == "Linux":
        print("Instalando Ollama (script oficial de ollama.com)...")
        subprocess.run("curl -fsSL https://ollama.com/install.sh | sh", shell=True, check=True)
    elif system == "Darwin":
        print("Instala Ollama manualmente desde https://ollama.com/download y vuelve a ejecutar este instalador.")
        sys.exit(1)
    else:
        print(f"SO no soportado todavía por este instalador: {system}.")
        print("Instala Ollama manualmente desde https://ollama.com/download.")
        sys.exit(1)


def ollama_pull(model):
    print(f"  Descargando {model}...")
    subprocess.run(["ollama", "pull", model], check=True)


def benchmark(model):
    payload = json.dumps({"model": model, "prompt": BENCHMARK_PROMPT, "stream": False}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", data=payload,
        headers={"Content-Type": "application/json"}
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=RESPONSE_BUDGET_SECONDS + 20) as resp:
            resp.read()
    except Exception as e:
        print(f"  Error probando {model}: {e}")
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
                print("No se pudo probar ningún modelo. Revisa que Ollama esté corriendo.")
                sys.exit(1)
            idx += 1
            continue

        print(f"Con el modelo actual ({model}) el sistema tarda {elapsed:.1f} segundos en contestar.")

        if idx == len(MODEL_TIERS) - 1:
            print("Ya es el modelo más ligero disponible.")
            return model

        answer = input("¿Probamos con uno más ligero? [S/N]: ").strip().lower()
        if answer == "s":
            idx += 1
            continue
        return model


def choose_response_style():
    print("\nElige el estilo de respuesta del bot:")
    for i, key in enumerate(RESPONSE_STYLE_ORDER, start=1):
        print(f"  {i}. {RESPONSE_STYLE_LABELS[key]}")
    while True:
        choice = input(f"Opción [1-{len(RESPONSE_STYLE_ORDER)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(RESPONSE_STYLE_ORDER):
            return RESPONSE_STYLE_ORDER[int(choice) - 1]
        print("Opción no válida.")


def write_env(telegram_token, chat_model, response_style):
    with open(".env", "w") as f:
        f.write(f"TELEGRAM_TOKEN={telegram_token}\n")
        f.write(f"OLLAMA_URL={OLLAMA_URL}\n")
        f.write("OLLAMA_KEEP_ALIVE=30m\n")
        f.write(f"CHAT_MODEL={chat_model}\n")
        f.write(f"RESPONSE_STYLE={response_style}\n")
        f.write("DB_PATH=./conversations.db\n")
        f.write("MEMORY_DB_PATH=./memory_db\n")
    print(".env creado.")


def setup_venv():
    if not os.path.isdir("venv"):
        print("Creando entorno virtual...")
        subprocess.run([sys.executable, "-m", "venv", "venv"], check=True)
    pip = os.path.join("venv", "bin", "pip") if platform.system() != "Windows" \
        else os.path.join("venv", "Scripts", "pip.exe")
    print("Instalando dependencias...")
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
    print(f"\nGenerado {unit_path}. Para activarlo como servicio:")
    print(f"  sudo cp {unit_path} /etc/systemd/system/asimov.service")
    print("  sudo systemctl daemon-reload")
    print("  sudo systemctl enable --now asimov")


def main():
    print("=== Instalador de Asimov ===\n")

    if platform.system() != "Linux":
        print(f"Este instalador solo soporta Linux por ahora (detectado: {platform.system()}).")
        sys.exit(1)

    ensure_ollama_installed()

    ram_gb = detect_ram_gb()
    cpu_cores = detect_cpu_cores()
    gpu = has_nvidia_gpu()
    print(f"RAM detectada: {ram_gb:.1f} GB | CPU: {cpu_cores} núcleos | GPU NVIDIA: {'sí' if gpu else 'no'}\n")

    ollama_pull(EMBED_MODEL)
    chat_model = choose_chat_model(ram_gb, cpu_cores, gpu)
    print(f"\nModelo de chat elegido: {chat_model}\n")

    response_style = choose_response_style()

    token = input("Introduce tu token de Telegram (de @BotFather): ").strip()
    if not token:
        print("Token vacío, abortando.")
        sys.exit(1)

    write_env(token, chat_model, response_style)
    setup_venv()

    as_service = input("\n¿Configurar como servicio systemd para que arranque solo? [s/N]: ").strip().lower()
    if as_service == "s":
        write_systemd_service()

    print("\nInstalación completa. Para arrancar el bot manualmente:")
    print("  source venv/bin/activate")
    print("  python bot.py")


if __name__ == "__main__":
    main()
