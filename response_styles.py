# These stay in Spanish on purpose: they're injected straight into the
# prompt and instruct the model, which converses with Telegram users in Spanish.
RESPONSE_STYLES = {
    "brief": "Responde de forma natural y breve.",
    "technical": "Responde de forma técnica y extensa, aportando todo el detalle posible.",
    "balanced": "Responde de forma equilibrada, sin ser demasiado breve ni demasiado extensa.",
}

# CLI-facing labels shown by install.py/configure.py, kept in English.
RESPONSE_STYLE_LABELS = {
    "brief": "Natural and brief",
    "technical": "Technical and detailed",
    "balanced": "Balanced (neither too brief nor too long)",
}

RESPONSE_STYLE_ORDER = ["brief", "technical", "balanced"]

DEFAULT_RESPONSE_STYLE = "balanced"
