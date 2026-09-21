import os
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler,
    CallbackQueryHandler, ContextTypes, filters
)

load_dotenv()

from logging_config import setup_logging
logger = setup_logging()

import db
import context
import actions
import scheduler

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN. Set it in a .env file (see .env.example).")
CHAT_MODEL = os.getenv("CHAT_MODEL", "llama3.2:3b")

active_conversation = {}   # user_id -> conversation_id (in RAM, lost on bot restart)

# ---------- /start: choose new or continue ----------

def build_start_menu(user_id):
    conversations = db.list_conversations(user_id, limit=5)
    buttons = [[InlineKeyboardButton("🆕 Nueva conversación", callback_data="new")]]
    for conv_id, title, created_at in conversations:
        label = title if title else f"(vacía) {created_at[:16]}"
        buttons.append([InlineKeyboardButton(f"📂 {label}", callback_data=f"load:{conv_id}")])
    return InlineKeyboardMarkup(buttons)

async def start(update: Update, context_: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(f"User {user_id} opened /start")
    await update.message.reply_text(
        "¿Quieres empezar una conversación nueva o continuar una anterior?",
        reply_markup=build_start_menu(user_id)
    )

async def announce_start_menu(application):
    for user_id in db.list_known_users():
        try:
            await application.bot.send_message(
                chat_id=user_id,
                text="El bot se ha reiniciado. ¿Quieres empezar una conversación nueva o continuar una anterior?",
                reply_markup=build_start_menu(user_id)
            )
            logger.info(f"Sent startup menu to user {user_id}")
        except Exception:
            logger.error(f"Failed to send startup menu to user {user_id}", exc_info=True)

async def handle_button(update: Update, context_: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "new":
        conv_id = db.create_conversation(user_id)
        active_conversation[user_id] = conv_id
        logger.info(f"User {user_id} started new conversation {conv_id}")
        await query.edit_message_text("Conversación nueva iniciada. Escríbeme cuando quieras.")
    elif query.data.startswith("load:"):
        conv_id = query.data.split(":", 1)[1]
        active_conversation[user_id] = conv_id
        logger.info(f"User {user_id} loaded conversation {conv_id}")
        await query.edit_message_text("Conversación anterior cargada. Sigamos donde lo dejamos.")

# ---------- Regular messages ----------

async def handle_message(update: Update, context_: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    # No active conversation: force the user to pick one first
    if user_id not in active_conversation:
        await start(update, context_)
        return

    conv_id = active_conversation[user_id]
    logger.info(f"User {user_id} sent a message in conversation {conv_id} ({len(user_text)} chars)")

    try:
        answer = actions.handle_user_message(user_id, conv_id, user_text)
    except Exception:
        logger.error(f"Failed to process message for conversation {conv_id}", exc_info=True)
        await update.message.reply_text("Ha ocurrido un error procesando tu mensaje. Inténtalo de nuevo.")
        return

    db.set_title_if_missing(conv_id, user_text)
    db.add_message(conv_id, "Usuario", user_text)
    db.add_message(conv_id, "Tú", answer)

    logger.info(f"Replied to user {user_id} in conversation {conv_id}")
    await update.message.reply_text(answer)

    context.maybe_update_state_and_facts(conv_id, user_id)

# ---------- Startup ----------

async def error_handler(update: object, context_: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception while processing an update", exc_info=context_.error)

app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(announce_start_menu).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(handle_button))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_error_handler(error_handler)

scheduler.setup(app)

logger.info(f"Starting Asimov bot with model {CHAT_MODEL}")
app.run_polling()
