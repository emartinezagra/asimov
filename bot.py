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
    create_conversation, list_conversations, set_title_if_missing,
    add_message, get_recent_messages
)

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.2:3b")          # cambia esto por el modelo que mejor te fue
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Falta TELEGRAM_TOKEN. Definilo en un archivo .env (ver .env.example).")

active_conversation = {}   # user_id -> conversation_id (en RAM, se pierde al reiniciar el bot)
pending_reminders = {}     # simple, en RAM

# ---------- /start: elegir nueva o continuar ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversations = list_conversations(user_id, limit=5)

    buttons = [[InlineKeyboardButton("🆕 Nueva conversación", callback_data="new")]]
    for conv_id, title, created_at in conversations:
        label = title if title else f"(vacía) {created_at[:16]}"
        buttons.append([InlineKeyboardButton(f"📂 {label}", callback_data=f"load:{conv_id}")])

    await update.message.reply_text(
        "¿Quieres empezar una conversación nueva o continuar una anterior?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "new":
        conv_id = create_conversation(user_id)
        active_conversation[user_id] = conv_id
        await query.edit_message_text("Conversación nueva iniciada. Escríbeme cuando quieras.")
    elif query.data.startswith("load:"):
        conv_id = query.data.split(":", 1)[1]
        active_conversation[user_id] = conv_id
        await query.edit_message_text("Conversación anterior cargada. Sigamos donde lo dejamos.")

# ---------- Recordatorios ----------

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    await context.bot.send_message(chat_id=job.chat_id, text=job.data)

# ---------- Mensajes normales ----------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    # Si no hay conversación activa, obliga a elegir primero
    if user_id not in active_conversation:
        await start(update, context)
        return

    conv_id = active_conversation[user_id]

    # Detección simple de recordatorio (2 minutos fijos, mejorable)
    if "recuérdame" in user_text.lower() or "recordatorio" in user_text.lower():
        context.job_queue.run_once(
            send_reminder, when=120,
            chat_id=update.effective_chat.id,
            data="⏰ Recordatorio: " + user_text
        )
        await update.message.reply_text("Vale, te lo recuerdo en 2 minutos.")
        return

    # Memoria semántica (RAG) — busca en TODO el historial del usuario, no solo esta conversación
    relevant = search_memory(user_id, user_text, k=5)
    memory_block = "\n".join(relevant) if relevant else "Sin recuerdos relevantes."

    # Ventana reciente — solo los últimos turnos de ESTA conversación (acotado, no crece sin límite)
    recent = get_recent_messages(conv_id, limit=8)
    history_block = "\n".join(f"{r}: {c}" for r, c in recent) if recent else "Inicio de la conversación."

    fecha_actual = datetime.now().strftime("%A %d de %B de %Y, %H:%M")

    prompt = f"""Eres un asistente personal. Hoy es {fecha_actual}.

Recuerdos relevantes de conversaciones pasadas (puede que no todos apliquen):
{memory_block}

Conversación reciente (esto es lo más importante para el contexto inmediato):
{history_block}

Mensaje actual del usuario: {user_text}

Responde de forma natural y breve, usando el contexto reciente antes que los recuerdos antiguos si hay conflicto."""

    resp = requests.post(f"{OLLAMA_URL}/api/generate", json={
        "model": CHAT_MODEL, "prompt": prompt, "stream": False
    })
    answer = resp.json()["response"]

    set_title_if_missing(conv_id, user_text)
    add_message(conv_id, "Usuario", user_text)
    add_message(conv_id, "Tú", answer)
    add_memory(user_id, f"Usuario dijo: {user_text}")
    add_memory(user_id, f"Tú respondiste: {answer}")

    await update.message.reply_text(answer)

# ---------- Arranque ----------

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(handle_button))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()
