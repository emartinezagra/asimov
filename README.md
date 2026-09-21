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
| `OLLAMA_KEEP_ALIVE` | Cuánto mantiene Ollama el modelo cargado en RAM tras cada uso (`-1` = no descargar nunca) | `30m` |
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

## Tamaño del prompt

Cada mensaje se envía a Ollama junto con recuerdos relevantes (RAG) y los últimos turnos de la conversación. Para que ese contexto no crezca sin límite y dispare el tiempo de `prompt_eval` (sobre todo sin GPU), `bot.py` acota, mediante constantes al principio del fichero:

- `MEMORY_RESULTS` (3) / `MEMORY_SNIPPET_CHARS` (300) — cuántos recuerdos se recuperan y cuántos caracteres de cada uno se usan.
- `HISTORY_MESSAGES` (6) / `HISTORY_SNIPPET_CHARS` (300) — lo mismo para los mensajes recientes de la conversación.
- `MEMORY_STORE_CHARS` (500) — también se recorta lo que se guarda como recuerdo nuevo, para que no siga creciendo indefinidamente.

Además, `MEMORY_MAX_DISTANCE` (en `.env`, sin definir por defecto) descarta recuerdos poco relevantes para la pregunta actual en vez de inyectar siempre los `MEMORY_RESULTS` más cercanos aunque no vengan al caso. ChromaDB devuelve una distancia por cada recuerdo candidato (más bajo = más relevante); `search_memory()` la usa para filtrar. El log (`Memory candidate distances for user ...`) muestra esos valores reales en cada mensaje, para que calibres el umbral con datos de tu propio uso en vez de un número arbitrario.

### Resumen progresivo

`HISTORY_MESSAGES` solo mantiene en crudo los últimos turnos de la conversación — pero en vez de simplemente descartar lo anterior, se va condensando en un resumen. Cuando se acumulan `SUMMARY_BATCH_SIZE` (6) mensajes que ya han salido de esa ventana reciente y aún no están resumidos, `maybe_update_summary()` le pide al propio modelo que los condense (integrando el resumen previo si lo había) y lo guarda en `conversations.summary`. Ese resumen se incluye en el prompt junto al historial reciente, en vez de la conversación completa en crudo.

Esto añade una llamada extra a Ollama, pero solo cada `SUMMARY_BATCH_SIZE` mensajes (no en cada uno), y ocurre **después** de responderte, así que no añade espera a la respuesta que recibes. `SUMMARY_MAX_CHARS` (600) acota además el tamaño del resumen ya guardado al incluirlo en el prompt.

## Logs

El bot escribe logs (en inglés) a `logs/asimov.log`, con rotación automática (5 MB por fichero, 3 copias de respaldo) y también por consola. Se registran arranque del bot, acciones de usuario (start, nueva conversación, mensajes) y errores (fallos al llamar a Ollama, excepciones no controladas). Los ficheros de log no se versionan (`logs/*.log*` está en `.gitignore`); solo se mantiene la carpeta vacía en el repo.

Cada respuesta del chat también registra el desglose de tiempos que devuelve Ollama (`total`, `load`, `prompt_eval`, `generation`), útil para diagnosticar respuestas lentas: si `load` es alto, es que Ollama tuvo que recargar el modelo en memoria (ver `OLLAMA_KEEP_ALIVE` arriba); si `generation` es el que domina, es que la respuesta generada es simplemente muy larga.

Con `LOG_LEVEL=DEBUG` en `.env` (por defecto `INFO`), además se registra el **prompt completo** enviado a Ollama en cada mensaje — útil para ver exactamente qué contexto (recuerdos + historial) se está inyectando de verdad.
