# telegram-audio-dl — guía para Claude Code

CLI interactivo en Python para descargar archivos de audio/música de canales de Telegram suscritos en una cuenta personal.

## Alcance

- Autenticación con cuenta de usuario (no bot) vía MTProto / Telethon.
- Listar canales suscritos.
- Buscar canal por nombre (autocomplete).
- Listar archivos de audio del canal seleccionado.
- Descargar todos los audios (con dedup contra checkpoint local) o selección manual (multi-select).
- Carpeta destino configurable. Default: `~/Downloads/<nombre-del-canal>/`.
- Checkpoint JSON por canal para reanudar descargas interrumpidas.

## Fuera de alcance

- Modo headless / no interactivo (puede agregarse después).
- Descarga de otros tipos de media (video, fotos, documentos).
- Subida de archivos.
- Soporte para bots (requiere otro flujo de autenticación).

## Stack

| Componente | Librería |
|---|---|
| Cliente Telegram | `telethon` |
| Menú interactivo | `questionary` |
| Progreso y logging | `rich` |
| Carga de `.env` | `python-dotenv` |

Python 3.10+. Type hints en todas las funciones.

## Estructura

```
AUT-03 — Telegram Audio Downloader/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── state/                       # checkpoint JSON por canal (gitignored)
└── src/telegram_audio_dl/
    ├── __init__.py
    ├── __main__.py              # python -m telegram_audio_dl
    ├── cli.py                   # menú questionary, async
    ├── client.py                # wrapper Telethon: list_channels, list_audios
    ├── config.py                # carga .env
    ├── downloader.py            # iter_download con resume + progress bar
    └── state.py                 # checkpoint JSON: load/save/mark_*
```

## Credenciales

- `API_ID` y `API_HASH` se obtienen en [my.telegram.org](https://my.telegram.org).
- Se guardan en `.env` local. **Nunca** commitear este archivo.
- La sesión de Telethon se persiste en `telegram_audio_dl.session` (gitignored). Tras el primer login con código SMS / Telegram, no se vuelve a pedir.

## Reglas operativas

- No mocks: las pruebas se hacen contra la cuenta real con un canal de prueba.
- Si una descarga falla, no se reintenta automáticamente — el checkpoint queda en `downloaded_bytes` actual y al re-correr se reanuda desde ahí.
- El checkpoint registra `sha256` solo cuando el archivo se completa, para auditoría futura.
- Al borrar manualmente un archivo del filesystem, en la próxima corrida se detecta y se vuelve a marcar como pendiente.

## Despliegue (genérico)

El proyecto puede correr en cualquier host con Python 3.10+. El wrapper `bin/aut-03-run.sh` integra con Vaultwarden/Bitwarden si quieres tener los secrets fuera del `.env`. Configurable vía `AUT03_ROOT` y `AUT03_ITEM_NAME`.

Para una guía paso-a-paso de despliegue como systemd service o launchd agent, ver `scripts/telegram-audio-dl.service` y `scripts/com.telegram-audio-dl.plist`.

---

## Tests

```bash
~/.virtualenvs/telegram-audio-dl/bin/pytest -q
```

165+ tests automatizados. Sin red Telegram real (mocks de Telethon).

---

## Referencias

- Telethon docs: <https://docs.telethon.dev/>
- Filtros de mensajes: `InputMessagesFilterMusic` (audios musicales con metadata) vs `InputMessagesFilterVoice` (notas de voz). Este proyecto usa **Music**.
