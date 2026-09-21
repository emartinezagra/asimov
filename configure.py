#!/usr/bin/env python3
"""Change Asimov's response style (stored in .env) and restart the bot."""
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
    print("Current response style:", RESPONSE_STYLE_LABELS.get(current, current))
    print("\nChoose the bot's new response style:")
    for i, key in enumerate(RESPONSE_STYLE_ORDER, start=1):
        marker = " (current)" if key == current else ""
        print(f"  {i}. {RESPONSE_STYLE_LABELS[key]}{marker}")
    while True:
        choice = input(f"Option [1-{len(RESPONSE_STYLE_ORDER)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(RESPONSE_STYLE_ORDER):
            return RESPONSE_STYLE_ORDER[int(choice) - 1]
        print("Invalid option.")


def restart_bot():
    if os.path.exists(SYSTEMD_UNIT_PATH):
        print("\nRestarting systemd service 'asimov'...")
        subprocess.run(["sudo", "systemctl", "restart", "asimov"], check=True)
        print("Done.")
    else:
        print("\nAsimov's systemd service wasn't found.")
        print("If you're running it in tmux/screen, restart it manually:")
        print("  tmux attach -t asimov   # Ctrl+C to stop it, then: python bot.py")


def main():
    if not os.path.exists(ENV_PATH):
        print(".env not found. Run install.py first.")
        sys.exit(1)

    values = read_env()
    current_style = values.get("RESPONSE_STYLE", DEFAULT_RESPONSE_STYLE)

    new_style = choose_response_style(current_style)
    values["RESPONSE_STYLE"] = new_style
    write_env(values)
    print(f"\nStyle updated to: {RESPONSE_STYLE_LABELS[new_style]}")

    restart_bot()


if __name__ == "__main__":
    main()
