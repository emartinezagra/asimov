import os
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
    set_title_if_missing, add_message, get_recent_messages,
    count_messages, get_conversation_summary_state,
    update_conversation_summary, get_messages_range,
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

# [RECENT] literal-turn window: small by default because [STATE] already
# carries condensed continuity; it only expands when the message looks
# like it depends on recent context (pronouns, very short/elliptical messages).
DEFAULT_WINDOW = 4
EXPANDED_WINDOW = 8
HISTORY_SNIPPET_CHARS = 300
STATE_MAX_CHARS = 400
FACT_MAX_CHARS = 150

# Every SUMMARY_BATCH_SIZE messages that fall out of the [RECENT] window without
# being folded yet trigger a [STATE] update + extraction of new facts for [MEM].
SUMMARY_BATCH_SIZE = 6

active_conversation = {}   # user_id -> conversation_id (in RAM, lost on bot restart)
pending_reminders = {}     # simple, in RAM

def truncate(text, max_chars):
    text = text.strip()
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "…"

def looks_referential(text):
    markers = ("eso", "ese", "esa", "él", "ella", "lo anterior", "el mismo", "la misma", "eso mismo")
    lowered = text.lower()
    return len(text) < 40 or any(m in lowered for m in markers)

# ---------- Layered prompt construction: [SYS][MEM][STATE][RECENT][USER] ----------

def format_recent(recent):
    # Tags each Usuario/Tú pair with how many questions ago it was, so the
    # model doesn't have to count plain alternating lines itself to answer
    # something like "what did I ask two questions ago?".
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

    state, _ = get_conversation_summary_state(conv_id)
    if state:
        parts.append(f"[STATE]\n{truncate(state, STATE_MAX_CHARS)}")

    window = EXPANDED_WINDOW if looks_referential(user_text) else DEFAULT_WINDOW
    recent = get_recent_messages(conv_id, limit=window)
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

def parse_extraction(text):
    state = None
    facts = []
    mode = None
    for line in text.strip().splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("STATE:"):
            state = stripped[len("STATE:"):].strip()
            mode = "state"
            continue
        if stripped.upper().startswith("FACTS:"):
            rest = stripped[len("FACTS:"):].strip()
            mode = "facts"
            if rest and rest.upper() != "NONE":
                facts.append(rest.lstrip("-").strip())
            continue
        if mode == "facts" and stripped.startswith("-"):
            fact = stripped.lstrip("-").strip()
            if fact and fact.upper() != "NONE":
                facts.append(fact)
        elif mode == "state" and stripped:
            state = f"{state} {stripped}" if state else stripped
    return state, facts

async def maybe_update_state_and_facts(conv_id, user_id):
    total = count_messages(conv_id)
    prev_state, summarized_up_to = get_conversation_summary_state(conv_id)
    foldable = total - DEFAULT_WINDOW - summarized_up_to

    if foldable < SUMMARY_BATCH_SIZE:
        return

    batch = get_messages_range(conv_id, offset=summarized_up_to, limit=foldable)
    transcript = "\n".join(f"{r}: {c}" for r, c in batch)

    extraction_prompt = f"""A partir del ESTADO PREVIO y estos turnos, genera dos cosas.

STATE: una línea compacta con el tema, entidades mencionadas, decisiones tomadas y tareas \
pendientes de esta conversación, y cualquier referente necesario para entender pronombres \
futuros como "eso" o "el anterior". Si el usuario corrigió una afirmación del asistente, \
refleja la corrección, no la afirmación original. No incluyas saludos, cortesías ni texto repetido.

FACTS: hechos estables que el USUARIO haya dicho sobre sí mismo (edad, trabajo, preferencias, \
objetivos). Ignora cualquier frase dicha por el asistente. Un hecho por línea, empezando con "-". \
Si no hay ninguno, escribe NONE.

ESTADO PREVIO: {prev_state or "(ninguno)"}

TURNOS:
{transcript}

Responde exactamente en este formato:
STATE: <una línea>
FACTS:
- <hecho 1>
- <hecho 2>
(o FACTS: NONE si no hay ninguno)"""

    try:
        resp = requests.post(f"{OLLAMA_URL}/api/generate", json={
            "model": CHAT_MODEL, "prompt": extraction_prompt, "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE
        })
        resp.raise_for_status()
        new_state, new_facts = parse_extraction(resp.json()["response"])

        update_conversation_summary(conv_id, new_state or prev_state, summarized_up_to + foldable)
        for fact in new_facts:
            upsert_user_fact(user_id, truncate(fact, FACT_MAX_CHARS))

        logger.info(
            f"Updated state for conversation {conv_id} "
            f"(folded {foldable} messages, {len(new_facts)} new facts)"
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
