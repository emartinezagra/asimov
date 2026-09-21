"""Detects, installs (via Docker) and health-checks a local SearXNG instance
for Asimov's WEB_SEARCH action. Used by configure.py — nothing here talks to
Telegram, Ollama, or the action router; it only prepares the search backend
tools/websearch.py's SearchService will call at runtime.

Asimov itself runs directly on the host (venv + systemd, confirmed by
inspecting install.py — there's no Docker/Compose anywhere in the project),
so this uses a single `docker run` for SearXNG only, published to
127.0.0.1 — no Docker Compose is introduced for the rest of the project.
"""
import logging
import os
import secrets
import shutil
import socket
import subprocess
import time

import requests

logger = logging.getLogger("asimov")

CONTAINER_NAME = "asimov-searxng"
IMAGE = "searxng/searxng:latest"
DEFAULT_PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "searxng")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.yml")


def _run(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def docker_available():
    """Returns (True, None) if Docker can be used, or (False, reason) where
    reason is 'not_installed', 'permission_denied', or 'daemon_unavailable'."""
    if not shutil.which("docker"):
        return False, "not_installed"
    result = _run(["docker", "ps"])
    if result is None:
        return False, "daemon_unavailable"
    if result.returncode == 0:
        return True, None
    if "permission denied" in (result.stderr or "").lower():
        return False, "permission_denied"
    return False, "daemon_unavailable"


def container_status():
    """Returns {"running": bool, "status": str} for CONTAINER_NAME, or None
    if no such container exists (running or stopped)."""
    result = _run([
        "docker", "ps", "-a",
        "--filter", f"name=^/{CONTAINER_NAME}$",
        "--format", "{{.Status}}",
    ])
    if result is None or result.returncode != 0:
        return None
    status = (result.stdout or "").strip()
    if not status:
        return None
    return {"running": status.lower().startswith("up"), "status": status}


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def check_searxng(base_url, timeout=10):
    """Health check: the endpoint must respond AND the JSON search API must
    return a structurally valid response — not just HTTP 200."""
    try:
        resp = requests.get(f"{base_url}/search", params={"q": "test", "format": "json"}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return False, str(e)
    if not isinstance(data, dict) or "results" not in data:
        return False, "unexpected response shape"
    return True, None


def wait_until_ready(base_url, timeout=60, interval=2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok, _ = check_searxng(base_url, timeout=5)
        if ok:
            return True
        time.sleep(interval)
    return False


def ensure_settings_file():
    """Generates settings.yml with a fresh secret_key only if one doesn't
    already exist — never regenerates it on a re-run (point 19/20: the key,
    and the rest of the config, must survive repeated `configure.py` runs)."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(SETTINGS_PATH):
        return
    secret_key = secrets.token_urlsafe(32)
    content = f"""use_default_settings: true

general:
  instance_name: "Asimov Search"

server:
  secret_key: "{secret_key}"
  limiter: false
  image_proxy: false

search:
  formats:
    - html
    - json
"""
    with open(SETTINGS_PATH, "w") as f:
        f.write(content)
    logger.info("[SearXNG] Generated settings.yml with a new secret_key")


def start_container(port):
    ensure_settings_file()
    logger.info(f"[SearXNG] Starting container '{CONTAINER_NAME}' on 127.0.0.1:{port}")
    result = _run([
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "--restart", "unless-stopped",
        "-p", f"127.0.0.1:{port}:8080",
        "-v", f"{CONFIG_DIR}:/etc/searxng",
        IMAGE,
    ], timeout=120)
    if result is None:
        return False, "docker run timed out or failed to execute"
    return result.returncode == 0, (result.stderr or "").strip()


def start_existing_container():
    result = _run(["docker", "start", CONTAINER_NAME], timeout=30)
    return result is not None and result.returncode == 0


def _offer_docker_install():
    print("Docker no está instalado. Es necesario para instalar SearXNG automáticamente.")
    answer = input(
        "¿Instalar Docker ahora con el script oficial de docker.com (pide sudo)? [y/N]: "
    ).strip().lower()
    if answer != "y":
        return (
            "Instalación de Docker cancelada. Instálalo manualmente "
            "(https://docs.docker.com/engine/install/) y vuelve a ejecutar: python configure.py"
        )
    print("Installing Docker...")
    result = subprocess.run("curl -fsSL https://get.docker.com | sudo sh", shell=True)
    if result.returncode != 0:
        return "No se pudo instalar Docker automáticamente. Instálalo manualmente y vuelve a intentarlo."
    return (
        "Docker instalado. Para usarlo sin sudo, añade tu usuario al grupo docker y reinicia sesión:\n"
        "  sudo usermod -aG docker $USER\n"
        "Luego cierra sesión (o reinicia) y vuelve a ejecutar: python configure.py"
    )


def configure_searxng(current_url=None):
    """Idempotent: safe to call on every `configure.py` run. Returns
    (url, error_message) — exactly one of the two is set."""
    print("Configuring web search (SearXNG)...")

    if current_url:
        ok, _ = check_searxng(current_url)
        if ok:
            print(f"SearXNG already configured and working at {current_url}.")
            return current_url, None

    default_url = f"http://127.0.0.1:{DEFAULT_PORT}"
    status = container_status()
    if status is not None:
        if not status["running"]:
            print(f"Found stopped container '{CONTAINER_NAME}', starting it...")
            start_existing_container()
        print("Waiting for SearXNG to be ready...")
        if wait_until_ready(default_url):
            print("Search: OK")
            return default_url, None
        return None, (
            f"El contenedor '{CONTAINER_NAME}' existe pero no responde correctamente. "
            f"Revísalo con: docker logs {CONTAINER_NAME}"
        )

    available, reason = docker_available()
    if not available:
        if reason == "permission_denied":
            return None, (
                "Docker está instalado pero tu usuario no tiene permiso para usarlo.\n"
                "Ejecuta esto y vuelve a iniciar sesión (o reinicia la terminal):\n"
                "  sudo usermod -aG docker $USER\n"
                "Luego vuelve a ejecutar: python configure.py"
            )
        if reason == "not_installed":
            msg = _offer_docker_install()
            if msg is None:
                return configure_searxng(current_url)
            return None, msg
        return None, "No se pudo conectar con Docker. Comprueba: sudo systemctl status docker"

    print("Docker: OK")
    print("SearXNG: not installed")

    port = DEFAULT_PORT
    if port_in_use(port):
        print(f"El puerto {port} ya está en uso por otro proceso.")
        alt = input("Introduce un puerto alternativo para SearXNG [8081]: ").strip()
        port = int(alt) if alt.isdigit() else 8081

    print("Installing SearXNG...")
    ok, err = start_container(port)
    if not ok:
        return None, f"No se pudo iniciar el contenedor de SearXNG: {err}"

    print("Waiting for SearXNG to be ready...")
    url = f"http://127.0.0.1:{port}"
    if not wait_until_ready(url):
        return None, f"SearXNG no respondió a tiempo tras instalarse. Comprueba: docker logs {CONTAINER_NAME}"

    print("Testing search API...")
    ok, err = check_searxng(url)
    if not ok:
        return None, f"SearXNG está corriendo pero la búsqueda de prueba falló: {err}"

    print(f"Web search configured successfully. Endpoint: {url}")
    logger.info(f"[SearXNG] Ready at {url}")
    return url, None


def status_report(configured_url):
    status = container_status()
    installed = status is not None
    running = bool(status and status["running"])
    url = configured_url or (f"http://127.0.0.1:{DEFAULT_PORT}" if running else None)

    lines = ["SearXNG", "-------", f"Installed: {'YES' if installed else 'NO'}", f"Running: {'YES' if running else 'NO'}"]
    if url:
        lines.append(f"Endpoint: {url}")
        ok, err = check_searxng(url)
        lines.append(f"API: {'OK' if ok else 'FAIL (' + err + ')'}")
        lines.append(f"Search: {'OK' if ok else 'FAIL'}")
    else:
        lines.append("Endpoint: (not set)")
        lines.append("API: N/A")
        lines.append("Search: N/A")
    return "\n".join(lines)
