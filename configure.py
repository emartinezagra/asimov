#!/usr/bin/env python3
"""Cambia el estilo de respuesta de Asimov (guardado en .env) y reinicia el bot."""
import os
import subprocess
import sys

from response_styles import RESPONSE_STYLE_LABELS, RESPONSE_STYLE_ORDER, DEFAULT_RESPONSE_STYLE

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
SYSTEMD_UNIT_PATH = "/etc/systemd/system/asimov.service"


def read_env():
    values = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    values[key] = value
    return values


def write_env(values):
    with open(ENV_PATH, "w") as f:
        for key, value in values.items():
            f.write(f"{key}={value}\n")


def choose_response_style(current):
    print("Estilo de respuesta actual:", RESPONSE_STYLE_LABELS.get(current, current))
    print("\nElige el nuevo estilo de respuesta del bot:")
    for i, key in enumerate(RESPONSE_STYLE_ORDER, start=1):
        marker = " (actual)" if key == current else ""
        print(f"  {i}. {RESPONSE_STYLE_LABELS[key]}{marker}")
    while True:
        choice = input(f"Opción [1-{len(RESPONSE_STYLE_ORDER)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(RESPONSE_STYLE_ORDER):
            return RESPONSE_STYLE_ORDER[int(choice) - 1]
        print("Opción no válida.")


def restart_bot():
    if os.path.exists(SYSTEMD_UNIT_PATH):
        print("\nReiniciando servicio systemd 'asimov'...")
        subprocess.run(["sudo", "systemctl", "restart", "asimov"], check=True)
        print("Hecho.")
    else:
        print("\nNo se ha encontrado el servicio systemd de Asimov.")
        print("Si lo tienes corriendo en tmux/screen, reinícialo manualmente:")
        print("  tmux attach -t asimov   # Ctrl+C para pararlo, luego: python bot.py")


def main():
    if not os.path.exists(ENV_PATH):
        print(".env no encontrado. Ejecuta primero install.py.")
        sys.exit(1)

    values = read_env()
    current_style = values.get("RESPONSE_STYLE", DEFAULT_RESPONSE_STYLE)

    new_style = choose_response_style(current_style)
    values["RESPONSE_STYLE"] = new_style
    write_env(values)
    print(f"\nEstilo actualizado a: {RESPONSE_STYLE_LABELS[new_style]}")

    restart_bot()


if __name__ == "__main__":
    main()
