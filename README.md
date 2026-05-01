# telegram-audio-dl

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

CLI interactivo en Python para descargar archivos de audio/música de canales de Telegram a los que estás suscrito y reproducirlos localmente. Las descargas corren en background, persisten checkpoint para retomarlas, el reproductor soporta controles multimedia (next/prev/seek/pause), y todo el playback puede minimizarse a "Now Playing" mientras navegas el menú.

> **Aviso**: este proyecto usa la **MTProto API personal** de Telegram (Telethon). No es un bot. Solo descarga audios de canales/chats a los que ya tienes acceso con tu cuenta. Respeta los términos de servicio de Telegram y el copyright del material.

---

## Tabla de contenidos

1. [Características](#características)
2. [Requisitos](#requisitos)
3. [Setup paso a paso](#setup-paso-a-paso)
4. [Variables de entorno (`.env`)](#variables-de-entorno-env)
5. [Estructura del proyecto](#estructura-del-proyecto)
6. [Cómo correrlo](#cómo-correrlo)
7. [Flujo del menú principal](#flujo-del-menú-principal)
8. [Reproductor: controles](#reproductor-controles)
9. [Streaming online](#streaming-online)
10. [Modo daemon (descargas en background)](#modo-daemon-descargas-en-background)
11. [Estados de los jobs](#estados-de-los-jobs)
12. [Selección por rangos (multi-select)](#selección-por-rangos-multi-select)
13. [Logs y troubleshooting](#logs-y-troubleshooting)
14. [Archivos persistentes](#archivos-persistentes)
15. [Tests](#tests)
16. [FAQ](#faq)
17. [Limitaciones conocidas](#limitaciones-conocidas)
18. [Operación día-a-día](#operación-día-a-día)

---

## Características

- 🔍 **Listado de canales** suscritos con autocomplete (paginación si >100).
- ⬇️  **Descargas en background** con `DownloadManager` async — un job a la vez para evitar rate limits.
- 📊 **Pre-inventario al encolar**: el state SQLite se llena al momento de encolar, así nada se pierde si se interrumpe el CLI.
- ⏸️ **Pausa al salir** + 🔁 **auto-reanudación** al volver a abrir (con fallback a Telegram si el state se borró).
- 🌐 **Sincronización con Telegram** al reanudar para detectar audios nuevos del canal.
- 🌐 **Streaming online** sin tocar disco (`iter_download` → `mpv` por stdin).
- 🎚️  **Cola de streaming online** con auto-avance entre tracks y pre-fetch del siguiente en RAM (arranque <1s entre canciones).
- 👀 **Vista previa** de 30s antes de bajar.
- 📚 **Biblioteca local consultable** con tabla de canales, carpetas, tamaños, y reproducción de uno/varios canales o búsqueda de track específico.
- 🎲 **Shuffle global** de 50 tracks aleatorios de toda la biblioteca.
- 🔀 **Cola mezclada** entre múltiples canales con orden shuffle/alfabético/por canal.
- 🎵 **Reproductor multimedia** con metadata ID3 (mutagen), barra animada, controles mpv (pause/seek/etc).
- 📂 **Reproducir carpeta arbitraria** del filesystem (ajena al CLI).
- 🗄️  **SQLite con WAL** para state + jobs (lecturas no bloquean writes).
- 📝 **Logs rotativos** configurables con `LOG_LEVEL=DEBUG`.

---

## Requisitos

Funciona en **macOS** y **Linux** (Windows no probado). El CLI detecta automáticamente qué reproductor usar según lo que esté instalado.

| Componente | Versión | macOS | Linux (Debian/Ubuntu) | Linux (Arch) |
|---|---|---|---|---|
| Python | ≥ 3.10 | `brew install python` | `apt install python3` | `pacman -S python` |
| `mpv` (recomendado, controles completos) | cualquiera reciente | `brew install mpv` | `apt install mpv` | `pacman -S mpv` |
| Reproductor de respaldo | — | `afplay` (nativo) | `apt install ffmpeg` (incluye `ffplay`) | `pacman -S ffmpeg` |
| Cuenta Telegram | con número y 2FA opcional | tu propia cuenta | tu propia cuenta | tu propia cuenta |
| App en my.telegram.org | api_id + api_hash | ver paso 1 abajo | ver paso 1 abajo | ver paso 1 abajo |

### Detección automática del reproductor

El CLI busca, en orden de prioridad:

1. **`mpv`** — controles completos (pause/seek/queue navigation). Recomendado en cualquier plataforma.
2. **`afplay`** — nativo macOS, sin controles.
3. **`ffplay`** — parte de ffmpeg, cross-platform, sin controles. Mejor opción Linux si no usas mpv.
4. **`mpg123`** — solo mp3, ligero. Tercera opción Linux.
5. **`paplay`** — PulseAudio, solo wav/raw. Cuarta opción Linux.

Sin ninguno de los anteriores: la opción **Biblioteca local** y **Vista previa** quedan deshabilitadas con un mensaje claro indicando qué instalar.

---

## Setup paso a paso

### 1. Credenciales de Telegram

1. Entra a <https://my.telegram.org> con tu número.
2. Te llega un código por Telegram (no SMS).
3. Click en **API development tools**.
4. Crea una aplicación:
   - **App title**: `telegram-audio-dl`
   - **Short name**: `tgaudiodl`
   - **Platform**: Desktop
5. Copia `api_id` (entero ~8 dígitos) y `api_hash` (string hex de 32 chars).

### 2. Reproductor con controles (recomendado)

| Sistema | Comando |
|---|---|
| macOS | `brew install mpv` |
| Debian/Ubuntu | `sudo apt install mpv` |
| Arch | `sudo pacman -S mpv` |
| Fedora | `sudo dnf install mpv` |

Sin `mpv`, el reproductor cae a un binario simple sin controles (solo `Ctrl+C` para detener):
- En macOS, `afplay` (preinstalado).
- En Linux, `ffplay` si tienes ffmpeg (`apt/pacman/dnf install ffmpeg`); si no, `mpg123` o `paplay`.

### 3. Crear venv e instalar

> Recomendación: crea el venv **fuera del SSD externo** si lo estás usando, porque macOS crea archivos AppleDouble (`._*`) en filesystems no-APFS que rompen `pip`.

```bash
python3 -m venv ~/.virtualenvs/telegram-audio-dl
~/.virtualenvs/telegram-audio-dl/bin/pip install --upgrade pip
~/.virtualenvs/telegram-audio-dl/bin/pip install -e "$PWD"
```

### 4. Configurar `.env`

```bash
cp .env.example .env
# Edita con tus valores
```

```dotenv
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef0123456789abcdef0123456789
TELEGRAM_PHONE=+1234567890
TELEGRAM_SESSION=telegram_audio_dl
DOWNLOAD_ROOT=
```

### 5. Primer login

```bash
~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl
```

La primera vez:
1. Te pide el **código** que llega por Telegram (chat oficial "Telegram", no SMS).
2. Si tienes **2FA** activado, te pide la contraseña de cifrado.
3. La sesión queda guardada en `telegram_audio_dl.session`. **No se vuelve a pedir** en futuras corridas.

---

## Variables de entorno (`.env`)

| Variable | Obligatoria | Default | Descripción |
|---|---|---|---|
| `TELEGRAM_API_ID` | sí | — | Entero. De my.telegram.org |
| `TELEGRAM_API_HASH` | sí | — | String hex 32 chars. De my.telegram.org |
| `TELEGRAM_PHONE` | sí | — | Formato internacional con `+` |
| `TELEGRAM_SESSION` | no | `telegram_audio_dl` | Nombre del archivo de sesión Telethon |
| `DOWNLOAD_ROOT` | no | `~/Downloads` | Carpeta raíz para descargas |
| `LOG_LEVEL` | no | `INFO` | `DEBUG` para troubleshooting detallado |

⚠ **Nunca** committear el `.env` ni el `*.session` — ambos están en `.gitignore`.

---

## Estructura del proyecto

```
telegram-audio-dl/
├── .env.example                  # plantilla — copia a .env
├── .gitignore
├── LICENSE                       # GPL v3
├── pyproject.toml                # dependencias y entry point
├── README.md                     # este archivo
├── CLAUDE.md                     # guía para Claude Code
├── docs/
│   ├── credenciales-telegram.md  # cómo obtener credenciales
│   └── escenarios.md             # matriz de escenarios + cobertura
├── bin/
│   └── aut-03-run.sh             # wrapper Vaultwarden/Bitwarden (opcional)
├── scripts/
│   ├── telegram-audio-dl.service # unit file systemd (Linux)
│   └── com.telegram-audio-dl.plist # launchd agent (macOS)
├── tools/
│   ├── kickoff_downloads.py      # script: descarga lista fija de canales
│   └── migrate_json_to_sqlite.py # one-shot: state JSON viejo → SQLite
├── src/telegram_audio_dl/
│   ├── __main__.py               # python -m telegram_audio_dl
│   ├── entrypoint.py             # entry point del console script
│   ├── cli.py                    # menús y flujos interactivos
│   ├── config.py                 # carga de .env
│   ├── client.py                 # wrapper Telethon
│   ├── database.py               # wrapper SQLite (WAL, schema, migrations)
│   ├── downloader.py             # descarga foreground (Downloader)
│   ├── download_manager.py       # jobs en background (DownloadManager)
│   ├── daemon.py                 # daemon mode + IPC
│   ├── ipc.py                    # IPC server/client (Unix socket)
│   ├── player.py                 # reproductor mpv via IPC
│   ├── metadata.py               # mutagen para tags ID3/m4a/etc
│   ├── state.py                  # FileEntry + StateStore (sobre SQLite)
│   └── logging_setup.py          # configuración del logger
└── tests/                        # 200+ tests pytest
```

Generados al primer uso (gitignored): `.env`, `*.session*`, `state/`.

---

## Cómo correrlo

```bash
# Entry point instalado
~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl

# O sin instalar
python -m telegram_audio_dl

# Con logs verbosos
LOG_LEVEL=DEBUG ~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl
```

### Modo desarrollo (sin escribir secrets a `.env`)

Si tu `.env` del repo es solo un placeholder/documentación y no quieres dejar credenciales en disco, exporta las variables en el shell antes de invocar el binario. `python-dotenv` **no sobreescribe** variables ya presentes en el environment, así que estas tienen prioridad:

```bash
export TELEGRAM_API_ID=...
export TELEGRAM_API_HASH=...
export TELEGRAM_PHONE=+1234567890
~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl
```

Útil para pruebas one-shot, máquinas compartidas, o repos donde `.env` solo lleva metadata. Las variables viven solo en la sesión del shell — al cerrar la terminal desaparecen.

> **Tip de seguridad**: si guardas las credenciales en un gestor (Vaultwarden, 1Password, Bitwarden CLI), puedes envolver los `export` en un script `scripts/dev-env.sh` (gitignored) que lea de ahí. Ejemplo con `bw` CLI:
>
> ```bash
> # scripts/dev-env.sh (NO commitear)
> export BW_SESSION=$(bw unlock --raw)
> ITEM=$(bw get item "Telegram (AUT-03)" --session "$BW_SESSION")
> export TELEGRAM_API_ID=$(echo $ITEM | jq -r '.fields[]|select(.name=="api_id").value')
> export TELEGRAM_API_HASH=$(echo $ITEM | jq -r '.login.username')
> export TELEGRAM_PHONE=$(echo $ITEM | jq -r '.login.password')
> ```
>
> Luego: `source scripts/dev-env.sh && ~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl`

---

## Flujo del menú principal

```
Menú principal:
> Buscar canales en Telegram          ← descargar de un canal nuevo
  Reanudar descargas pendientes        ← retomar cosas interrumpidas
  Ver descargas en curso               ← monitorear jobs activos
  Biblioteca local                     ← canales descargados (consulta + reproducción)
  Reproducir música de una carpeta     ← cualquier carpeta del FS arbitraria
  🌐 Reproducción online (streaming)   ← solo si mpv está instalado
  Salir
```

### Buscar canales en Telegram

1. Lista todos los canales/grupos suscritos (paginado de 50 si hay >100).
2. Eliges uno → muestra los audios encontrados (filtrados por `InputMessagesFilterMusic`).
3. Acción:
   - **Descargar todos (con dedup)** — encola al manager el set completo.
   - **Seleccionar algunos** — checkbox si <100 audios; rangos si más.
   - **Vista previa (30s con afplay)** — preview corto sin descargar todo.
   - **← Volver a canales**
4. Confirmas carpeta destino (default: `<project_root>/<canal>` o última usada).
5. Job encolado → vuelves al menú.

> Para reproducir audios sin guardarlos, usa la opción **🌐 Reproducción online (streaming)** del menú principal — ver sección [Streaming online](#streaming-online).

### Reanudar descargas pendientes

1. Lista canales con archivos no completados.
2. Tabla extendida con:

   | Columna | Significado |
   |---|---|
   | Total | Audios conocidos del canal en el state local |
   | Completados | Ya bajados al 100% (verificados en disco) |
   | Pendientes | Sin completar (no iniciados + parciales) |
   | Parciales | Subconjunto de Pendientes con `downloaded_bytes > 0` |
   | % local | `Completados / Total` — qué tan avanzado vas |
   | Faltan | Bytes restantes a descargar |
   | Última carpeta | `destination_dir` registrado |

3. Eliges canal.
4. **Sincronización opcional con Telegram** (default: sí):
   - Consulta a Telegram el estado actual del canal con `iter_messages`.
   - Compara cada `message_id` contra el state local.
   - Agrega entries nuevas para los audios que el canal añadió desde tu última visita.
   - Reporta: `✓ N audios nuevos agregados al state, M ya conocidos.`
5. Confirma carpeta destino → encola.

> **Por qué la sincronización**: los canales agregan música todos los días. Sin sincronizar, "Reanudar" solo procesa lo que ya conoces. Con sincronización, el state se actualiza primero para que los nuevos audios aparezcan como pendientes.

> **Reanudación es instantánea**: construye los `AudioItem` desde el state local (`filename`, `size`) sin re-resolver mensajes ya conocidos. La sincronización es opcional — si la saltas, te ahorras la consulta pero no detectas audios nuevos.

### Ver descargas en curso

Tabla con todos los jobs (queued, running, done, failed, cancelled, interrupted).

```
Resumen: running: 1 · queued: 2 · done: 5

ID    Canal           Estado     Archivos                 Bytes               Velocidad   ETA       Actual
a3f1  House Techno    running    36/14898 (canal: 15102)  654.4 MiB / 219 GiB 1.4 MiB/s   43:57:12  Track Name
```

Acciones:
- **Refrescar (snapshot)** — re-renderiza con datos actualizados.
- **Ver en vivo (Ctrl+C para volver)** — modo Live con auto-refresh 2/s.
- **Cancelar un trabajo** — marca un job como cancelado; el worker corta al siguiente chunk.
- **← Menú principal**

### Biblioteca local

Vista consolidada de **todos los canales descargados** según el state local. Te dice qué canal quedó en qué carpeta y permite reproducir desde ahí.

Tabla de resumen:

```
                 📚 Biblioteca local
┌─────────────────────────┬────────┬──────────┬─────────────────────────────────────────┐
│ Canal                   │ Tracks │ Tamaño   │ Carpeta                                 │
├─────────────────────────┼────────┼──────────┼─────────────────────────────────────────┤
│ Channel A               │   320  │ 6.4 GiB  │ ~/Music/Channel A                       │
│ Channel B               │    98  │ 1.2 GiB  │ ~/Music/Channel B                       │
│ Channel C               │   410  │ 6.5 GiB  │ ~/Music/Channel C                       │
└─────────────────────────┴────────┴──────────┴─────────────────────────────────────────┘
```

Acciones:

- **🎲 Shuffle global (50 al azar de toda la biblioteca)** — `random.sample` sin reemplazo de los tracks de **todos los canales**. Cola con panel multimedia, anterior/siguientes 6, controles mpv.
- **▶️ Reproducir un canal completo** — cola del canal entero.
- **🔀 Reproducir varios canales (cola mezclada)** — multi-select de canales + orden:
  - Aleatorio (shuffle).
  - Alfabético por filename.
  - Por canal (uno tras otro).
- **🎵 Buscar y reproducir un track** — selector con autocomplete sobre TODOS los tracks de TODOS los canales (paginado si >100).
- **📂 Ver todas las carpetas** — tabla detallada con cada canal y sus rutas (útil si un canal quedó en varias carpetas distintas).
- **↩️ Menú principal**.

> **Cómo se construye**: query SQL sobre `files` filtrando `completed = 1 AND destination_dir IS NOT NULL`, luego verifica que cada archivo siga existiendo en disco. Si bajaste a varias carpetas distintas el mismo canal, todas aparecen en `destination_dirs`.

### Reproducir música de una carpeta

Apunta a cualquier carpeta del filesystem (autocomplete con Tab).
- Pregunta si recursivo o no.
- Detecta extensiones: `.mp3 .m4a .mp4 .aac .ogg .oga .opus .flac .wav .aiff .aif`.
- Acciones: "Reproducir uno (loop)" o "Reproducir todos en cola" (Ctrl+C corta cola).

---

## Reproductor: controles

Cuando el track está sonando (con `mpv` instalado):

| Tecla | Acción (playback local) | Acción (streaming online) |
|---|---|---|
| `Espacio` | Pausa / Reanudar | Pausa / Reanudar |
| `→` | +10 segundos | +10 segundos |
| `←` | −10 segundos | −10 segundos |
| `↑` | +30 segundos | +30 segundos |
| `↓` | −30 segundos | −30 segundos |
| `0` | Volver al inicio del track | Volver al inicio del track |
| `n` | — | **Siguiente** (en cola) |
| `p` | — | **Anterior** (en cola; en el primer track lo reinicia) |
| `q` o `Q` | Saltar al siguiente / cerrar | **Salir de la cola** |
| `Ctrl+C` | Cerrar | Salir de la cola |

> **Diferencia local vs streaming**: el playback local solo tiene `q`/`Ctrl+C` para cerrar el track actual y avanzar. En streaming online separamos:
> - `n` salta al siguiente sin esperar a que termine.
> - `p` retrocede al anterior (cancela el prefetch actual y arranca uno del track previo).
> - `q` y `Ctrl+C` salen de toda la cola, no solo del track.
>
> Este mapping sigue la convención de mpv/cmus: `q` siempre es "quit", nunca "skip".

El panel muestra:
- Estado: `▶ REPRODUCIENDO` (archivo local), `🌐 STREAMING` (online), o `⏸ PAUSADO`.
- Título · Artista · Álbum (de los tags ID3 leídos con `mutagen`).
- Bitrate · sample rate · canales · tamaño del archivo.
- Barra de progreso ASCII (40 chars) con `MM:SS / MM:SS` real (consultado a mpv vía IPC).
- Para colas: panel adicional con `↶ Anterior`, `▶ Ahora`, `↷ Siguientes 6`.
- Línea de controles.

Sin `mpv`: panel similar pero sin controles activos. Solo `Ctrl+C` para detener.

---

## Streaming online

Reproduce audios **directamente desde Telegram sin tocar disco**. Los bytes pasan de `iter_download` al stdin de `mpv` por una `asyncio.Queue` en RAM. Requiere `mpv` instalado (`brew install mpv`).

### Cómo entrar

Es una opción top-level del menú principal: **🌐 Reproducción online (streaming)**. La opción solo aparece si `mpv` está instalado.

```
Menú principal → 🌐 Reproducción online (streaming)
                     ↓
               Selecciona canal (autocomplete)
                     ↓
               Lista audios del canal
                     ↓
               Submenú: Uno solo / Selección (cola) / Todos en cola / Cancelar
```

Al cancelar dentro del submenú vuelves a la selección de canal; al cancelar la selección de canal vuelves al menú principal.

### Submenú

| Opción | Qué hace |
|---|---|
| 🎵 **Uno solo** | Selector paginado, eliges un audio, suena, termina. |
| 🔀 **Selección (cola)** | Multi-select (checkbox si <100 audios; selección por rangos si más). Reproduce los seleccionados en orden de message_id, auto-avanzando. |
| 📃 **Todos en cola** | Cola con todos los audios del canal, en orden. |
| ↩️  Cancelar | Vuelve a la selección de canal. |

### Cola y pre-fetch del siguiente

Las opciones "Selección" y "Todos en cola" usan `_stream_queue_play`, que:

1. **Construye una `PlaybackQueue`** por cada track con `position`, `total`, `previous_name` y `upcoming_names[:6]` — el panel del reproductor muestra `↶ Anterior · ▶ Ahora · ↷ Siguientes`.
2. **Lanza el track N** con su propia instancia de `mpv` (stdin pipe, socket IPC nuevo).
3. **En paralelo, pre-descarga el track N+1** con `iter_download` a una `asyncio.Queue` en memoria (`Prefetch`).
4. **Auto-avanza** al terminar el track N: el N+1 ya tiene la cabecera (y normalmente todo el archivo) bufferizado → arranque <1s. El N+2 entra como nuevo prefetch.
5. **Cleanup**: al hacer `Ctrl+C`, `q` en el último track, o fin natural de cola, se cancelan todos los prefetch tasks pendientes.

#### Pre-fetch: detalles técnicos

| Aspecto | Decisión | Por qué |
|---|---|---|
| Lookahead | Solo el siguiente (N+1) | Audios típicos pesan 6-15 MB; cachear 2 sería >30 MB en RAM con un caso de uso (consumo) que no lo justifica. |
| Tamaño de queue | `maxsize=128` chunks de 256 KB = ~32 MB tope | Backpressure natural si el prefetch va más rápido que el playback. |
| Cancelación | `task.cancel()` + `await` en `_cancel_prefetch` | Idempotente; tolera `task=None` y tasks ya `done()`. |
| Sentinel de fin | `None` encolado en el `finally` del prefetch | El feed task del mpv detecta `None` y cierra stdin → mpv termina. |
| Mensaje faltante | `prefetch.failed = True` + sentinel inmediato | El feed termina sin escribir nada; mpv recibe stdin vacío y sale; la cola avanza al siguiente. |

#### Latencia esperada

- **Track 1**: igual que el stream single-track (depende del primer chunk de Telegram, ~1-3s).
- **Tracks 2…N**: <1s entre el final del anterior y el inicio del siguiente, asumiendo que el prefetch terminó (audios pequeños) o que tiene cabecera suficiente para que mpv arranque.
- Si la red es lenta y el prefetch no alcanzó, el feed task se queda esperando bytes → mpv pausa hasta que llegan. No hay error, solo latencia.

### Diferencias con descargar y reproducir

| | Descargar | Stream online |
|---|---|---|
| Tocar disco | sí (archivo final) | no (todo en RAM) |
| Persistencia | DB SQLite + filesystem | ninguna; al cerrar, se va |
| Resume | sí (offset persistente) | no (re-empieza desde 0) |
| Concurrencia con otros jobs | el manager serializa 1 a la vez | streaming es independiente (no usa el manager) |
| Cola | desde Biblioteca local | desde el menú del canal |
| Pre-fetch | no aplica (descarga lineal) | sí (siguiente track en RAM) |

---

## Modo daemon (descargas en background)

Para que las descargas continúen aunque cierres la sesión SSH (homelab) o reinicies el CLI, ejecuta el binario como **daemon headless**. El daemon:

- Es dueño de la sesión Telethon (`*.session` SQLite — solo un proceso a la vez puede abrirla).
- Corre el `DownloadManager` y reanuda automáticamente jobs `paused`.
- Expone un socket Unix (`state/daemon.sock`) con permisos `0600` para comandos vía IPC.
- Acepta `SIGTERM`/`SIGINT` para shutdown limpio: jobs activos pasan a `paused` (no se pierde progreso).

### Subcomandos

```bash
telegram-audio-dl                  # modo interactivo (default)
telegram-audio-dl daemon           # daemon en foreground (para systemd / launchd / nohup)
telegram-audio-dl daemon --detach  # fork + setsid; el padre devuelve 0
telegram-audio-dl status           # imprime tabla de jobs (requiere daemon)
telegram-audio-dl status --watch   # refresca cada 2s (Ctrl+C para salir)
telegram-audio-dl status --json    # salida JSON cruda (para scripts)
telegram-audio-dl cancel <job_id>  # cancela un job activo
telegram-audio-dl stop-daemon      # SIGTERM al daemon, espera shutdown limpio
telegram-audio-dl player           # solo biblioteca local, sin Telethon ni daemon
```

### Coexistencia daemon ↔ CLI interactivo

**Una sesión Telethon = un proceso.** Mientras el daemon corre, el CLI interactivo NO puede abrir Telethon:

| Acción del menú | Sin daemon | Con daemon corriendo |
|---|---|---|
| 🔍 Buscar canales en Telegram | ✓ | ✗ ("detén el daemon primero") |
| 🌐 Reproducción online (streaming) | ✓ | ✗ (Telegram ocupado por daemon) |
| ⏬ Reanudar descargas pendientes | ✓ | ✗ |
| 📊 Ver descargas en curso | ✓ (manager local) | ✓ (vía IPC al daemon) |
| 📚 Biblioteca local | ✓ | ✓ |
| 🎵 Reproducir música de carpeta | ✓ | ✓ |

Cuando el CLI detecta el daemon (`state/daemon.pid` existe y el PID está vivo), avisa al inicio y muestra solo las opciones compatibles. Para usar las opciones de Telegram, ejecuta `telegram-audio-dl stop-daemon` primero.

### Cómo correr el daemon en homelab

#### Opción A: systemd (Linux)

```bash
# 1. Copia y edita el template
cp scripts/telegram-audio-dl.service ~/.config/systemd/user/
$EDITOR ~/.config/systemd/user/telegram-audio-dl.service
#    → ajusta WorkingDirectory, ExecStart y User=

# 2. Habilita e inicia
systemctl --user daemon-reload
systemctl --user enable --now telegram-audio-dl
systemctl --user status telegram-audio-dl

# 3. Logs
journalctl --user -u telegram-audio-dl -f
# o el log file directo
tail -f state/logs/daemon.log
```

#### Opción B: launchd (macOS)

```bash
cp scripts/com.telegram-audio-dl.plist ~/Library/LaunchAgents/
$EDITOR ~/Library/LaunchAgents/com.telegram-audio-dl.plist
#    → ajusta paths absolutos
launchctl load ~/Library/LaunchAgents/com.telegram-audio-dl.plist

# Estado
launchctl list | grep telegram-audio-dl

# Detener
launchctl unload ~/Library/LaunchAgents/com.telegram-audio-dl.plist
```

#### Opción C: rápido sin servicio (para probar)

```bash
nohup telegram-audio-dl daemon > /dev/null 2>&1 &
disown
```

### Comandos remotos / desde otra terminal

Mientras el daemon corre en el homelab, puedes controlarlo desde otra terminal SSH:

```bash
# Ver estado en tiempo real
telegram-audio-dl status --watch 5

# Cancelar un job
telegram-audio-dl status                 # copia el job_id
telegram-audio-dl cancel a3f1b2c4

# JSON para scripts
telegram-audio-dl status --json | jq '.jobs[] | select(.state=="running")'

# Detener daemon limpiamente
telegram-audio-dl stop-daemon
```

### Protocolo IPC

Socket Unix line-delimited JSON. Permisos `0600` (solo el dueño). Mensajes documentados en `src/telegram_audio_dl/ipc.py`. Ejemplo manual con `socat`:

```bash
echo '{"cmd":"status"}' | socat - UNIX-CONNECT:state/daemon.sock
```

### Lifecycle / persistencia

- `state/daemon.pid`: PID del daemon vivo. Si está pero el proceso no existe, el siguiente arranque limpia el archivo stale.
- `state/daemon.sock`: socket Unix. Se borra al shutdown limpio; archivos zombi de crashes se limpian automáticamente al próximo `start`.
- `state/logs/daemon.log`: log separado del CLI interactivo (`telegram_audio_dl.log`). Rotación: 2 MiB × 5 backups.
- `state/telegram_audio_dl.db`: la misma base SQLite (con WAL) que el CLI interactivo. El daemon escribe; el CLI puede leer en paralelo (WAL no bloquea reads).

### Player en otra máquina

Caso de uso: daemon corriendo en homelab; quieres reproducir música de TU máquina (laptop) sin tocar el daemon:

```bash
# En la laptop, sin Telethon ni daemon, solo biblioteca local:
telegram-audio-dl player
```

Esto requiere que la biblioteca local esté accesible (NFS / Syncthing / SSH FUSE / copia manual). El subcomando `player` no abre Telethon ni IPC — solo lee `state/telegram_audio_dl.db` y reproduce con mpv/ffplay.

---

## Estados de los jobs

| Estado | Color | Significado |
|---|---|---|
| `queued` | gris | En cola, esperando al worker |
| `running` | cyan | Descargando ahora |
| `done` | verde | Completado |
| `failed` | rojo | Error fatal — ver `error` y logs |
| `cancelled` | amarillo | El user lo canceló explícitamente |
| `paused` | magenta | El CLI se cerró mientras corría — **se retoma automáticamente** al volver a abrir y conectarse |

**Concurrencia**: 1 job a la vez (evita rate limits de Telegram). Múltiples jobs encolados se procesan en orden FIFO.

**Auto-reanudación**: cuando cierras el CLI con descargas activas (`queued` o `running`), pasan a `paused` y se persisten en la tabla `jobs` de SQLite. La próxima vez que entres y se conecte el manager a Telegram, `_auto_resume_paused` los detecta y los reencola con sus pendientes — verás `▶ Reanudando N descarga(s) pausada(s) automáticamente.`. **No necesitas hacer nada manual.** Si un job paused no tiene state local (ej. fue encolado y cerrado antes de arrancar el worker), se reconstruye desde Telegram con `list_audios`.

---

## Selección por rangos (multi-select)

Cuando un canal tiene >100 audios y eliges "Seleccionar algunos":

```
1500 audios disponibles. La tabla muestra los primeros 50; para ver más, escribe more.
Formato de rangos: 1-50,100-200,500  ·  all = todos  ·  vacío = cancelar

? Rangos (1-1500) o 'more' para siguiente página: ▌
```

| Input | Resultado |
|---|---|
| `1-100` | Items 1 al 100 |
| `1-50,100-200` | Dos rangos disjuntos |
| `5,7,12-20` | Mezcla de números sueltos y rangos |
| `all` | Todos |
| `more` | Siguiente página de la tabla |
| `prev` | Página anterior |
| vacío | Cancelar |

---

## Logs y troubleshooting

### Dónde están los logs

```
state/logs/telegram_audio_dl.log     # log actual (max 2 MiB)
state/logs/telegram_audio_dl.log.1   # rotación previa
state/logs/telegram_audio_dl.log.2   # …
```

### Niveles

```bash
# Default: INFO (eventos clave)
~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl

# Verboso: DEBUG (cada tecla del reproductor, cada chunk de descarga)
LOG_LEVEL=DEBUG ~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl
```

### Qué se loguea

| Componente | INFO | DEBUG |
|---|---|---|
| `cli` | Inicio, opciones de menú, errores | Cada tecla presionada |
| `client` | Connect, list_channels, list_audios | — |
| `download_manager` | Job lifecycle (enqueued/running/done/failed) | — |
| `downloader` | Inicio de cada archivo, errores | Offset al reanudar |
| `player` | mpv start/stop, tiempo de socket, errores con stderr | Comandos enviados |
| `telethon` (externo) | WARNING+ (FloodWait, sesión expirada) | — |

### Diagnóstico rápido

```bash
# Últimas 50 líneas
tail -50 state/logs/telegram_audio_dl.log

# Solo errores
grep -E "ERROR|WARNING" state/logs/telegram_audio_dl.log

# Eventos de un job específico
grep "Job a3f1" state/logs/telegram_audio_dl.log

# Stream en vivo
tail -f state/logs/telegram_audio_dl.log
```

### Problemas comunes

| Síntoma | Causa probable | Fix |
|---|---|---|
| `sqlite3.OperationalError: database is locked` (telethon) | Otra instancia del CLI corriendo | `pkill -f telegram_audio_dl` y reintenta |
| `mpv falló: mpv no creó el socket IPC` | Timeout o config de mpv | Ya arreglado a 10s. Pega el stderr del log si vuelve |
| `RuntimeError: asyncio.run() cannot be called from a running event loop` | Mezclar questionary `.ask()` con async | Usar `.ask_async()` |
| `WARNING: Ignoring invalid distribution -X` | Archivos AppleDouble en venv en SSD externo | Crear el venv en `~/.virtualenvs/` (volumen APFS) |
| Streaming corta a media canción | Conexión Telegram inestable | Reintentar; el caché de mpv (10s) ayuda en recuperación corta |
| Descarga se detiene sin error visible | El job pasó a `paused` por cierre del CLI | Auto-reanuda al volver a abrir y conectar |
| Biblioteca aparece vacía aunque hay archivos | DB no migrada desde JSON viejo | `python tools/migrate_json_to_sqlite.py` |

---

## Archivos persistentes

| Archivo | Propósito | Tamaño típico |
|---|---|---|
| `*.session` | Sesión Telethon (autenticación) | ~50 KB |
| `state/telegram_audio_dl.db` | **SQLite**: canales, files, jobs | ~10-15 MB para 30K entries |
| `state/logs/*.log` | Logs rotativos | hasta 12 MB (2 MB × 6 versiones) |

Todos están en `.gitignore`. Backup recomendado antes de borrar el `state/`.

### Schema SQLite

```sql
channels(channel_id PK, channel_name, last_seen)
files(channel_id, message_id, filename, size, downloaded_bytes,
      completed, sha256, destination_dir, updated_at)
      PRIMARY KEY (channel_id, message_id)
jobs(job_id PK, channel_id, channel_name, destination, state, ...,
     started_at, finished_at, enqueued_at,
     completed_count, skipped_count, failed_count,
     bytes_done_session, bytes_done_total, total_bytes,
     total_files, channel_total_files, error)
```

Modo **WAL** activo: lecturas no bloquean al writer.

### Inspección manual

```bash
sqlite3 state/telegram_audio_dl.db

sqlite> SELECT channel_name, COUNT(*) AS total, SUM(completed) AS done
        FROM files JOIN channels USING(channel_id)
        GROUP BY channel_id;

sqlite> SELECT job_id, channel_name, state, completed_count || '/' || total_files
        FROM jobs ORDER BY enqueued_at DESC;
```

### Migración desde JSON (si vienes de versión anterior)

```bash
~/.virtualenvs/telegram-audio-dl/bin/python tools/migrate_json_to_sqlite.py
```

Lee `state/*.json` + `state/_jobs_history.json`, popula la DB y renombra los JSON a `.bak` por seguridad.

---

## Tests

```bash
~/.virtualenvs/telegram-audio-dl/bin/pytest tests/
```

**216 tests** cubren:

| Suite | Tests | Qué cubre |
|---|---|---|
| `test_config.py` | 5 | Carga `.env`, validación, defaults |
| `test_state.py` | 7 | StateStore/FileEntry, reconcile_with_disk |
| `test_downloader_pure.py` | 10 | filename sanitizer, ext por mime, sha256, dedup |
| `test_client_pure.py` | 7 | Parser de `DocumentAttributeAudio` y `DocumentAttributeFilename` |
| `test_cli_format.py` | 12 | `_fmt_size`, `_fmt_duration`, `_safe_dirname` |
| `test_metadata_and_format.py` | 10 | mutagen, `_fmt_mmss`, `_stringify_tag` |
| `test_pagination.py` | 13 | `_parse_ranges` (rangos, dedupe, edge cases) |
| `test_pending.py` | 9 | `_scan_pending` + `_sync_state_with_audios` |
| `test_library.py` | 17 | `_scan_audio_folder`, `_scan_library`, `_sample_shuffle` |
| `test_inventory_and_manager.py` | 16 | `Downloader.inventory`, `DownloadManager` (sin red) |
| `test_history_and_player.py` | 23 | History persistence, paused/auto-resume, player panel, streaming single-track |
| `test_logging_setup.py` | 8 | Niveles, no-duplicate handlers, telethon logger |
| `test_scenarios.py` | 10 | E2E con mock Telethon: download, cancel, resume, FloodWait, dedup |
| `test_streaming.py` | 12 | `Prefetch`, `_prefetch_audio`, cola con auto-avance + lookahead, cancelación, navegación prev/next, metadata de `PlaybackQueue` |
| `test_paginated_select.py` | 9 | Guard contra el bug de questionary: filter string del usuario no matchante → None en vez de propagarse y romper |
| `test_audio_player_detection.py` | 14 | Detector cross-platform (afplay/ffplay/mpg123/paplay), prioridad por OS, mapeo de duración a flags por binario |
| `test_ipc.py` | 16 | Protocolo IPC daemon: server, cliente, errores, permisos 0600, concurrencia, detección de daemon vía PID file |
| `test_entrypoint.py` | 12 | Subcomandos argparse (`daemon`, `status`, `cancel`, `stop-daemon`, `player`), dispatch, fallos graciosos sin daemon |
| `tests/_db_helpers.py` | (helpers) | `seed_channel_files`, `seed_job` para fixtures |

**Tiempo total: ~0.6 segundos**.

No están cubiertos automáticamente (requieren entorno real):
- `list_channels`, `list_audios`, `iter_download` con red Telegram.
- Flujo interactivo de questionary (necesita TTY).
- mpv binario para controles del reproductor.
- Streaming end-to-end (requiere mpv + red). Los tests usan `_stream_one_track` mockeado para verificar la orquestación de la cola sin levantar mpv.

Para ejecutarlos manualmente, ver [`docs/escenarios.md`](docs/escenarios.md).

---

## FAQ

**¿Por qué un job dice `36/14898` y otro `181/15102` para el mismo canal?**

Cada job se construye con los **pendientes en el momento de encolar**. Si encolaste 15102 y descargaste 181 antes de interrumpir, al reanudar quedan 14921. Los completados anteriores no se reencolan (dedup). La columna "Archivos" muestra `progreso/total_de_este_job (canal: total_absoluto)` para evitar la confusión.

**¿Cómo bajo música nueva que se agregó al canal después de mi primera descarga?**

Al elegir "Reanudar descargas pendientes" → eliges canal → te pregunta `¿Sincronizar con Telegram?`. Di que sí. Consulta el canal, compara `message_id` por `message_id` contra el state local, y agrega entries nuevas. Esos audios aparecen como pendientes en el job que se va a encolar. Si saltas la sincronización, solo procesa lo que ya conoces.

**¿Puedo descargar de varios canales en paralelo?**

No. El worker procesa un job a la vez para evitar `FloodWaitError` de Telegram. Si encolas dos, el segundo espera al primero.

**¿Cómo cancelo una descarga sin cerrar el CLI?**

Menú principal → "Ver descargas en curso" → "Cancelar un trabajo" → eliges el job. El cancelflag se respeta al siguiente chunk descargado.

**¿La sesión expira?**

No automáticamente. Permanece válida hasta que la cierres manualmente desde otra app de Telegram (Settings → Active Sessions → Terminate).

**¿Qué pasa si el archivo en disco es más grande que `size` del checkpoint?**

`reconcile_with_disk` ajusta: si `actual > size`, asume que el archivo está corrupto y reinicia desde 0. Si `actual <= size`, toma `actual` como nuevo punto de partida.

**¿Funciona en Linux?**

Sí. El detector `find_simple_audio_player()` resuelve la diferencia con macOS:

| Tarea | macOS | Linux |
|---|---|---|
| Reproducción con controles | `mpv` | `mpv` (igual) |
| Reproducción simple (fallback) | `afplay` (nativo) | `ffplay` → `mpg123` → `paplay` |
| Preview 30s del menú "Buscar canales" | `afplay -t 30` | `ffplay -t 30` (o `mpg123 -n 1140`) |
| Keyboard listener | `termios` | `termios` (POSIX, idéntico) |
| mpv IPC (Unix socket en `/tmp`) | ✓ | ✓ |

Lo único que se rompería en una máquina Linux mínima sin `ffmpeg` ni `mpg123`: la opción de Biblioteca local y la Vista previa quedarían deshabilitadas. Streaming online y descargas funcionan independientemente.

**¿Funciona en Windows?**

No probado. El keyboard listener usa `termios` (POSIX-only). Habría que adaptar con `msvcrt`. Linux y macOS sí están cubiertos.

**¿Cómo veo qué proceso `mpv` está corriendo?**

```bash
ps aux | grep "mpv --input-ipc-server"
```

Cada playback crea un socket en `/tmp/tg-mpv-XXXXXXXX.sock`.

**¿Por qué el track 2 arranca casi instantáneo en streaming pero el 1 sí tarda?**

El primer track no tiene pre-fetch previo: empieza al mismo tiempo que `iter_download`, así que arranca cuando llega el primer chunk de Telegram (~1-3s). Mientras suena, el track 2 está siendo pre-descargado a un `asyncio.Queue` en RAM. Cuando termina el 1, mpv del 2 abre stdin y consume el buffer ya listo → arranque <1s. Lo mismo para el 3 cuando empieza el 2.

**¿Cuánta RAM consume el pre-fetch?**

Tope teórico: `maxsize=128 chunks × 256 KB = 32 MB` por prefetch activo. Como solo se pre-fetch el siguiente (lookahead=1), el peak es ~32 MB. En la práctica, los audios típicos pesan 6-15 MB y el queue nunca se llena.

**¿Qué pasa si Telegram corta a mitad del pre-fetch?**

`prefetch.failed = True` y se encola sentinel `None`. Cuando el track actual termina y el feed task del siguiente empieza a drenar, recibe el sentinel sin haber escrito todo → mpv recibe stdin truncado y termina prematuro. La cola avanza al siguiente. No hay retry automático en esta versión (ver tabla "E. Resilencia" en el roadmap).

**¿Puedo reordenar la cola en runtime?**

No en esta versión. La cola es fija desde el momento de la selección. Para saltar al siguiente usa `n`; para retroceder al anterior, `p`.

**¿Qué pasa si presiono `p` en el primer track de la cola?**

Lo reinicia desde el principio (no underflowea ni va al final de la cola). Es equivalente a `0` (seek al inicio) pero re-arranca mpv desde cero. Si lo que quieres es solo volver al inicio del track sin reiniciar el prefetch, usa `0`.

**¿Por qué `p` y `n` y no flechas izquierda/derecha?**

Las flechas ya están asignadas a seek (±10s), que es la convención de mpv y la mayoría de reproductores. Usar `p`/`n` (previous/next) es la convención de cmus/vimusic/spotify-tui y evita conflicto.

---

## Limitaciones conocidas

- **macOS y Linux**: ambos plenamente soportados con detección automática del reproductor de respaldo (`afplay` en macOS; `ffplay`/`mpg123`/`paplay` en Linux). Windows no probado (`termios` es POSIX-only).
- **Una descarga concurrente**: para evitar rate limits Telegram.
- **Streaming**: usa **🌐 Reproducción online (streaming)** del menú principal (requiere `mpv`). Bytes pasan por RAM, nunca por disco. Soporta uno solo, selección o canal completo en cola con auto-avance y pre-fetch del siguiente track. Lookahead de 1 (no se pre-cargan más de un track adelante para acotar RAM). Sin reanudación entre sesiones — al cerrar el CLI, la cola se pierde.
- **Sin live refresh en `_jobs_view` por defecto**: hay que entrar a "Ver en vivo" porque `Live` y `questionary` no coexisten en el mismo TTY.
- **`mpv` opcional**: sin él, no hay controles (afplay no acepta input).

---

## Operación día-a-día

### Empezar una nueva descarga grande

```
1. telegram-audio-dl
2. → 🔍 Buscar canales en Telegram
3. → eliges canal → "⬇️ Descargar todos"
4. → confirma carpeta destino
5. → ✓ Encolado (state pre-inventariado en SQLite, vuelves al menú)
6. → 📊 Ver descargas en curso → "📺 Ver en vivo" para monitorear
7. Cierras la terminal cuando quieras → job pasa a "paused"
```

### Retomar al día siguiente (auto-reanudación)

```
1. telegram-audio-dl
2. → Buscar canales en Telegram (o cualquier opción que conecte el manager)
3. → "Conectando a Telegram…"
4. → "▶ Reanudando 1 descarga(s) pausada(s) automáticamente."
5. → ya estás de vuelta en el menú; las descargas continúan en background
```

Para retomar también con **audios nuevos del canal**, usa "Reanudar descargas pendientes" → eliges canal → "¿Sincronizar con Telegram?" → Sí. Eso busca audios nuevos en el canal y los agrega al state antes de reencolar.

Si saltas la sincronización, solo se reencolan los pendientes ya conocidos. Útil cuando estás offline o cuando sabes que el canal no cambió.

### Escuchar lo descargado mientras se baja más

```
1. (tienes una descarga corriendo en background)
2. → 📚 Biblioteca local
3. → 🎲 Shuffle global   (50 al azar de toda la biblioteca)
   o → ▶️ Reproducir un canal completo
4. Suena con panel multimedia + controles mpv
5. La descarga sigue en background sin afectarse
```

### Probar antes de descargar

```
1. → 🌐 Reproducción online (streaming)
2. → eliges canal
3. → 🎵 Uno solo → eliges audio → suena inmediato sin tocar disco
4. Ctrl+C / q al terminar → nada queda local
5. Si te gusta: → 🔍 Buscar canales en Telegram → "Descargar todos" o "Seleccionar algunos"
```

### Catar varios tracks de un canal nuevo en cola

```
1. → 🌐 Reproducción online (streaming)
2. → eliges canal
3. → 🔀 Selección (cola)  →  marca 5-10 audios (espacio para checkbox, o rangos si >100)
4. Suena el primero con panel COLA mostrando ↶ Anterior · ▶ Ahora · ↷ Siguientes 6
5. Mientras suena, el siguiente ya se está pre-descargando en RAM
6. Al terminar el actual: arranque <1s del siguiente (gracias al pre-fetch)
7. n = siguiente  ·  p = anterior  ·  q o Ctrl+C = salir de toda la cola
8. Si te convence el canal: → menú principal → 🔍 Buscar canales → "Descargar todos"
```

### Limpiar todo y empezar de cero

```bash
rm -rf state/ telegram_audio_dl.session
# ⚠ pierdes la DB SQLite (channels, files, jobs) y los logs.
#   Los archivos descargados en sus carpetas se mantienen intactos.
```
