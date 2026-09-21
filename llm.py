import os
import logging
import requests

logger = logging.getLogger("asimov")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.2:3b")
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")


def generate(prompt, json_mode=False, log_label=None):
    """Single call to Ollama's /api/generate. Returns (text, timing_dict) or
    (None, None) on failure. json_mode asks Ollama to constrain its output
    to valid JSON (grammar-constrained decoding) — a strong first defense
    against malformed output, on top of the Python-side validation that
    always follows."""
    payload = {
        "model": CHAT_MODEL, "prompt": prompt, "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE
    }
    if json_mode:
        payload["format"] = "json"

    try:
        resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.error(f"Ollama request failed{f' ({log_label})' if log_label else ''}", exc_info=True)
        return None, None

    timing = {
        "total_s": data.get("total_duration", 0) / 1e9,
        "load_s": data.get("load_duration", 0) / 1e9,
        "prompt_eval_s": data.get("prompt_eval_duration", 0) / 1e9,
        "eval_s": data.get("eval_duration", 0) / 1e9,
        "tokens": data.get("eval_count", 0),
    }
    if log_label:
        logger.info(
            f"Ollama timing [{log_label}]: total={timing['total_s']:.1f}s load={timing['load_s']:.1f}s "
            f"prompt_eval={timing['prompt_eval_s']:.1f}s generation={timing['eval_s']:.1f}s "
            f"tokens={timing['tokens']}"
        )
    return data.get("response"), timing
