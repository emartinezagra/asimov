# Asimov

Bot de Telegram con memoria persistente estructurada y resumen progresivo de conversación (todo en SQLite), pensado para correr en local contra [Ollama](https://ollama.com) u otro LLM compatible con su API, y optimizado para dar la mejor calidad posible con el mínimo de tokens de contexto — especialmente pensado para modelos pequeños como Llama 3.2 3B.

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
- [Ollama](https://ollama.com) corriendo en local (`http://127.0.0.1:11434` por defecto), con el modelo de chat que vayas a usar:
  ```bash
  ollama pull llama3.2:3b
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
| `DB_PATH` | Ruta del SQLite de conversaciones, estado y memoria | `./conversations.db` |

`.env` **no** se sube al repositorio (está en `.gitignore`) — cada persona usa su propio token.

## Ejecución

```bash
source venv/bin/activate
python bot.py
```

`conversations.db` se crea automáticamente en el primer arranque; tampoco se versiona, ya que son datos generados en tiempo de ejecución (historial de conversaciones, estado y memoria).

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

## Arquitectura del contexto

Cada mensaje se construye en capas (`build_prompt()` en `bot.py`), y **una capa vacía se omite por completo** del prompt en vez de mostrarse como "sin datos":

```
[SYS]     Instrucciones fijas: fecha + estilo de respuesta. Siempre presente.
[MEM]     Hechos persistentes y estables sobre el usuario (edad, trabajo, preferencias...).
          Cross-conversación. Solo si hay alguno.
[STATE]   Estado compacto de ESTA conversación (tema, entidades, decisiones, tareas
          pendientes, referentes de pronombres). Solo si ya se generó alguno.
[RECENT]  Últimos turnos literales de esta conversación. Solo si hay mensajes previos.
[USER]    El mensaje actual, siempre al final.
```

**[MEM] — memoria persistente estructurada.** No usa búsqueda semántica ni embeddings: se guardan solo hechos cortos y explícitos que el usuario dice sobre sí mismo (tabla `user_facts`), nunca afirmaciones del asistente. Esto es importante porque una respuesta del modelo puede ser errónea (alucinación) — si se guardara como "recuerdo" y se reinyectara más tarde, el error se propagaría y se reforzaría con el tiempo. Al extraerse solo de los mensajes del usuario, eso no puede pasar. Como en un uso personal el número de hechos estables se mantiene pequeño, se incluyen siempre todos, sin necesidad de filtrar por relevancia.

**[STATE] — resumen progresivo de la conversación**, no un histórico completo. Cuando se acumulan `SUMMARY_BATCH_SIZE` (6) mensajes que ya salieron de la ventana `[RECENT]` sin condensar, `maybe_update_state_and_facts()` le pide al modelo, en una sola llamada, que (a) actualice el `[STATE]` con tema/entidades/decisiones/pendientes/referentes — instruyéndole explícitamente a reflejar correcciones del usuario en vez de las afirmaciones originales del asistente si hubo un error — y (b) extraiga hechos nuevos para `[MEM]` a partir únicamente de lo que dijo el usuario. Esto añade una llamada extra a Ollama, pero solo cada 6 mensajes (no en cada uno) y **después** de responderte, sin añadir espera a la respuesta que recibes.

**[RECENT] — ventana dinámica**, no fija: por defecto `DEFAULT_WINDOW` (4 mensajes / 2 turnos), ya que `[STATE]` aporta la continuidad condensada y un modelo de 3B rinde peor cuanto más texto literal se le mezcla. Se amplía a `EXPANDED_WINDOW` (8) solo cuando el mensaje actual parece depender de contexto inmediato — mensajes cortos o con pronombres/referencias ("eso", "el anterior", etc.), vía `looks_referential()`, una heurística barata sin llamada extra al modelo.

Constantes ajustables al principio de `bot.py`: `DEFAULT_WINDOW`, `EXPANDED_WINDOW`, `HISTORY_SNIPPET_CHARS`, `STATE_MAX_CHARS`, `FACT_MAX_CHARS`, `SUMMARY_BATCH_SIZE`.

## Logs

El bot escribe logs (en inglés) a `logs/asimov.log`, con rotación automática (5 MB por fichero, 3 copias de respaldo) y también por consola. Se registran arranque del bot, acciones de usuario (start, nueva conversación, mensajes) y errores (fallos al llamar a Ollama, excepciones no controladas). Los ficheros de log no se versionan (`logs/*.log*` está en `.gitignore`); solo se mantiene la carpeta vacía en el repo.

Cada respuesta del chat también registra el desglose de tiempos que devuelve Ollama (`total`, `load`, `prompt_eval`, `generation`), útil para diagnosticar respuestas lentas: si `load` es alto, es que Ollama tuvo que recargar el modelo en memoria (ver `OLLAMA_KEEP_ALIVE` arriba); si `generation` es el que domina, es que la respuesta generada es simplemente muy larga.

Con `LOG_LEVEL=DEBUG` en `.env` (por defecto `INFO`), además se registra el **prompt completo** enviado a Ollama en cada mensaje — útil para ver exactamente qué contexto (recuerdos + historial) se está inyectando de verdad.
