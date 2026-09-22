#!/usr/bin/env python3
"""Change Asimov's settings (stored in .env) and restart the bot."""
import getpass
import os
import platform
import subprocess
import sys

import searxng_manager
from response_styles import RESPONSE_STYLE_LABELS, RESPONSE_STYLE_ORDER, DEFAULT_RESPONSE_STYLE

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
SYSTEMD_UNIT_PATH = "/etc/systemd/system/asimov.service"
DEFAULT_TIMEZONE = "Europe/Madrid"


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


def restart_bot():
    if platform.system() == "Windows":
        result = subprocess.run(["schtasks", "/query", "/tn", "Asimov"], capture_output=True, text=True)
        if result.returncode == 0:
            print("\nRestarting the 'Asimov' scheduled task...")
            print("(If bot.py is already running in a console window, close that window first.)")
            subprocess.run(["schtasks", "/run", "/tn", "Asimov"])
            print("Done.")
        else:
            print("\nNo 'Asimov' scheduled task found.")
            print("If you're running it in a console window, close it and start it again:")
            print("  venv\\Scripts\\activate")
            print("  python bot.py")
        return

    if os.path.exists(SYSTEMD_UNIT_PATH):
        print("\nRestarting systemd service 'asimov'...")
        subprocess.run(["sudo", "systemctl", "restart", "asimov"], check=True)
        print("Done.")
    else:
        print("\nAsimov's systemd service wasn't found.")
        print("If you're running it in tmux/screen, restart it manually:")
        print("  tmux attach -t asimov   # Ctrl+C to stop it, then: python bot.py")


# ---------- Response style ----------

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


# ---------- Timezone ----------

def choose_timezone(current):
    print(f"Current timezone: {current or '(not set, defaults to ' + DEFAULT_TIMEZONE + ')'}")
    tz = input(f"New IANA timezone (e.g. {DEFAULT_TIMEZONE}) [leave empty to keep current]: ").strip()
    if not tz:
        return current or DEFAULT_TIMEZONE
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
    except Exception:
        print(f"'{tz}' isn't a valid timezone, keeping the previous one.")
        return current or DEFAULT_TIMEZONE
    return tz


# ---------- Contacts + email (SMTP) ----------

def parse_contacts(raw):
    contacts = {}
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if pair and ":" in pair:
            name, _, email_addr = pair.partition(":")
            contacts[name.strip()] = email_addr.strip()
    return contacts


def contacts_to_string(contacts):
    return ",".join(f"{name}:{email_addr}" for name, email_addr in contacts.items())


def choose_contacts(current_raw):
    contacts = parse_contacts(current_raw)
    if contacts:
        print("Current contacts:")
        for name, email_addr in contacts.items():
            print(f"  - {name}: {email_addr}")
    else:
        print("No contacts configured yet.")

    print("\nAdd contacts (leave the name empty to finish). Existing names get overwritten.")
    while True:
        name = input("Name: ").strip()
        if not name:
            break
        email_addr = input(f"Email for {name}: ").strip()
        if email_addr:
            contacts[name] = email_addr

    return contacts_to_string(contacts)


def choose_email_settings(values):
    print("\nSMTP settings for sending email. Leave a field empty to keep its current value.")

    host = input(f"SMTP host [{values.get('EMAIL_SMTP_HOST') or 'not set'}]: ").strip()
    if host:
        values["EMAIL_SMTP_HOST"] = host

    port = input(f"SMTP port [{values.get('EMAIL_SMTP_PORT') or '587'}]: ").strip()
    if port:
        values["EMAIL_SMTP_PORT"] = port
    elif not values.get("EMAIL_SMTP_PORT"):
        values["EMAIL_SMTP_PORT"] = "587"

    user = input(f"SMTP user/email [{values.get('EMAIL_USER') or 'not set'}]: ").strip()
    if user:
        values["EMAIL_USER"] = user

    password = getpass.getpass("SMTP password (input hidden, leave empty to keep current): ").strip()
    if password:
        values["EMAIL_PASSWORD"] = password

    from_addr = input(f"From address [{values.get('EMAIL_FROM') or 'same as SMTP user'}]: ").strip()
    if from_addr:
        values["EMAIL_FROM"] = from_addr

    return values


def configure_email(values):
    values["CONTACTS"] = choose_contacts(values.get("CONTACTS", ""))
    return choose_email_settings(values)


# ---------- Location + web search ----------

def choose_location_and_search(values):
    print("\nDefault city for weather questions that don't name one (e.g. \"¿va a llover mañana?\").")
    location = input(f"Default location [{values.get('DEFAULT_LOCATION') or 'not set'}]: ").strip()
    if location:
        values["DEFAULT_LOCATION"] = location

    print()
    url, error = searxng_manager.configure_searxng(values.get("SEARXNG_URL"))
    if url:
        values["SEARXNG_URL"] = url
    else:
        print(f"\nWeb search not configured: {error}")
        print("Everything else (reminders, calendar, notes, tasks, memory, weather) still works.")

    return values


# ---------- Main menu ----------

def main():
    if not os.path.exists(ENV_PATH):
        print(".env not found. Run install.py first.")
        sys.exit(1)

    values = read_env()

    if "--check-searxng" in sys.argv:
        print(searxng_manager.status_report(values.get("SEARXNG_URL")))
        return

    print("=== Configure Asimov ===")
    print("  1. Response style")
    print("  2. Timezone (used to resolve reminders/calendar dates)")
    print("  3. Email (contacts + SMTP, needed to actually send emails)")
    print("  4. Default location + web search (weather, SearXNG)")
    choice = input("Option [1-4]: ").strip()

    if choice == "1":
        new_style = choose_response_style(values.get("RESPONSE_STYLE", DEFAULT_RESPONSE_STYLE))
        values["RESPONSE_STYLE"] = new_style
        write_env(values)
        print(f"\nStyle updated to: {RESPONSE_STYLE_LABELS[new_style]}")
    elif choice == "2":
        values["TIMEZONE"] = choose_timezone(values.get("TIMEZONE"))
        write_env(values)
        print(f"\nTimezone updated to: {values['TIMEZONE']}")
    elif choice == "3":
        values = configure_email(values)
        write_env(values)
        print("\nEmail settings updated.")
    elif choice == "4":
        values = choose_location_and_search(values)
        write_env(values)
        print("\nLocation/search settings updated.")
    else:
        print("Invalid option.")
        sys.exit(1)

    restart_bot()


if __name__ == "__main__":
    main()
