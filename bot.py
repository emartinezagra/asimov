import os
import re
import unicodedata
import requests
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from db import (
    create_conversation, list_conversations, list_known_users,
    set_title_if_missing, add_message,
    get_current_turn_number, get_turn, get_recent_turns, get_turns_range,
    get_conversation_state, update_conversation_state,
    get_user_facts, upsert_user_fact
)
from logging_config import setup_logging
from response_styles import RESPONSE_STYLES, DEFAULT_RESPONSE_STYLE

load_dotenv()
logger = setup_logging()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.2:3b")          # change this to whichever model worked best for you
# How long Ollama keeps the model loaded in memory after each request.
# By default Ollama unloads it after 5 minutes of inactivity, which forces
# a slow reload (especially without a GPU) on the next request.
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
RESPONSE_STYLE_TEXT = RESPONSE_STYLES.get(
    os.getenv("RESPONSE_STYLE", DEFAULT_RESPONSE_STYLE),
    RESPONSE_STYLES[DEFAULT_RESPONSE_STYLE]
)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN. Set it in a .env file (see .env.example).")

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

active_conversation = {}   # user_id -> conversation_id (in RAM, lost on bot restart)
pending_reminders = {}     # simple, in RAM

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
    turn = get_turn(conv_id, turn_number)
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
    current_turn = get_current_turn_number(conv_id)
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

# ---------- Layered prompt construction: [SYS][MEM][STATE][REFERENCE][RECENT][USER] ----------

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

def build_prompt(user_id, conv_id, user_text):
    fecha_actual = datetime.now().strftime("%A %d de %B de %Y, %H:%M")
    parts = [f"[SYS]\nEres un asistente personal. Fecha: {fecha_actual}. {RESPONSE_STYLE_TEXT}"]

    facts = get_user_facts(user_id)
    if facts:
        parts.append("[MEM]\n" + "\n".join(f"- {f}" for f in facts))

    state, _ = get_conversation_state(conv_id)
    if state:
        parts.append(f"[STATE]\n{format_state(state)}")

    reference = resolve_reference(conv_id, user_text)
    if reference:
        parts.append(f"[REFERENCE]\n{reference}")
        window_turns = DEFAULT_WINDOW_TURNS  # already resolved explicitly, no need to widen RECENT
    else:
        window_turns = EXPANDED_WINDOW_TURNS if looks_referential(user_text) else DEFAULT_WINDOW_TURNS

    recent = get_recent_turns(conv_id, window_turns)
    if recent:
        parts.append(f"[RECENT]\n{format_recent(recent)}")

    parts.append(f"[USER]\n{user_text}")
    return "\n\n".join(parts)

# ---------- /start: choose new or continue ----------

def build_start_menu(user_id):
    conversations = list_conversations(user_id, limit=5)
    buttons = [[InlineKeyboardButton("🆕 Nueva conversación", callback_data="new")]]
    for conv_id, title, created_at in conversations:
        label = title if title else f"(vacía) {created_at[:16]}"
        buttons.append([InlineKeyboardButton(f"📂 {label}", callback_data=f"load:{conv_id}")])
    return InlineKeyboardMarkup(buttons)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"User {user_id} opened /start")
    await update.message.reply_text(
        "¿Quieres empezar una conversación nueva o continuar una anterior?",
        reply_markup=build_start_menu(user_id)
    )

async def announce_start_menu(application):
    for user_id in list_known_users():
        try:
            await application.bot.send_message(
                chat_id=user_id,
                text="El bot se ha reiniciado. ¿Quieres empezar una conversación nueva o continuar una anterior?",
                reply_markup=build_start_menu(user_id)
            )
            logger.info(f"Sent startup menu to user {user_id}")
        except Exception:
            logger.error(f"Failed to send startup menu to user {user_id}", exc_info=True)

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "new":
        conv_id = create_conversation(user_id)
        active_conversation[user_id] = conv_id
        logger.info(f"User {user_id} started new conversation {conv_id}")
        await query.edit_message_text("Conversación nueva iniciada. Escríbeme cuando quieras.")
    elif query.data.startswith("load:"):
        conv_id = query.data.split(":", 1)[1]
        active_conversation[user_id] = conv_id
        logger.info(f"User {user_id} loaded conversation {conv_id}")
        await query.edit_message_text("Conversación anterior cargada. Sigamos donde lo dejamos.")

# ---------- Reminders ----------

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    logger.info(f"Sending reminder to chat {job.chat_id}")
    await context.bot.send_message(chat_id=job.chat_id, text=job.data)

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

async def maybe_update_state_and_facts(conv_id, user_id):
    current_turn = get_current_turn_number(conv_id)
    prev_state, summarized_up_to = get_conversation_state(conv_id)
    foldable = current_turn - DEFAULT_WINDOW_TURNS - summarized_up_to

    if foldable < SUMMARY_BATCH_TURNS:
        return

    to_turn = summarized_up_to + foldable
    batch = get_turns_range(conv_id, summarized_up_to, to_turn)
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

ESTADO PREVIO:
{format_state(prev_state) if prev_state else "(ninguno)"}

TURNOS:
{transcript}"""

    try:
        resp = requests.post(f"{OLLAMA_URL}/api/generate", json={
            "model": CHAT_MODEL, "prompt": extraction_prompt, "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE
        })
        resp.raise_for_status()
        new_fields, new_facts = parse_state_and_facts(resp.json()["response"])
        merged_state = merge_state(prev_state, new_fields)

        update_conversation_state(conv_id, merged_state, to_turn)
        for fact in new_facts:
            upsert_user_fact(user_id, truncate(fact, FACT_MAX_CHARS))

        logger.info(
            f"Updated state for conversation {conv_id} "
            f"(folded turns {summarized_up_to+1}-{to_turn}, {len(new_facts)} new facts)"
        )
    except Exception:
        logger.error(f"Failed to update state/facts for conversation {conv_id}", exc_info=True)

# ---------- Regular messages ----------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    # No active conversation: force the user to pick one first
    if user_id not in active_conversation:
        await start(update, context)
        return

    conv_id = active_conversation[user_id]
    logger.info(f"User {user_id} sent a message in conversation {conv_id} ({len(user_text)} chars)")

    # Simple reminder detection (fixed 2 minutes, could be improved)
    if "recuérdame" in user_text.lower() or "recordatorio" in user_text.lower():
        context.job_queue.run_once(
            send_reminder, when=120,
            chat_id=update.effective_chat.id,
            data="⏰ Recordatorio: " + user_text
        )
        logger.info(f"Reminder scheduled for chat {update.effective_chat.id} in 120s")
        await update.message.reply_text("Vale, te lo recuerdo en 2 minutos.")
        return

    prompt = build_prompt(user_id, conv_id, user_text)
    logger.debug(f"Prompt sent to Ollama ({len(prompt)} chars) for conversation {conv_id}:\n{prompt}")

    try:
        resp = requests.post(f"{OLLAMA_URL}/api/generate", json={
            "model": CHAT_MODEL, "prompt": prompt, "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE
        })
        resp.raise_for_status()
        data = resp.json()
        answer = data["response"]
        load_s = data.get("load_duration", 0) / 1e9
        eval_s = data.get("eval_duration", 0) / 1e9
        prompt_eval_s = data.get("prompt_eval_duration", 0) / 1e9
        total_s = data.get("total_duration", 0) / 1e9
        logger.info(
            f"Ollama timing for conversation {conv_id}: "
            f"total={total_s:.1f}s load={load_s:.1f}s "
            f"prompt_eval={prompt_eval_s:.1f}s generation={eval_s:.1f}s "
            f"tokens={data.get('eval_count', 0)}"
        )
    except Exception:
        logger.error(f"Ollama request failed for conversation {conv_id}", exc_info=True)
        await update.message.reply_text("Ha ocurrido un error generando la respuesta. Inténtalo de nuevo.")
        return

    set_title_if_missing(conv_id, user_text)
    add_message(conv_id, "Usuario", user_text)
    add_message(conv_id, "Tú", answer)

    logger.info(f"Replied to user {user_id} in conversation {conv_id}")
    await update.message.reply_text(answer)

    await maybe_update_state_and_facts(conv_id, user_id)

# ---------- Startup ----------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception while processing an update", exc_info=context.error)

app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(announce_start_menu).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(handle_button))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_error_handler(error_handler)

logger.info(f"Starting Asimov bot with model {CHAT_MODEL}")
app.run_polling()
