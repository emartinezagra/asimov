import os
import json
import logging

import db
import llm
import context
import datetime_utils as dtu
from tools import reminders as reminders_tool
from tools import calendar as calendar_tool
from tools import email as email_tool
from tools import notes as notes_tool
from tools import tasks as tasks_tool
from tools import memory as memory_tool
from tools import weather as weather_tool
from tools import websearch as websearch_tool

logger = logging.getLogger("asimov")

DEFAULT_LOCATION = os.getenv("DEFAULT_LOCATION", "").strip()

ALLOWED_ACTIONS = {
    "CHAT", "REMINDER", "CANCEL_REMINDER", "LIST_REMINDERS",
    "CALENDAR", "CANCEL_EVENT", "LIST_EVENTS",
    "EMAIL_DRAFT", "SEND_EMAIL",
    "NOTE", "SEARCH_NOTES", "LIST_NOTES", "DELETE_NOTE",
    "TASK", "COMPLETE_TASK", "CANCEL_TASK", "LIST_TASKS",
    "REMEMBER", "FORGET", "LIST_MEMORY",
    "WEATHER", "WEB_SEARCH",
}

FALLBACK_MESSAGE = "No te he entendido bien. ¿Puedes reformularlo?"


def parse_action_json(raw_text):
    """Parses and whitelists the model's raw output. Never trusted blindly:
    invalid JSON or an unknown action always falls back to plain CHAT, so a
    malformed response can never be confused with — or block — a real action."""
    logger.debug(f"Raw action JSON from model: {raw_text!r}")

    if not raw_text:
        logger.error("Empty response from model, falling back to CHAT")
        return {"action": "CHAT", "message": FALLBACK_MESSAGE}
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        logger.error(f"Invalid JSON from model, falling back to CHAT: {raw_text!r}")
        return {"action": "CHAT", "message": FALLBACK_MESSAGE}

    data = _normalize_action_shape(data)

    if not isinstance(data, dict) or data.get("action") not in ALLOWED_ACTIONS:
        logger.error(f"Unknown/missing action from model, falling back to CHAT: {data!r}")
        return {"action": "CHAT", "message": FALLBACK_MESSAGE}

    if data.get("action") == "CHAT" and not (isinstance(data.get("message"), str) and data["message"].strip()):
        logger.error(f"CHAT action with no usable message from model, falling back: {data!r}")
        return {"action": "CHAT", "message": FALLBACK_MESSAGE}

    return data


def _normalize_action_shape(data):
    """Recovers from a common small-model mistake: wrapping the action name
    as a key instead of putting it under "action", e.g.
    {"CHAT": {"message": "..."}} instead of {"action": "CHAT", "message": "..."}.
    Only touches that exact single-key shape — anything else is left alone
    for the normal validation path to reject."""
    if not isinstance(data, dict) or "action" in data or len(data) != 1:
        return data
    key, value = next(iter(data.items()))
    if key in ALLOWED_ACTIONS and isinstance(value, dict):
        return {"action": key, **value}
    return data


# ---------- Per-action parameter validation (never trust the LLM's values) ----------

def validate_reminder(raw):
    missing = []
    text = raw.get("text")
    if not (isinstance(text, str) and text.strip()):
        missing.append("text")
        text = None

    execute_at = None
    if raw.get("delay_seconds") is not None:
        execute_at = dtu.validate_delay_seconds(raw.get("delay_seconds"))
    elif raw.get("datetime"):
        execute_at = dtu.validate_absolute_datetime(raw.get("datetime"))
    if not execute_at:
        missing.append("datetime")

    return {"text": text.strip() if text else None, "execute_at": execute_at}, missing


def validate_cancel_reminder(raw):
    text = raw.get("text")
    if not (isinstance(text, str) and text.strip()):
        return {}, ["text"]
    return {"text": text.strip()}, []


def validate_calendar(raw):
    missing = []
    title = raw.get("title")
    if not (isinstance(title, str) and title.strip()):
        missing.append("title")
        title = None

    start = dtu.validate_absolute_datetime(raw.get("start")) if raw.get("start") else None
    if not start:
        missing.append("start")

    try:
        duration = int(raw.get("duration_minutes"))
        if duration <= 0 or duration > 24 * 60:
            duration = 30
    except (TypeError, ValueError):
        duration = 30

    attendees = raw.get("attendees") or []
    if not isinstance(attendees, list):
        attendees = [str(attendees)]
    attendees = [str(a).strip() for a in attendees if str(a).strip()]

    return {
        "title": title.strip() if title else None, "start": start,
        "duration_minutes": duration, "attendees": attendees,
    }, missing


def validate_cancel_event(raw):
    title = raw.get("title")
    if not (isinstance(title, str) and title.strip()):
        return {}, ["title"]
    return {"title": title.strip()}, []


def truncate_subject(body):
    if not body:
        return "Sin asunto"
    return body[:40] + ("..." if len(body) > 40 else "")


def validate_email_draft(raw):
    missing = []
    to_field = raw.get("to")
    email_addr, display_name = email_tool.resolve_recipient(to_field) if to_field else (None, None)
    if not email_addr:
        missing.append("to")

    body = raw.get("body")
    if not (isinstance(body, str) and body.strip()):
        missing.append("body")
        body = None

    subject = raw.get("subject")
    if not (isinstance(subject, str) and subject.strip()):
        subject = truncate_subject(body)

    return {
        "to_field": to_field, "to_email": email_addr, "to_name": display_name,
        "subject": subject, "body": body.strip() if body else None,
    }, missing


def validate_note(raw):
    content = raw.get("content")
    if not (isinstance(content, str) and content.strip()):
        return {}, ["content"]
    return {"content": content.strip()}, []


def validate_search_notes(raw):
    query = raw.get("query")
    if not (isinstance(query, str) and query.strip()):
        return {}, ["query"]
    return {"query": query.strip()}, []


def validate_delete_note(raw):
    text = raw.get("text")
    if not (isinstance(text, str) and text.strip()):
        return {}, ["text"]
    return {"text": text.strip()}, []


def validate_task(raw):
    missing = []
    title = raw.get("title")
    if not (isinstance(title, str) and title.strip()):
        missing.append("title")
        title = None

    # due_at is optional for a task — an invalid/missing date just means no
    # due date, it never blocks creating the task itself.
    due_at = dtu.validate_absolute_datetime(raw.get("due_at")) if raw.get("due_at") else None

    return {"title": title.strip() if title else None, "due_at": due_at}, missing


def validate_task_title(raw):
    """Shared by COMPLETE_TASK and CANCEL_TASK — both only need a title fragment."""
    title = raw.get("title")
    if not (isinstance(title, str) and title.strip()):
        return {}, ["title"]
    return {"title": title.strip()}, []


def validate_remember(raw):
    fact = raw.get("fact")
    if not (isinstance(fact, str) and fact.strip()):
        return {}, ["fact"]
    return {"fact": fact.strip()}, []


def validate_forget(raw):
    text = raw.get("text")
    if not (isinstance(text, str) and text.strip()):
        return {}, ["text"]
    return {"text": text.strip()}, []


def validate_weather(raw):
    location = raw.get("location") or DEFAULT_LOCATION
    missing = [] if location else ["location"]

    date_str = raw.get("date")
    resolved_date = dtu.validate_date(date_str) if date_str else dtu.today_date_str()
    if date_str and not resolved_date:
        # An unusable date shouldn't block the whole request — just fall back to today.
        resolved_date = dtu.today_date_str()

    return {"location": location, "date": resolved_date}, missing


def validate_web_search(raw):
    query = raw.get("query")
    if not (isinstance(query, str) and query.strip()):
        return {}, ["query"]
    return {"query": query.strip()}, []


VALIDATORS = {
    "REMINDER": validate_reminder,
    "CANCEL_REMINDER": validate_cancel_reminder,
    "CALENDAR": validate_calendar,
    "CANCEL_EVENT": validate_cancel_event,
    "EMAIL_DRAFT": validate_email_draft,
    "NOTE": validate_note,
    "SEARCH_NOTES": validate_search_notes,
    "DELETE_NOTE": validate_delete_note,
    "TASK": validate_task,
    "COMPLETE_TASK": validate_task_title,
    "CANCEL_TASK": validate_task_title,
    "REMEMBER": validate_remember,
    "FORGET": validate_forget,
    "WEATHER": validate_weather,
    "WEB_SEARCH": validate_web_search,
}

# ---------- Missing-field questions (templated in Python, no extra LLM call) ----------

MISSING_FIELD_QUESTIONS = {
    ("REMINDER", "text"): "¿Qué quieres que te recuerde?",
    ("REMINDER", "datetime"): "¿Cuándo quieres que te lo recuerde?",
    ("CANCEL_REMINDER", "text"): "¿Cuál recordatorio quieres cancelar?",
    ("CALENDAR", "title"): "¿Cómo quieres llamar al evento?",
    ("CALENDAR", "start"): "¿Para qué día y hora?",
    ("CANCEL_EVENT", "title"): "¿Qué evento quieres cancelar?",
    ("EMAIL_DRAFT", "body"): "¿Qué quieres decirle?",
    ("NOTE", "content"): "¿Qué quieres que apunte?",
    ("SEARCH_NOTES", "query"): "¿Qué buscas en tus notas?",
    ("DELETE_NOTE", "text"): "¿Qué nota quieres borrar?",
    ("TASK", "title"): "¿Qué tarea quieres añadir?",
    ("COMPLETE_TASK", "title"): "¿Qué tarea quieres marcar como hecha?",
    ("CANCEL_TASK", "title"): "¿Qué tarea quieres eliminar?",
    ("REMEMBER", "fact"): "¿Qué quieres que recuerde?",
    ("FORGET", "text"): "¿Qué quieres que olvide?",
    ("WEATHER", "location"): "¿De qué ciudad quieres saber el tiempo?",
    ("WEB_SEARCH", "query"): "¿Qué quieres que busque?",
}

def missing_field_question(action, missing_fields, params):
    field = missing_fields[0]
    if action == "EMAIL_DRAFT" and field == "to":
        name = params.get("to_field") or "esa persona"
        return f"No tengo el correo de {name}. ¿Cuál es su dirección?"
    return MISSING_FIELD_QUESTIONS.get((action, field), "Me falta un dato, ¿puedes darme más detalles?")


# ---------- Execution (deterministic Python; the model never runs anything) ----------

def run_action(action, params, user_id, conv_id):
    if action == "REMINDER":
        result = reminders_tool.create(user_id, params["text"], params["execute_at"])
        when = dtu.human_time(result["execute_at"])
        return f"Hecho, te lo recordaré {when}: {result['text']}."

    if action == "CANCEL_REMINDER":
        result = reminders_tool.cancel(user_id, params["text"])
        if result["status"] == "cancelled":
            return f"Recordatorio cancelado: {result['text']}."
        if result["status"] == "ambiguous":
            return f"Tienes varios recordatorios que coinciden: {'; '.join(result['matches'])}. ¿Cuál exactamente?"
        return "No he encontrado ningún recordatorio con ese texto."

    if action == "LIST_REMINDERS":
        items = reminders_tool.list_all(user_id)
        if not items:
            return "No tienes recordatorios pendientes."
        lines = [f"- {i['text']} ({dtu.human_time(i['execute_at'])})" for i in items]
        return "Tus recordatorios pendientes:\n" + "\n".join(lines)

    if action == "CALENDAR":
        result = calendar_tool.create_event(
            user_id, params["title"], params["start"], params["duration_minutes"], params["attendees"]
        )
        when = dtu.human_time(result["start"])
        return f"Evento creado: {result['title']}, {when} ({result['duration_minutes']} min)."

    if action == "CANCEL_EVENT":
        result = calendar_tool.cancel_event(user_id, params["title"])
        if result["status"] == "cancelled":
            return f"Evento cancelado: {result['title']}."
        if result["status"] == "ambiguous":
            return f"Tienes varios eventos que coinciden: {'; '.join(result['matches'])}. ¿Cuál exactamente?"
        return "No he encontrado ningún evento con ese título."

    if action == "LIST_EVENTS":
        items = calendar_tool.list_events(user_id)
        if not items:
            return "No tienes eventos programados."
        lines = [f"- {i['title']}, {dtu.human_time(i['start'])} ({i['duration_minutes']} min)" for i in items]
        return "Tus próximos eventos:\n" + "\n".join(lines)

    if action == "EMAIL_DRAFT":
        result = email_tool.create_draft(user_id, params["to_field"], params["subject"], params["body"])
        if result.get("status") == "unknown_contact":
            return f"No tengo el correo de {result['to_name']}. ¿Cuál es su dirección?"
        db.set_pending_action(conv_id, json.dumps({
            "action": "SEND_EMAIL_CONFIRM", "params": {"draft_id": result["id"]}
        }))
        return (
            f"He preparado este correo:\n\nPara: {result['to_name']}\nAsunto: {result['subject']}\n\n"
            f"{result['body']}\n\n¿Quieres que lo envíe?"
        )

    if action == "SEND_EMAIL":
        return "No hay ningún borrador de correo listo para enviar. Pídeme primero que lo prepare."

    if action == "NOTE":
        result = notes_tool.create(user_id, params["content"])
        return f"Apuntado: {result['content']}"

    if action == "SEARCH_NOTES":
        items = notes_tool.search(user_id, params["query"])
        if not items:
            return "No he encontrado notas sobre eso."
        return "Notas encontradas:\n" + "\n".join(f"- {i['content']}" for i in items)

    if action == "LIST_NOTES":
        items = notes_tool.list_all(user_id)
        if not items:
            return "No tienes notas guardadas."
        return "Tus notas:\n" + "\n".join(f"- {i['content']}" for i in items)

    if action == "DELETE_NOTE":
        result = notes_tool.delete(user_id, params["text"])
        if result["status"] == "deleted":
            return f"Nota eliminada: {result['content']}"
        if result["status"] == "ambiguous":
            return f"Tienes varias notas que coinciden: {'; '.join(result['matches'])}. ¿Cuál exactamente?"
        return "No he encontrado ninguna nota con ese texto."

    if action == "TASK":
        result = tasks_tool.create(user_id, params["title"], params["due_at"])
        if result["due_at"]:
            return f"Hecho. He añadido \"{result['title']}\" a tus tareas, para {dtu.human_time(result['due_at'])}. 📋"
        return f"Hecho. He añadido \"{result['title']}\" a tus tareas. 📋"

    if action == "COMPLETE_TASK":
        result = tasks_tool.complete(user_id, params["title"])
        if result["status"] == "completed":
            return f"Tarea completada: {result['title']}."
        if result["status"] == "ambiguous":
            return f"Tienes varias tareas que coinciden: {'; '.join(result['matches'])}. ¿Cuál exactamente?"
        return "No he encontrado ninguna tarea pendiente con ese texto."

    if action == "CANCEL_TASK":
        result = tasks_tool.cancel(user_id, params["title"])
        if result["status"] == "cancelled":
            return f"Tarea eliminada: {result['title']}."
        if result["status"] == "ambiguous":
            return f"Tienes varias tareas que coinciden: {'; '.join(result['matches'])}. ¿Cuál exactamente?"
        return "No he encontrado ninguna tarea con ese texto."

    if action == "LIST_TASKS":
        items = tasks_tool.list_pending(user_id)
        if not items:
            return "No tienes tareas pendientes."
        lines = []
        for i in items:
            suffix = f" (para {dtu.human_time(i['due_at'])})" if i["due_at"] else ""
            lines.append(f"- {i['title']}{suffix}")
        return "Tus tareas pendientes:\n" + "\n".join(lines)

    if action == "REMEMBER":
        result = memory_tool.remember(user_id, params["fact"])
        if result["status"] == "refused_sensitive":
            return "Prefiero no guardar ese tipo de información (contraseñas, claves o datos sensibles)."
        if result["status"] == "refused_malformed":
            return "Eso no parece un hecho concreto que pueda guardar. ¿Puedes decírmelo de forma más directa?"
        return "Lo recordaré. 🧠"

    if action == "FORGET":
        result = memory_tool.forget(user_id, params["text"])
        if result["status"] == "deleted":
            return f"Olvidado: {result['fact']}."
        if result["status"] == "ambiguous":
            return f"Tengo varias cosas que coinciden: {'; '.join(result['matches'])}. ¿Cuál exactamente?"
        return "No tengo guardado nada parecido a eso."

    if action == "LIST_MEMORY":
        facts = memory_tool.list_all(user_id)
        if not facts:
            return "Todavía no tengo nada guardado sobre ti."
        return "Esto es lo que recuerdo de ti:\n" + "\n".join(f"- {f}" for f in facts)

    if action == "WEATHER":
        result = weather_tool.get_weather(params["location"], params["date"])
        if result["status"] == "location_not_found":
            return f"No he encontrado la ubicación \"{result['location']}\"."
        if result["status"] != "ok":
            return "No he podido consultar el tiempo ahora mismo. Inténtalo de nuevo en un momento."
        when = dtu.human_date(result["date"])
        return (
            f"En {result['location']} {when}: {result['condition']}, "
            f"{result['temp_min']:.0f}–{result['temp_max']:.0f}°C, "
            f"{result['rain_probability']}% de probabilidad de lluvia."
        )

    if action == "WEB_SEARCH":
        result = websearch_tool.search(params["query"])
        if result["status"] == "not_configured":
            return "No tengo la búsqueda web configurada todavía."
        if result["status"] == "no_results":
            return "No he encontrado nada sobre eso."
        if result["status"] != "ok":
            return "No he podido buscar eso ahora mismo. Inténtalo de nuevo en un momento."
        return _summarize_search_results(params["query"], result["results"])

    return FALLBACK_MESSAGE


def _summarize_search_results(query, results):
    # The one deliberate exception to "never a second LLM call": raw search
    # snippets need language understanding to turn into an answer, which a
    # fixed template can't do. Python still controls the actual network
    # access — the model only ever sees these compact, already-fetched
    # title/url/snippet triples, never the open internet.
    lines = [f"- {r['title']}: {r['snippet']} ({r['url']})" for r in results]
    prompt = f"""El usuario preguntó: "{query}"

Estos son los resultados de una búsqueda web:
{chr(10).join(lines)}

Responde a la pregunta del usuario de forma natural y breve, basándote SOLO en estos resultados. \
Si citas una fuente, menciona el título, no la URL completa. Si los resultados no responden la \
pregunta, dilo claramente en vez de inventar."""
    text, _ = llm.generate(prompt, log_label="websearch-summarize")
    return text or "He encontrado resultados pero no he podido resumirlos. Inténtalo de nuevo."


def _handle_send_confirmation(user_id, conv_id, user_text, params):
    draft_id = params.get("draft_id")
    confirm_prompt = (
        "El usuario tiene un correo listo para enviar y se le preguntó si quiere que se envíe. "
        "¿Este mensaje confirma el envío? Responde solo SI o NO.\n\n"
        f"Mensaje: {user_text}"
    )
    raw, _ = llm.generate(confirm_prompt, log_label=f"send-confirm:{conv_id}")
    confirmed = bool(raw) and raw.strip().upper().startswith("SI")

    db.clear_pending_action(conv_id)

    if not confirmed:
        return "Vale, no lo envío. El borrador se queda guardado; dime cuándo quieras que lo mande."

    result = email_tool.send(user_id, draft_id)
    if result["sent"]:
        return f"Correo enviado a {result['to_email']}."
    errors = {
        "draft_not_found": "No encuentro ese borrador.",
        "already_sent": "Ese correo ya se había enviado.",
        "smtp_not_configured": "No tengo configurado el envío de correo en este servidor.",
        "smtp_error": "Ha habido un error enviando el correo. Inténtalo de nuevo.",
    }
    return errors.get(result["error"], "No se pudo enviar el correo.")


def _continue_pending(user_id, conv_id, user_text, pending):
    action = pending["action"]

    if action == "SEND_EMAIL_CONFIRM":
        return _handle_send_confirmation(user_id, conv_id, user_text, pending["params"])

    missing = pending.get("missing") or []
    field = missing[0] if missing else None
    if not field:
        db.clear_pending_action(conv_id)
        return FALLBACK_MESSAGE

    extraction_prompt = f"""{dtu.current_datetime_label()}
El usuario tiene una acción pendiente ({action}) y le falta el dato "{field}". Extrae SOLO ese dato \
de este mensaje. Responde con un único JSON: {{"{field}": <valor>}}. Si el mensaje no lo aclara, \
responde: {{"{field}": null}}.

Mensaje: {user_text}"""

    raw, _ = llm.generate(extraction_prompt, json_mode=True, log_label=f"pending-fill:{conv_id}")
    try:
        extracted = json.loads(raw) if raw else {}
        if not isinstance(extracted, dict):
            extracted = {}
    except (json.JSONDecodeError, TypeError):
        extracted = {}

    raw_params = dict(pending.get("raw_params") or {})
    raw_params[field] = extracted.get(field)

    validator = VALIDATORS.get(action)
    params, still_missing = validator(raw_params) if validator else ({}, [])

    if still_missing:
        question = missing_field_question(action, still_missing, params)
        db.set_pending_action(conv_id, json.dumps({
            "action": action, "raw_params": raw_params, "missing": still_missing
        }))
        return question

    db.clear_pending_action(conv_id)
    return run_action(action, params, user_id, conv_id)


def handle_user_message(user_id, conv_id, user_text):
    """Main entry point: returns the reply text to send back to the user.
    Handles an in-progress pending action first; otherwise classifies the
    fresh message with a single Ollama call."""
    pending_raw = db.get_pending_action(conv_id)
    if pending_raw:
        try:
            pending = json.loads(pending_raw)
        except (json.JSONDecodeError, TypeError):
            pending = None
        if pending:
            return _continue_pending(user_id, conv_id, user_text, pending)
        db.clear_pending_action(conv_id)

    prompt = context.build_action_prompt(user_id, conv_id, user_text)
    logger.debug(f"Action prompt ({len(prompt)} chars) for conversation {conv_id}:\n{prompt}")
    raw, _ = llm.generate(prompt, json_mode=True, log_label=f"action-classify:{conv_id}")
    action_data = parse_action_json(raw)
    action = action_data["action"]

    if action == "CHAT":
        return action_data.get("message") or FALLBACK_MESSAGE

    validator = VALIDATORS.get(action)
    if not validator:
        return run_action(action, {}, user_id, conv_id)

    params, missing = validator(action_data)
    declared_missing = action_data.get("missing") or []
    for f in declared_missing:
        if isinstance(f, str) and f not in missing:
            missing.append(f)

    if missing:
        question = missing_field_question(action, missing, params)
        db.set_pending_action(conv_id, json.dumps({
            "action": action, "raw_params": action_data, "missing": missing
        }))
        return question

    return run_action(action, params, user_id, conv_id)
