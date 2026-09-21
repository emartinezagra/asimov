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

Chosen the first time in `install.py`, and saved to `.env` as `RESPONSE_STYLE`. To change it later:

```bash
source venv/bin/activate
python configure.py
```

`configure.py` updates `.env` (without touching other variables like the token) and restarts the bot automatically if it's running as a `systemd` service; otherwise it tells you how to restart it manually.

## Context architecture

Each message is built in layers (`build_prompt()` in `bot.py`), and **an empty layer is omitted entirely** from the prompt instead of being shown as "no data":

```
[SYS]     Fixed instructions: date + response style. Always present.
[MEM]     Persistent, stable facts about the user (age, job, preferences...).
          Cross-conversation. Only if there are any.
[STATE]   Compact state of THIS conversation (topic, entities, decisions,
          pending tasks, pronoun referents). Only once one has been generated.
[RECENT]  Last literal turns of this conversation. Only if there are prior messages.
[USER]    The current message, always last.
```

**[MEM] — structured persistent memory.** No semantic search or embeddings: it only stores short, explicit facts the user states about themselves (`user_facts` table), never assistant claims. This matters because a model response can be wrong (a hallucination) — if it were stored as a "memory" and re-injected later, the error would propagate and reinforce itself over time. Since it's only ever extracted from the user's own messages, that can't happen. Since a personal-use fact count stays small, all of them are always included, no relevance filtering needed.

**[STATE] — progressive conversation summary**, not a full transcript. When `SUMMARY_BATCH_SIZE` (6) messages that already fell out of the `[RECENT]` window pile up unfolded, `maybe_update_state_and_facts()` asks the model, in a single call, to (a) update `[STATE]` with topic/entities/decisions/pending items/referents — explicitly instructed to record the user's corrections instead of the assistant's original (possibly wrong) claims — and (b) extract new facts for `[MEM]` from what the user said only. This adds one extra Ollama call, but only every 6 messages (not every one), and it runs **after** replying to you, so it doesn't add latency to the response you receive.

**[RECENT] — dynamic window**, not fixed: `DEFAULT_WINDOW` (4 messages / 2 turns) by default, since `[STATE]` already carries the condensed continuity and a 3B model performs worse the more literal text gets mixed in. It expands to `EXPANDED_WINDOW` (8) only when the current message looks like it depends on immediate context — short messages or ones with pronouns/references ("that", "the previous one", etc.) via `looks_referential()`, a cheap heuristic with no extra model call.

Tunable constants at the top of `bot.py`: `DEFAULT_WINDOW`, `EXPANDED_WINDOW`, `HISTORY_SNIPPET_CHARS`, `STATE_MAX_CHARS`, `FACT_MAX_CHARS`, `SUMMARY_BATCH_SIZE`.

## Logs

The bot writes logs to `logs/asimov.log`, with automatic rotation (5 MB per file, 3 backups) and also to the console. It logs bot startup, user actions (start, new conversation, messages), and errors (failed Ollama calls, unhandled exceptions). Log files aren't versioned (`logs/*.log*` is in `.gitignore`); only the empty folder is kept in the repo.

Each chat reply also logs Ollama's own timing breakdown (`total`, `load`, `prompt_eval`, `generation`), useful for diagnosing slow replies: if `load` is high, Ollama had to reload the model into memory (see `OLLAMA_KEEP_ALIVE` above); if `generation` dominates, the generated reply is simply very long.

With `LOG_LEVEL=DEBUG` in `.env` (default `INFO`), it also logs the **full prompt** sent to Ollama on every message — useful for seeing exactly what context (memory + history) is actually being injected.
