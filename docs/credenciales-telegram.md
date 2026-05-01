---
fecha_de_creación: 2026-04-30
última_modificación: 2026-04-30
---

# Credenciales de Telegram — registro

Este documento describe cómo se obtuvieron las credenciales y dónde viven los valores reales. **No transcribe los secrets** — viven solo en `.env` (gitignored).

## Resumen

| Campo | Ubicación del valor |
|---|---|
| `App api_id` (entero) | `.env` → `TELEGRAM_API_ID` |
| `App api_hash` (string hex) | `.env` → `TELEGRAM_API_HASH` |
| Teléfono internacional | `.env` → `TELEGRAM_PHONE` |
| App title | `telegram-audio-dl` |
| Short name | `tgaudiodl` |
| Plataforma | Desktop |

## Origen

Credenciales generadas en <https://my.telegram.org> → **API development tools**, usando la cuenta personal del usuario y el número configurado en `TELEGRAM_PHONE`.

## Capturas (locales, gitignored)

Las capturas con los valores en claro están en `assets-local/` que está en `.gitignore`. **No** se incluyen como `![]()` en este `.md` para evitar que el `api_hash` quede embebido en el vault sincronizable.

- `assets-local/my-telegram-org-app-config.png` — vista principal con `api_id`, `api_hash`, `App title`, `Short name`, y los DCs de prueba/producción.
- `assets-local/my-telegram-org-public-keys.png` — segunda llave pública RSA.

## Higiene de secrets

- ⚠ El `api_hash` es un **secret**. Combinado con el número de teléfono permite emitir tokens de sesión nuevos.
- El archivo `.env` **nunca** se commitea (`.gitignore`).
- La carpeta `assets-local/` **nunca** se commitea (`.gitignore`).
- La sesión persistida (`telegram_audio_dl.session`) tampoco (`.gitignore`).
- Si las capturas pasaron por iCloud/Dropbox/backup automático, considerar **regenerar `api_hash`** en my.telegram.org.

## Servidores MTProto (informativo)

| Entorno | Endpoint | DC |
|---|---|---|
| Test | `149.154.167.40:443` | DC 2 |
| Production | `149.154.167.50:443` | DC 2 |

Telethon resuelve esto automáticamente — no se configura manualmente.
