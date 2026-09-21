# Asimov

Telegram bot with structured persistent memory and progressive conversation summarization (all in SQLite), designed to run locally against [Ollama](https://ollama.com) or any Ollama-compatible LLM API, and optimized for the best possible quality with the minimum context tokens — built with small models like Llama 3.2 3B specifically in mind.

## Quick install (recommended)

Only needs Python 3 and `curl`. The installer handles the rest: installs Ollama if missing, detects the machine's RAM/CPU/GPU to pick a starting model, and shows you the real response time of each one, asking whether you want to try a lighter one.

```bash
git clone https://github.com/emartinezagra/asimov.git
cd asimov
./install.sh
```

It will ask you to choose the bot's response style, your Telegram token (from [@BotFather](https://t.me/BotFather)), and at the end whether you want to register it as a `systemd` service so it starts on its own.

> The automatic installer (`install.sh`/`install.py`) only supports **Linux** for now. For Windows/Mac, follow the manual installation below.

## Manual installation

### Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running locally (`http://127.0.0.1:11434` by default), with the chat model you'll use:
  ```bash
  ollama pull llama3.2:3b
  ```
- A Telegram bot token (get one by talking to [@BotFather](https://t.me/BotFather))

### Steps

```bash
git clone https://github.com/emartinezagra/asimov.git
cd asimov

python3 -m venv venv
source venv/bin/activate      # on Windows: venv\Scripts\activate

pip install -r requirements.txt
```

Copy the environment variable template and fill in your token:

```bash
cp .env.example .env
```

Variables available in `.env`:

| Variable | Description | Default |
|---|---|---|
| `TELEGRAM_TOKEN` | Bot token (BotFather) — **required** | — |
| `OLLAMA_URL` | Ollama server URL | `http://127.0.0.1:11434` |
| `OLLAMA_KEEP_ALIVE` | How long Ollama keeps the model loaded in RAM after each use (`-1` = never unload) | `30m` |
| `CHAT_MODEL` | Chat model to use | `llama3.2:3b` |
| `RESPONSE_STYLE` | Response style: `brief`, `technical`, or `balanced` | `balanced` |
| `DB_PATH` | Path to the conversations/state/memory SQLite file | `./conversations.db` |
| `TIMEZONE` | IANA timezone used to resolve dates/times ("tomorrow at 9", "in 10 minutes"...) | `Europe/Madrid` |
| `CONTACTS` | `Name:email` pairs, comma-separated, so emails can be sent by first name | (empty) |
| `EMAIL_SMTP_HOST` / `EMAIL_SMTP_PORT` / `EMAIL_USER` / `EMAIL_PASSWORD` / `EMAIL_FROM` | SMTP credentials for sending email. Leave `EMAIL_SMTP_HOST` empty to disable sending | (empty) |

`.env` is **not** pushed to the repo (it's in `.gitignore`) — each person uses their own token.

## Running it

```bash
source venv/bin/activate
python bot.py
```

`conversations.db` is created automatically on first run; it isn't versioned either, since it's runtime-generated data (conversation history, state, and memory).

To keep it running after closing the SSH session without `systemd`, you can use `tmux` or `screen`:

```bash
tmux new -s asimov
source venv/bin/activate
python bot.py
# Ctrl+B, D to detach while leaving it running
```

## How the installer picks a model

`install.py` first detects RAM, CPU core count, and whether there's an NVIDIA GPU, and uses that to pick a starting model:

| Model | Rough minimum RAM |
|---|---|
| `llama3.1:8b` | 16 GB |
| `mistral:7b` | 12 GB |
| `llama3.2:3b` | 6 GB |
| `llama3.2:1b` | 3 GB |
| `qwen2.5:0.5b` | — |

With an NVIDIA GPU it starts directly at the largest model; with fewer than 4 CPU cores it starts one tier lower. From there, it tries that model with a real prompt, tells you how long it took, and asks whether you want to try a lighter one (Y/N) — you can repeat as many times as you like until you keep the one you prefer.

## Response style

The bot replies according to one of three styles, defined in [response_styles.py](response_styles.py):

| Option | Style | Text used in the prompt |
|---|---|---|
| 1 | `brief` | "Responde de forma natural y breve." |
| 2 | `technical` | "Responde de forma técnica y extensa, aportando todo el detalle posible." |
| 3 | `balanced` | "Responde de forma equilibrada, sin ser demasiado breve ni demasiado extensa." |

(The instruction text itself stays in Spanish, since that's the language the bot talks to Telegram users in.)

Chosen the first time in `install.py`, and saved to `.env` as `RESPONSE_STYLE`. To change it — or the other settings below — later:

```bash
source venv/bin/activate
python configure.py
```

`configure.py` shows a small menu:

1. **Response style** — as above.
2. **Timezone** — the IANA timezone (e.g. `Europe/Madrid`) used to resolve reminder/calendar dates; validated against Python's `zoneinfo` before saving, so a typo can't silently break date resolution.
3. **Email (contacts + SMTP)** — add `name:email` contacts one at a time (existing ones are shown and can be overwritten), and set the SMTP host/port/user/password/from address needed to actually send emails. The password prompt hides your input (`getpass`) and is never echoed or logged.

It only updates the section you picked, leaving the rest of `.env` (including the Telegram token) untouched, and restarts the bot automatically if it's running as a `systemd` service; otherwise it tells you how to restart it manually.

## Project layout

| File | Responsibility |
|---|---|
| `bot.py` | Telegram wiring only: handlers, startup, orchestration. |
| `context.py` | Builds the layered prompt, resolves references, updates `[STATE]`/`[MEM]`. |
| `llm.py` | Single wrapper around Ollama's `/api/generate` (used by everything else). |
| `actions.py` | Validates the model's action JSON, routes it to a tool, templates the reply. |
| `datetime_utils.py` | Timezone-aware "now", and the only place that validates dates/times. |
| `scheduler.py` | Polls SQLite for due reminders and delivers them via Telegram. |
| `db.py` | All SQLite access: conversations, messages, memory, reminders, events, drafts. |
| `tools/reminders.py`, `tools/calendar.py`, `tools/email.py` | One file per tool; this is where new ones get added. |

## Context architecture

Conversations are stored as **numbered turns** (one shared `turn_number` per Usuario/Tú pair in `messages`), which lets the system resolve references deterministically instead of asking the model to count. Each message is built in layers (`build_action_prompt()` in `context.py`), and **an empty layer is omitted entirely** from the prompt instead of being shown as "no data":

```
[SYS]        Fixed instructions: date + response style. Always present.
[MEM]        Persistent, stable facts about the user (job, preferences, long-term
             goals...). Cross-conversation. Only if there are any.
[STATE]      Compact state of THIS conversation, as four fixed fields (topic,
             goal, pending, note). Only the non-empty fields are shown.
[REFERENCE]  The exact turn a reference like "2 questions ago" resolved to.
             Only when resolve_reference() matched something.
[RECENT]     Last literal turns, tagged with how many questions ago each is.
             Only if there are prior turns.
[USER]       The current message, always last.
```

**Deterministic reference resolution — before the model ever sees the message.** `resolve_reference()` runs a set of accent-insensitive regex patterns against the user's message (`"hace N preguntas"`, `"la primera pregunta"`, `"qué te pregunté antes"`, etc.) and, if one matches, computes the exact `turn_number` it refers to and fetches that turn directly from the database — no LLM call, no counting, zero added latency. The result is injected as its own `[REFERENCE]` layer, so a question like *"¿qué te pregunté hace dos preguntas?"* gets the exact right turn handed to the model instead of relying on it to count alternating lines (which a 3B model does unreliably). If no pattern matches but the message still looks context-dependent (short, or with pronouns like "eso"), `[RECENT]` widens instead, as a fallback.

**[MEM] — structured persistent memory.** No semantic search or embeddings: it only stores short, explicit, *durable* facts the user states about themselves (`user_facts` table) — never assistant claims, and never session-specific context (e.g. "soy desarrollador web" qualifies, "hoy busco ofertas de Python" doesn't — that belongs in `[STATE]`). Since a model response can be wrong (a hallucination), and facts are only ever extracted from the user's own messages, an assistant error can never become a stored "fact" that contaminates future prompts.

**[STATE] — four fixed fields, not free prose.** `TOPIC` / `GOAL` / `PENDING` / `NOTE`, extracted together with new `[MEM]` facts in a single Ollama call (`maybe_update_state_and_facts()`), triggered every `SUMMARY_BATCH_TURNS` (3) turns that fall out of the `[RECENT]` window unfolded, and run **after** replying so it never adds latency to what you receive. The extraction prompt explicitly forbids copying the assistant's own prior wording, and `NOTE` is specifically meant to record the user's *corrections* instead of the assistant's original (possibly wrong) claim. As a second safety net, `merge_state()` rejects any field that comes back implausibly long (`FIELD_SANITY_MAX_CHARS`, 220 chars) — a strong signal the model copy-pasted prose instead of synthesizing state — and keeps the previous value rather than accepting it.

**[RECENT] — dynamic window, turn-tagged.** `DEFAULT_WINDOW_TURNS` (2 turns) by default, expanding to `EXPANDED_WINDOW_TURNS` (4) only as a fallback when no explicit reference resolved but the message still looks context-dependent. Each turn is tagged `[hace N preguntas]` so counting isn't left to the model even within the raw window.

Tunable constants at the top of `context.py`: `DEFAULT_WINDOW_TURNS`, `EXPANDED_WINDOW_TURNS`, `HISTORY_SNIPPET_CHARS`, `STATE_FIELD_MAX_CHARS`, `FIELD_SANITY_MAX_CHARS`, `FACT_MAX_CHARS`, `SUMMARY_BATCH_TURNS`.

**Known limitation, by design:** references to a sub-part of a compound question (e.g. *"¿cuál era la segunda cosa que te pedí?"*, when a single prior turn asked for two things) aren't resolved by turn lookup — that level of detail is expected to live in `[STATE].pending` instead, captured when that turn gets folded. Going further (parsing sub-requests within a turn) was left out on purpose to avoid over-engineering a local, single-user assistant.

## Actions: reminders, notes, tasks, calendar, email, memory

Asimov can do more than chat: reminders, personal notes, a task list, calendar events, and drafting/sending emails, all triggered by natural language. Examples: *"Recuérdame sacar el pollo del horno en 10 minutos"*, *"Apunta que dejé las llaves en el cajón"*, *"Añade comprar leche a mis tareas"*, *"Pon una reunión con Juan el jueves a las 11"*, *"Escribe un correo a Juan diciéndole que llegaré tarde"* → *"Envíalo"*, *"Recuerda que prefiero Python"*, *"¿Qué recuerdas de mí?"*.

**The core rule: the LLM never executes anything.** Every user message goes through exactly one Ollama call (`context.build_action_prompt()` + `llm.generate(..., json_mode=True)`) that asks the model to output a single, strict JSON object choosing one action (`CHAT`, the reminder/note/task/calendar/email actions, or `REMEMBER`/`FORGET`/`LIST_MEMORY` — see `TOOLS_BLOCK` in `context.py` for the full, compact list) with its parameters — never code, never a tool call the model runs itself. Ollama's `format: "json"` mode constrains the output at generation time, and `actions.parse_action_json()` still never trusts it blindly: invalid JSON or an unrecognized action always falls back to plain `CHAT` with a generic message instead of breaking the conversation.

```
USER MESSAGE
     │
     ▼
1 Ollama call → {"action": ..., ...params}   (context.build_action_prompt + llm.generate)
     │
     ▼
actions.py validates EVERY field itself (dates, required fields, recipients) —
the model's own "missing" list is never the only check
     │
     ├─ missing something?  → templated question in Python (no 2nd LLM call), stored as
     │                         conversations.pending_action, so the next message completes it
     │
     └─ complete? → tools/*.py executes deterministically against SQLite (or SMTP for email)
                     → actions.py templates the natural-language reply (no 2nd LLM call either)
```

**Dates and times are never invented by the model.** `context.build_action_prompt()` gives it `CURRENT_DATETIME`/`TIMEZONE` as ground truth in `[SYS]`; the model may extract a relative delay (`delay_seconds`, for "in 10 minutes") or an absolute datetime (for "tomorrow at 9"), but `datetime_utils.validate_delay_seconds()` / `validate_absolute_datetime()` are the only code that decides whether it's actually usable — rejecting anything unparseable, in the past, or absurdly far in the future (`MAX_HORIZON`, 1 year). A rejected date is treated exactly like a missing one: the user gets asked again.

**Multi-turn completion, still one call per message.** If a message is missing a required field (e.g. a reminder with no time), Python asks a fixed, templated question and stores the partial action as JSON in `conversations.pending_action`. The user's next message doesn't go through the general classifier again — a small, targeted prompt asks the model to extract *only* that missing field, Python re-validates, and either executes or asks again. This keeps every single turn — chat, a fresh action, or completing a pending one — to exactly one Ollama call, never a chain of them.

**Confirmation before sending an email — the one exception.** `EMAIL_DRAFT` only ever creates a draft (`email_drafts` table) and shows it to the user; it's never sent automatically. Sending requires an explicit follow-up, and because this is the one genuinely irreversible action, Asimov spends a second small Ollama call specifically to classify whether the reply confirms sending (`actions._handle_send_confirmation()`) rather than trusting a keyword match — the model can't skip this by emitting `SEND_EMAIL` directly with no draft in play; if there's no pending confirmation, that action is a no-op that asks the user to prepare the draft first.

**Contacts are never invented.** `tools/email.py` resolves a recipient name against the `CONTACTS` env var; if it's already a valid address it's used as-is, and if neither applies, the field is treated as missing and the user is asked for the actual email address — the model can never fabricate one.

**No `time.sleep()`, survives restarts.** Reminders live in the `reminders` SQLite table (`id`, `user_id`, `text`, `execute_at`, `status`). `scheduler.py` registers a `python-telegram-bot` `JobQueue` job (`check_due_reminders`) that polls every 30 seconds for `status='pending'` rows whose `execute_at` has passed, delivers them via `bot.send_message`, and marks them `completed`. Since the state lives in SQLite and not in memory, a restart just resumes polling — anything that came due while the process was down gets delivered on the next tick.

**Calendar is local-only for now**, deliberately: `tools/calendar.py` is the single seam the rest of the app talks to, backed today by the `calendar_events` SQLite table. Wiring it to Google Calendar or Outlook later means changing only that one file.

**Notes and tasks** (`tools/notes.py`, `tools/tasks.py`) follow the same pattern as reminders/events: plain SQLite tables (`notes`, `tasks`), substring search/matching for `SEARCH_NOTES`/`DELETE_NOTE`/`COMPLETE_TASK`/`CANCEL_TASK` (ambiguous matches are reported back instead of guessed at), and a task's due date is optional — an invalid or missing one just means "no due date," it never blocks creating the task.

**Memory is transversal, not just another tool.** `[MEM]` is already injected into *every* prompt automatically, and facts get extracted passively every few turns by `context.maybe_update_state_and_facts()` regardless of what actions happen — that part doesn't change. `REMEMBER`/`FORGET`/`LIST_MEMORY` are simply a second, user-controlled entry point into the *same* `user_facts` store, for explicit control ("recuerda que...", "olvida que...", "¿qué recuerdas de mí?"). Both entry points — passive extraction and explicit commands — go through `tools/memory.py`, which is the single place that decides what's allowed to become a memory:

- **Never guessed from the assistant's own words.** Facts are only ever extracted from what the *user* said (structurally true for passive extraction, since only user turns are fed to it; and true by construction for `REMEMBER`, since it's the user's own message). A hallucinated assistant claim can't become a stored "fact."
- **Sensitive data is refused, not stored.** `tools/memory.is_sensitive()` pattern-matches for passwords, tokens, API keys, PINs, card/account numbers, etc., and refuses to save a match — checked on *both* entry points, plus the extraction prompt itself is separately instructed never to put that kind of thing in `FACTS`. Defense in depth: an instruction a 3B model might ignore, backed by a code-level check that can't be talked out of it.
- **Deletable.** `FORGET` reuses the same ambiguous/not-found matching pattern as cancelling a reminder or event.

**Adding a new tool** means: write `tools/<name>.py` with plain functions that talk to `db.py` (or an external API), add its action(s) to `ALLOWED_ACTIONS` and `TOOLS_BLOCK` in `context.py`, add a validator to `VALIDATORS` in `actions.py`, and a branch in `run_action()`. No changes needed anywhere else.

**Not implemented yet:** `WEB_SEARCH` and `WEATHER` need a real external API (and a key) to avoid inventing results — they're not wired in until a provider is chosen. Chaining multiple actions from one message (e.g. "add it to the calendar and remind me an hour before") is intentionally deferred too, per the project's own phased plan — today each message resolves to exactly one action.

## Logs

The bot writes logs to `logs/asimov.log`, with automatic rotation (5 MB per file, 3 backups) and also to the console. It logs bot startup, user actions (start, new conversation, messages), and errors (failed Ollama calls, unhandled exceptions). Log files aren't versioned (`logs/*.log*` is in `.gitignore`); only the empty folder is kept in the repo.

Every Ollama call also logs its own timing breakdown (`total`, `load`, `prompt_eval`, `generation`), tagged with what it was for (`action-classify:<conv_id>`, `pending-fill:<conv_id>`, `send-confirm:<conv_id>`, `state-extraction`) — useful for diagnosing slow replies: if `load` is high, Ollama had to reload the model into memory (see `OLLAMA_KEEP_ALIVE` above); if `generation` dominates, the generated reply is simply very long.

With `LOG_LEVEL=DEBUG` in `.env` (default `INFO`), it also logs the **full prompt** sent to Ollama on every message — useful for seeing exactly what context (memory + history + tool definitions) is actually being injected.
