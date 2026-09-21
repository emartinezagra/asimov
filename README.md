# Asimov

Bot de Telegram con memoria conversacional (SQLite) y memoria semántica (RAG con ChromaDB), pensado para correr en local contra [Ollama](https://ollama.com) u otro LLM compatible con su API.

## Instalación rápida (recomendada)

Requiere solo Python 3 y `curl`. El instalador se encarga del resto: instala Ollama si falta, detecta RAM/CPU/GPU de la máquina para elegir un modelo de partida, y te muestra el tiempo de respuesta real de cada uno preguntándote si quieres probar uno más ligero.

```bash
git clone https://github.com/emartinezagra/asimov.git
cd asimov
./install.sh
```

Te pedirá elegir el estilo de respuesta del bot, el token de Telegram (de [@BotFather](https://t.me/BotFather)) y, al terminar, te preguntará si quieres registrarlo como servicio `systemd` para que arranque solo.

> Por ahora el instalador automático (`install.sh`/`install.py`) solo soporta **Linux**. Para Windows/Mac, sigue la instalación manual de abajo.

## Instalación manual

### Requisitos

- Python 3.10+
- [Ollama](https://ollama.com) corriendo en local (`http://127.0.0.1:11434` por defecto), con los modelos:
  ```bash
  ollama pull llama3.2:3b
  ollama pull nomic-embed-text
  ```
- Un token de bot de Telegram (se obtiene hablando con [@BotFather](https://t.me/BotFather))

### Pasos

```bash
git clone https://github.com/emartinezagra/asimov.git
cd asimov

python3 -m venv venv
source venv/bin/activate      # en Windows: venv\Scripts\activate

pip install -r requirements.txt
```

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
| `RESPONSE_STYLE` | Estilo de respuesta: `brief`, `technical` o `balanced` | `balanced` |
| `DB_PATH` | Ruta del SQLite de conversaciones | `./conversations.db` |
| `MEMORY_DB_PATH` | Ruta del store de ChromaDB | `./memory_db` |

`.env` **no** se sube al repositorio (está en `.gitignore`) — cada persona usa su propio token.

## Ejecución

```bash
source venv/bin/activate
python bot.py
```

`conversations.db` y `memory_db/` se crean automáticamente en el primer arranque; tampoco se versionan, ya que son datos generados en tiempo de ejecución (historial de conversaciones y memoria semántica).

Para dejarlo corriendo tras cerrar la sesión SSH sin usar `systemd`, puedes usar `tmux` o `screen`:

```bash
tmux new -s asimov
source venv/bin/activate
python bot.py
# Ctrl+B, D para salir dejándolo corriendo
```

## Cómo elige el modelo el instalador

`install.py` primero detecta RAM, núcleos de CPU y si hay GPU NVIDIA, y con eso elige un modelo de partida:

| Modelo | RAM mínima orientativa |
|---|---|
| `llama3.1:8b` | 16 GB |
| `mistral:7b` | 12 GB |
| `llama3.2:3b` | 6 GB |
| `llama3.2:1b` | 3 GB |
| `qwen2.5:0.5b` | — |

Si hay GPU NVIDIA, empieza directamente por el modelo más grande; si la máquina tiene menos de 4 núcleos de CPU, empieza un escalón más abajo. A partir de ahí, prueba ese modelo con un prompt real, te dice cuánto ha tardado, y te pregunta si quieres bajar a uno más ligero (S/N) — puedes repetir tantas veces como quieras hasta quedarte con el que prefieras.

## Estilo de respuesta

El bot responde según uno de tres estilos, definidos en [response_styles.py](response_styles.py):

| Opción | Estilo | Texto usado en el prompt |
|---|---|---|
| 1 | `brief` | "Responde de forma natural y breve." |
| 2 | `technical` | "Responde de forma técnica y extensa, aportando todo el detalle posible." |
| 3 | `balanced` | "Responde de forma equilibrada, sin ser demasiado breve ni demasiado extensa." |

Se elige la primera vez en `install.py`, y se guarda en `.env` como `RESPONSE_STYLE`. Para cambiarlo después:

```bash
source venv/bin/activate
python configure.py
```

`configure.py` actualiza `.env` (sin tocar el resto de variables, como el token) y reinicia el bot automáticamente si está corriendo como servicio `systemd`; si no, te indica cómo reiniciarlo a mano.

## Logs

El bot escribe logs (en inglés) a `logs/asimov.log`, con rotación automática (5 MB por fichero, 3 copias de respaldo) y también por consola. Se registran arranque del bot, acciones de usuario (start, nueva conversación, mensajes) y errores (fallos al llamar a Ollama, excepciones no controladas). Los ficheros de log no se versionan (`logs/*.log*` está en `.gitignore`); solo se mantiene la carpeta vacía en el repo.
