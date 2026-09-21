import re
import json
import unicodedata
import logging
from datetime import datetime

import llm
import db
from response_styles import RESPONSE_STYLES, DEFAULT_RESPONSE_STYLE
from datetime_utils import current_datetime_label
import os

logger = logging.getLogger("asimov")

RESPONSE_STYLE_TEXT = RESPONSE_STYLES.get(
    os.getenv("RESPONSE_STYLE", DEFAULT_RESPONSE_STYLE),
    RESPONSE_STYLES[DEFAULT_RESPONSE_STYLE]
)

# [RECENT] literal-turn window, in TURNS (a turn = one Usuario + one Tú message).
# Small by default because [STATE] already carries condensed continuity, and a
# 3B model performs worse the more literal text gets mixed in. It only expands
# when the message looks context-dependent AND no explicit reference could be
# resolved deterministically (see resolve_reference below).
DEFAULT_WINDOW_TURNS = 2
EXPANDED_WINDOW_TURNS = 4
HISTORY_SNIPPET_CHARS = 300
STATE_FIELD_MAX_CHARS = 150
FACT_MAX_CHARS = 150

# A STATE field longer than this after extraction is treated as a likely
# copy-paste of prior assistant prose rather than a synthesized state, and
# is discarded (keeping the previous value) instead of accepted as-is.
FIELD_SANITY_MAX_CHARS = 220

# Every SUMMARY_BATCH_TURNS turns that fall out of the [RECENT] window without
# being folded yet trigger a [STATE] update + extraction of new facts for [MEM].
SUMMARY_BATCH_TURNS = 3

# Compact tool definitions shown to the model in every action-classification
# call. Kept intentionally terse (this is Llama 3.2 3B, not a big model).
TOOLS_BLOCK = """Responde SIEMPRE con un único objeto JSON, sin texto adicional, sin markdown, eligiendo una acción:
CHAT(message)
REMINDER(text, delay_seconds|datetime, missing?)
CANCEL_REMINDER(text)
LIST_REMINDERS()
CALENDAR(title, start, duration_minutes, attendees, missing?)
CANCEL_EVENT(title)
LIST_EVENTS()
EMAIL_DRAFT(to, subject, body, missing?)
SEND_EMAIL()

Usa "missing" (lista de campos que faltan) si no tienes toda la información necesaria para REMINDER, CALENDAR o EMAIL_DRAFT. No inventes fechas, horas ni destinatarios: si no los tienes, decláralos en "missing"."""


def truncate(text, max_chars):
    text = text.strip()
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "…"

def looks_referential(text):
    markers = ("eso", "ese", "esa", "él", "ella", "lo anterior", "el mismo", "la misma", "eso mismo")
    lowered = text.lower()
    return len(text) < 40 or any(m in lowered for m in markers)

# ---------- Deterministic reference resolution (no LLM) ----------

_NUMBER_WORDS = {"una": 1, "un": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}

_PREVIOUS_TURN_MARKERS = (
    "pregunte antes", "dije antes", "respuesta anterior", "pregunta anterior",
    "me contestaste", "respondiste antes", "acabo de preguntar", "te pregunte",
)

def _normalize(text):
    text = text.lower()
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")

def format_reference(conv_id, turn_number):
    turn = db.get_turn(conv_id, turn_number)
    if not turn:
        return None
    user_text, assistant_text = turn
    lines = []
    if user_text:
        lines.append(f"[turno {turn_number}] Usuario: {truncate(user_text, HISTORY_SNIPPET_CHARS)}")
    if assistant_text:
        lines.append(f"[turno {turn_number}] Tú: {truncate(assistant_text, HISTORY_SNIPPET_CHARS)}")
    return "\n".join(lines) if lines else None

def resolve_reference(conv_id, user_text):
    """Deterministically resolves references like "hace dos preguntas" or "la
    pregunta anterior" to a specific turn number, without involving the LLM."""
    current_turn = db.get_current_turn_number(conv_id)
    if current_turn == 0:
        return None
    norm = _normalize(user_text)

    # "hace N preguntas/mensajes/turnos" — the question being asked right now
    # would be turn (current_turn + 1), so "N questions ago" is
    # (current_turn + 1) - N = current_turn - N + 1.
    m = re.search(r"hace\s+(\d+|una|un|dos|tres|cuatro|cinco)\s+(pregunta|mensaje|turno)", norm)
    if m:
        token = m.group(1)
        n = int(token) if token.isdigit() else _NUMBER_WORDS.get(token)
        if n and 0 < n <= current_turn:
            return format_reference(conv_id, current_turn - n + 1)

    # "la primera pregunta" / "la primera cosa que te pedi"
    if re.search(r"\bla primera\b", norm):
        return format_reference(conv_id, 1)

    # a bare reference to "the previous turn" ("what did I ask before?", etc.)
    # — that's simply the last stored turn, i.e. current_turn itself.
    if current_turn >= 1 and any(marker in norm for marker in _PREVIOUS_TURN_MARKERS):
        return format_reference(conv_id, current_turn)

    return None

# ---------- Layered prompt construction ----------

def format_state(state):
    labels = (("topic", "Tema"), ("goal", "Objetivo"), ("pending", "Pendiente"), ("note", "Nota"))
    lines = [f"{label}: {truncate(state[key], STATE_FIELD_MAX_CHARS)}" for key, label in labels if state.get(key)]
    return "\n".join(lines)

def format_recent(recent):
    # Tags each Usuario/Tú pair with how many questions ago it was, so the
    # model doesn't have to count plain alternating lines itself.
    pairs = [recent[i:i + 2] for i in range(0, len(recent), 2)]
    total = len(pairs)
    lines = []
    for i, pair in enumerate(pairs):
        turns_ago = total - i
        tag = f"[hace {turns_ago} pregunta{'s' if turns_ago != 1 else ''}]"
        for role, content in pair:
            lines.append(f"{tag} {role}: {truncate(content, HISTORY_SNIPPET_CHARS)}")
    return "\n".join(lines)

def build_action_prompt(user_id, conv_id, user_text):
    """Builds the prompt for the main per-message call: the model both
    classifies the intent (CHAT vs a tool action) and extracts parameters,
    in a single call. [SYS] carries the compact tool definitions plus the
    real current date/time (the LLM never invents it)."""
    parts = [
        f"[SYS]\nEres un asistente personal. {current_datetime_label()}\n"
        f"{RESPONSE_STYLE_TEXT}\n\n{TOOLS_BLOCK}"
    ]

    facts = db.get_user_facts(user_id)
    if facts:
        parts.append("[MEM]\n" + "\n".join(f"- {f}" for f in facts))

    state, _ = db.get_conversation_state(conv_id)
    if state:
        parts.append(f"[STATE]\n{format_state(state)}")

    reference = resolve_reference(conv_id, user_text)
    if reference:
        parts.append(f"[REFERENCE]\n{reference}")
        window_turns = DEFAULT_WINDOW_TURNS
    else:
        window_turns = EXPANDED_WINDOW_TURNS if looks_referential(user_text) else DEFAULT_WINDOW_TURNS

    recent = db.get_recent_turns(conv_id, window_turns)
    if recent:
        parts.append(f"[RECENT]\n{format_recent(recent)}")

    parts.append(f"[USER]\n{user_text}")
    return "\n\n".join(parts)

# ---------- [STATE] update and [MEM] fact extraction ----------

_STATE_LABELS = (("topic", "TOPIC"), ("goal", "GOAL"), ("pending", "PENDING"), ("note", "NOTE"))

def parse_state_and_facts(text):
    fields = {}
    facts = []
    mode = None
    for line in text.strip().splitlines():
        stripped = line.strip()
        upper = stripped.upper()

        matched = False
        for key, label in _STATE_LABELS:
            if upper.startswith(label + ":"):
                value = stripped[len(label) + 1:].strip()
                fields[key] = None if (not value or value.upper() == "NONE") else value
                mode = None
                matched = True
                break
        if matched:
            continue

        if upper.startswith("FACTS:"):
            rest = stripped[len("FACTS:"):].strip()
            mode = "facts"
            if rest and rest.upper() != "NONE":
                facts.append(rest.lstrip("-").strip())
            continue

        if mode == "facts" and stripped.startswith("-"):
            fact = stripped.lstrip("-").strip()
            if fact and fact.upper() != "NONE":
                facts.append(fact)

    return fields, facts

def merge_state(prev_state, new_fields):
    merged = {}
    for key, _ in _STATE_LABELS:
        value = new_fields.get(key) if key in new_fields else "__unset__"
        if value == "__unset__":
            merged[key] = prev_state.get(key)  # field wasn't in the model's output at all
        elif value is None:
            merged[key] = None  # model explicitly said NONE: treat as resolved/cleared
        elif len(value) <= FIELD_SANITY_MAX_CHARS:
            merged[key] = value
        else:
            # Suspiciously long: likely copy-pasted prose instead of a synthesized
            # state, so keep the previous value instead of accepting it.
            merged[key] = prev_state.get(key)
    return merged

def maybe_update_state_and_facts(conv_id, user_id):
    current_turn = db.get_current_turn_number(conv_id)
    prev_state, summarized_up_to = db.get_conversation_state(conv_id)
    foldable = current_turn - DEFAULT_WINDOW_TURNS - summarized_up_to

    if foldable < SUMMARY_BATCH_TURNS:
        return

    to_turn = summarized_up_to + foldable
    batch = db.get_turns_range(conv_id, summarized_up_to, to_turn)
    transcript = "\n".join(f"{r}: {c}" for r, c in batch)

    extraction_prompt = f"""Analiza estos turnos de conversación y responde EXACTAMENTE en este \
formato, una línea por campo, sin texto adicional:

TOPIC: <tema actual en pocas palabras, o NONE>
GOAL: <objetivo actual del usuario, o NONE>
PENDING: <preguntas o tareas del usuario aún sin responder, o NONE>
NOTE: <si el usuario corrigió algo que dijiste, qué corrigió exactamente — nunca repitas tu \
afirmación original — o NONE>
FACTS:
- <hecho ESTABLE que el USUARIO haya dicho sobre sí mismo>
(o FACTS: NONE)

Reglas importantes:
- No copies frases textuales de tus propias respuestas anteriores: describe el estado, no lo repitas.
- No incluyas saludos ni cortesías.
- En FACTS, guarda solo información duradera (profesión, habilidades, objetivos a largo plazo, \
preferencias generales) — por ejemplo "Soy desarrollador web" sí es un hecho duradero. NO guardes \
información específica de esta conversación (por ejemplo "hoy busco ofertas de Python" es contexto \
de esta conversación, no un hecho permanente; eso va en PENDING o TOPIC, no en FACTS).
- No conviertas acciones ejecutadas (recordatorios, eventos, correos) en hechos permanentes: son \
contexto de esta conversación, no preferencias del usuario.

ESTADO PREVIO:
{format_state(prev_state) if prev_state else "(ninguno)"}

TURNOS:
{transcript}"""

    text, _ = llm.generate(extraction_prompt, log_label="state-extraction")
    if text is None:
        logger.error(f"Failed to update state/facts for conversation {conv_id}: no response from Ollama")
        return

    try:
        new_fields, new_facts = parse_state_and_facts(text)
        merged_state = merge_state(prev_state, new_fields)

        db.update_conversation_state(conv_id, merged_state, to_turn)
        for fact in new_facts:
            db.upsert_user_fact(user_id, truncate(fact, FACT_MAX_CHARS))

        logger.info(
            f"Updated state for conversation {conv_id} "
            f"(folded turns {summarized_up_to+1}-{to_turn}, {len(new_facts)} new facts)"
        )
    except Exception:
        logger.error(f"Failed to parse state/facts for conversation {conv_id}", exc_info=True)
