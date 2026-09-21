import os
import requests
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from memory import add_memory, search_memory
from db import (
    create_conversation, list_conversations, list_known_users,
    set_title_if_missing, add_message, get_recent_messages,
    count_messages, get_conversation_summary_state,
    update_conversation_summary, get_messages_range
)
from logging_config import setup_logging
from response_styles import RESPONSE_STYLES, DEFAULT_RESPONSE_STYLE

load_dotenv()
logger = setup_logging()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.2:3b")          # cambia esto por el modelo que mejor te fue
# Cuánto tiempo mantiene Ollama el modelo cargado en memoria tras cada petición.
# Por defecto Ollama lo descarga a los 5 minutos de inactividad, lo que fuerza
# una recarga lenta (sobre todo sin GPU) en la siguiente petición.
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
RESPONSE_STYLE_TEXT = RESPONSE_STYLES.get(
    os.getenv("RESPONSE_STYLE", DEFAULT_RESPONSE_STYLE),
    RESPONSE_STYLES[DEFAULT_RESPONSE_STYLE]
)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Falta TELEGRAM_TOKEN. Definilo en un archivo .env (ver .env.example).")

# Límites para que el prompt enviado a Ollama no crezca sin control: cuántos
# recuerdos/mensajes se recuperan, y cuántos caracteres de cada uno se usan.
MEMORY_RESULTS = 3
MEMORY_SNIPPET_CHARS = 300
HISTORY_MESSAGES = 6
HISTORY_SNIPPET_CHARS = 300
MEMORY_STORE_CHARS = 500  # también se recorta lo que se guarda, para que no siga creciendo

# Resumen progresivo: los mensajes que quedan fuera de la ventana de HISTORY_MESSAGES
# se van condensando en un resumen (guardado en conversations.summary) en vez de
# arrastrarse en crudo para siempre. Se dispara cuando se acumulan SUMMARY_BATCH_SIZE
# mensajes "viejos" sin resumir todavía.
SUMMARY_BATCH_SIZE = 6
SUMMARY_MAX_CHARS = 600

# Si se define, descarta recuerdos cuya distancia (ChromaDB) sea mayor que este valor
# — es decir, poco relevantes para la pregunta actual. Sin definir, se usan siempre
# los MEMORY_RESULTS más cercanos. Mira "Memory candidate distances" en el log para
# calibrar un valor razonable en tu caso.
_max_distance_env = os.getenv("MEMORY_MAX_DISTANCE")
MEMORY_MAX_DISTANCE = float(_max_distance_env) if _max_distance_env else None

active_conversation = {}   # user_id -> conversation_id (en RAM, se pierde al reiniciar el bot)
pending_reminders = {}     # simple, en RAM

def truncate(text, max_chars):
    text = text.strip()
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "…"

# ---------- /start: elegir nueva o continuar ----------

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

# ---------- Recordatorios ----------

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    logger.info(f"Sending reminder to chat {job.chat_id}")
    await context.bot.send_message(chat_id=job.chat_id, text=job.data)

# ---------- Resumen progresivo ----------

async def maybe_update_summary(conv_id):
    total = count_messages(conv_id)
    existing_summary, summarized_up_to = get_conversation_summary_state(conv_id)
    foldable = total - HISTORY_MESSAGES - summarized_up_to

    if foldable < SUMMARY_BATCH_SIZE:
        return

    to_fold = get_messages_range(conv_id, offset=summarized_up_to, limit=foldable)
    transcript = "\n".join(f"{r}: {c}" for r, c in to_fold)

    summary_prompt = f"""Condensa en pocas frases, en tercera persona, lo esencial de este fragmento \
de conversación entre un usuario y un asistente. Si hay un resumen previo, intégralo sin repetirlo.

Resumen previo: {existing_summary or "(ninguno)"}

Fragmento a condensar:
{transcript}

Resumen actualizado (máximo 5-6 frases):"""

    try:
        resp = requests.post(f"{OLLAMA_URL}/api/generate", json={
            "model": CHAT_MODEL, "prompt": summary_prompt, "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE
        })
        resp.raise_for_status()
        new_summary = resp.json()["response"].strip()
        update_conversation_summary(conv_id, new_summary, summarized_up_to + foldable)
        logger.info(f"Updated summary for conversation {conv_id} (folded {foldable} messages)")
    except Exception:
        logger.error(f"Failed to update summary for conversation {conv_id}", exc_info=True)

# ---------- Mensajes normales ----------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    # Si no hay conversación activa, obliga a elegir primero
    if user_id not in active_conversation:
        await start(update, context)
        return

    conv_id = active_conversation[user_id]
    logger.info(f"User {user_id} sent a message in conversation {conv_id} ({len(user_text)} chars)")

    # Detección simple de recordatorio (2 minutos fijos, mejorable)
    if "recuérdame" in user_text.lower() or "recordatorio" in user_text.lower():
        context.job_queue.run_once(
            send_reminder, when=120,
            chat_id=update.effective_chat.id,
            data="⏰ Recordatorio: " + user_text
        )
        logger.info(f"Reminder scheduled for chat {update.effective_chat.id} in 120s")
        await update.message.reply_text("Vale, te lo recuerdo en 2 minutos.")
        return

    # Memoria semántica (RAG) — busca en TODO el historial del usuario, no solo esta conversación,
    # y descarta los recuerdos cuya distancia supere MEMORY_MAX_DISTANCE (si está configurado)
    relevant = search_memory(user_id, user_text, k=MEMORY_RESULTS, max_distance=MEMORY_MAX_DISTANCE)
    memory_block = "\n".join(truncate(m, MEMORY_SNIPPET_CHARS) for m in relevant) \
        if relevant else "Sin recuerdos relevantes."

    # Ventana reciente — solo los últimos turnos de ESTA conversación (acotado, no crece sin límite)
    recent = get_recent_messages(conv_id, limit=HISTORY_MESSAGES)
    history_block = "\n".join(f"{r}: {truncate(c, HISTORY_SNIPPET_CHARS)}" for r, c in recent) \
        if recent else "Inicio de la conversación."

    # Resumen progresivo de lo que ya quedó fuera de la ventana reciente
    conv_summary, _ = get_conversation_summary_state(conv_id)
    summary_block = truncate(conv_summary, SUMMARY_MAX_CHARS) if conv_summary else "Sin resumen todavía."

    fecha_actual = datetime.now().strftime("%A %d de %B de %Y, %H:%M")

    prompt = f"""Eres un asistente personal. Hoy es {fecha_actual}.

Recuerdos relevantes de conversaciones pasadas (puede que no todos apliquen):
{memory_block}

Resumen de lo hablado anteriormente en esta conversación (antes de la ventana reciente):
{summary_block}

Conversación reciente (esto es lo más importante para el contexto inmediato):
{history_block}

Mensaje actual del usuario: {user_text}

{RESPONSE_STYLE_TEXT} Usa el contexto reciente antes que los recuerdos antiguos si hay conflicto."""

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
    add_memory(user_id, f"Usuario dijo: {truncate(user_text, MEMORY_STORE_CHARS)}")
    add_memory(user_id, f"Tú respondiste: {truncate(answer, MEMORY_STORE_CHARS)}")

    logger.info(f"Replied to user {user_id} in conversation {conv_id}")
    await update.message.reply_text(answer)

    await maybe_update_summary(conv_id)

# ---------- Arranque ----------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception while processing an update", exc_info=context.error)

app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(announce_start_menu).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(handle_button))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_error_handler(error_handler)

logger.info(f"Starting Asimov bot with model {CHAT_MODEL}")
app.run_polling()
