# Asimov

Bot de Telegram con memoria conversacional (SQLite) y memoria semántica (RAG con ChromaDB), pensado para correr en local contra [Ollama](https://ollama.com) u otro LLM compatible con su API.

## Requisitos

- Python 3.10+
- [Ollama](https://ollama.com) corriendo en local (`http://127.0.0.1:11434` por defecto), con los modelos:
  ```bash
  ollama pull llama3.2:3b
  ollama pull nomic-embed-text
  ```
- Un token de bot de Telegram (se obtiene hablando con [@BotFather](https://t.me/BotFather))

## Instalación

```bash
git clone https://github.com/emartinezagra/asimov.git
cd asimov

python3 -m venv venv
source venv/bin/activate      # en Windows: venv\Scripts\activate

pip install -r requirements.txt
```

## Configuración

Copia la plantilla de variables de entorno y edítala con tu token:

```bash
cp .env.example .env
```

Variables disponibles en `.env`:

| Variable | Descripción | Por defecto |
|---|---|---|
| `TELEGRAM_TOKEN` | Token del bot (BotFather) — **obligatorio** | — |
| `OLLAMA_URL` | URL del servidor Ollama | `http://127.0.0.1:11434` |
| `CHAT_MODEL` | Modelo de chat a usar | `llama3.2:3b` |
| `DB_PATH` | Ruta del SQLite de conversaciones | `./conversations.db` |
| `MEMORY_DB_PATH` | Ruta del store de ChromaDB | `./memory_db` |

`.env` **no** se sube al repositorio (está en `.gitignore`) — cada persona usa su propio token.

## Ejecución

```bash
source venv/bin/activate
python bot.py
```

`conversations.db` y `memory_db/` se crean automáticamente en el primer arranque; tampoco se versionan, ya que son datos generados en tiempo de ejecución (historial de conversaciones y memoria semántica).

Para dejarlo corriendo tras cerrar la sesión SSH, usa `tmux` o `screen`:

```bash
tmux new -s asimov
source venv/bin/activate
python bot.py
# Ctrl+B, D para salir dejándolo corriendo
```
