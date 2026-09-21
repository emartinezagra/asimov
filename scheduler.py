import logging

import db
import datetime_utils as dtu

logger = logging.getLogger("asimov")

CHECK_INTERVAL_SECONDS = 30


async def check_due_reminders(context):
    due = db.get_due_reminders(dtu.now().isoformat())
    for reminder_id, user_id, text in due:
        try:
            await context.bot.send_message(chat_id=user_id, text=f"⏰ Recordatorio\n\n{text}")
            db.mark_reminder_completed(reminder_id)
            logger.info(f"Reminder {reminder_id} delivered to user {user_id}")
        except Exception:
            logger.error(f"Failed to deliver reminder {reminder_id} to user {user_id}", exc_info=True)


def setup(app):
    """Registers the periodic due-reminder check. Reminders live in SQLite,
    so this survives restarts: on startup it just resumes polling, and any
    reminder that was already due gets delivered on the first tick."""
    app.job_queue.run_repeating(check_due_reminders, interval=CHECK_INTERVAL_SECONDS, first=5)
    logger.info(f"Reminder scheduler started (checking every {CHECK_INTERVAL_SECONDS}s)")
