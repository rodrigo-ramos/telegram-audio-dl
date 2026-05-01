---
fecha_de_creación: 2026-04-30
última_modificación: 2026-04-30
---

# Matriz de escenarios — telegram-audio-dl

Cobertura de cada flujo posible del CLI con su estado actual.

**Leyenda**:
- ✅ test automatizado (en `tests/`)
- 🧪 prueba manual con instrucciones
- 📝 comportamiento documentado, sin verificación automática (requiere red real)

---

## A. Setup y configuración

| # | Escenario | Comportamiento esperado | Cobertura |
|---|---|---|---|
| A1 | Ejecutar sin `.env` | Error: "Faltan variables en .env" | ✅ `test_load_config_missing_*` |
| A2 | `.env` sin `TELEGRAM_API_ID` | RuntimeError mencionando `TELEGRAM_API_ID` | ✅ `test_load_config_missing_api_id_raises` |
| A3 | `.env` sin `TELEGRAM_PHONE` | RuntimeError mencionando `TELEGRAM_PHONE` | ✅ `test_load_config_missing_phone_raises` |
| A4 | `api_id` no numérico | RuntimeError "entero" | ✅ `test_load_config_invalid_api_id_raises` |
| A5 | `.env` válido, defaults | Carga config con `~/Downloads`, session `telegram_audio_dl` | ✅ `test_load_config_defaults_session_and_download_root` |
| A6 | `LOG_LEVEL=DEBUG` | Logger app a DEBUG | ✅ `test_setup_respects_log_level_env` |
| A7 | Llamar `setup_logging` 2x | No duplica handlers | ✅ `test_setup_does_not_duplicate_handlers` |

---

## B. Login y sesión Telegram

| # | Escenario | Comportamiento esperado | Cobertura |
|---|---|---|---|
| B1 | Primer login | Pide código por Telegram (oficial), opcional 2FA | 📝 manual |
| B2 | Sesión guardada existe | Conecta sin pedir código | 📝 manual |
| B3 | Sesión inválida (logout remoto) | Re-pide código | 📝 manual |
| B4 | Conexión exitosa loguea `Authenticated as: id=…` | Logger info a archivo | ✅ vía `client.py` con logger |

---

## C. Menú principal

| # | Escenario | Resultado | Cobertura |
|---|---|---|---|
| C1 | Selecciona "🔍 Buscar canales en Telegram" | Conecta cliente lazy → `_select_channel` | 🧪 manual |
| C2 | Selecciona "⏬ Reanudar descargas pendientes" | Conecta lazy → `_resume_flow` | 🧪 manual |
| C3 | Selecciona "📊 Ver descargas en curso" | Sin conexión Telegram → `_jobs_view` | 🧪 manual |
| C4 | "📊" sin manager (pre-conexión) | Mensaje "No hay descargas iniciadas todavía" | 🧪 manual |
| C5 | Selecciona "🎵 Reproducir música de una carpeta" | Sin conexión → `_folder_player_flow` | 🧪 manual |
| C6 | Salir sin descargas activas | Sale directo (return 0) | 🧪 manual |
| C7 | Salir con descargas en curso | Confirma "¿Salir? (quedarán pausadas y se reanudarán al volver a abrir)" default Yes | 🧪 manual |
| C8 | Salir → No → vuelve al menú | No cierra | 🧪 manual |

---

## D. Selector de canales (Telegram)

| # | Escenario | Resultado | Cobertura |
|---|---|---|---|
| D1 | <100 canales | Selector simple con autocomplete | 🧪 manual |
| D2 | ≥100 canales | Paginación con anterior/siguiente/primera/última | ✅ helper `_paginated_select` cubierto por uso |
| D3 | "↩️ Menú principal" | Devuelve None → vuelve al top | 🧪 manual |
| D4 | Selecciona canal con audios | `list_audios` + tabla truncada (50 filas) | 🧪 manual |
| D5 | Selecciona canal sin audios | "[yellow]Este canal no tiene audios.[/yellow]" → vuelve | 🧪 manual |

---

## E. Acciones de canal (descarga)

| # | Escenario | Resultado | Cobertura |
|---|---|---|---|
| E1 | "⬇️ Descargar todos" | Encola job al manager → vuelve al menú | ✅ `test_scenario_simple_download` |
| E2 | "✅ Seleccionar algunos" <100 audios | Checkbox de questionary | 🧪 manual |
| E3 | "✅ Seleccionar algunos" ≥100 audios | Modo rangos `1-50,100-200` | 🧪 manual + ✅ `_parse_ranges` cubierto |
| E4 | "👀 Vista previa" con mpv disponible | Selector de audio → preview 30s con afplay | 🧪 manual |
| E5 | "👀 Vista previa" sin afplay | Opción no aparece | 🧪 manual |
| E6 | "↩️ Volver a canales" | Vuelve al selector | 🧪 manual |

---

## F. Selección por rangos

| # | Input | Resultado | Cobertura |
|---|---|---|---|
| F1 | `1-50` | Items 1-50 | ✅ `test_parse_ranges_simple_range` |
| F2 | `1,3,5` | Items específicos | ✅ `test_parse_ranges_multiple_numbers` |
| F3 | `1-3,7,10-12` | Mezcla rangos+sueltos | ✅ `test_parse_ranges_mixed` |
| F4 | `5,1-3,2` | Deduplicado y ordenado | ✅ `test_parse_ranges_deduplicates_and_sorts` |
| F5 | ` 1 - 5 , 10 ` | Tolera espacios | ✅ `test_parse_ranges_with_whitespace` |
| F6 | `1-150` con total=100 | None (rechazado) | ✅ `test_parse_ranges_rejects_out_of_bounds` |
| F7 | `10-5` | None (rango inverso) | ✅ `test_parse_ranges_rejects_inverse_range` |
| F8 | `abc` | None (no numérico) | ✅ `test_parse_ranges_rejects_non_numeric` |
| F9 | `""` | None (vacío) | ✅ `test_parse_ranges_rejects_empty_string` |
| F10 | `all` | Todos los audios | 🧪 manual (lógica en `_select_by_ranges`) |
| F11 | `more` / `prev` | Cambia página de tabla | 🧪 manual |

---

## G. Carpeta de destino

| # | Escenario | Default propuesto | Cobertura |
|---|---|---|---|
| G1 | Primera descarga de la sesión | `<project_root>/<safe_dirname(canal)>` | 🧪 manual |
| G2 | Descarga subsecuente en la misma sesión | `<last_parent>/<safe_dirname(canal)>` | 🧪 manual |
| G3 | Tab autocomplete | `questionary.path()` muestra opciones del FS | 🧪 manual |
| G4 | Vacío | None → cancela el flujo | 🧪 manual |
| G5 | Path con `~` | Expandido a home | 🧪 manual |
| G6 | Path no existe | `mkdir(parents=True, exist_ok=True)` | 🧪 manual |

---

## H. Encolado y descarga (DownloadManager)

| # | Escenario | Resultado | Cobertura |
|---|---|---|---|
| H1 | `enqueue` crea state file | Pre-inventario, todos los audios pendientes | ✅ `test_scenario_preinventory_persists_before_run` |
| H2 | Worker procesa job | Estado: queued → running → done | ✅ `test_scenario_simple_download` |
| H3 | Archivo ya completo en disco | Skip (no descarga, `skipped_count++`) | ✅ `test_scenario_skips_already_complete` |
| H4 | Archivo parcial → reanuda | `iter_download` con `offset` | ✅ `test_scenario_resume_partial_file` |
| H5 | Mensaje borrado del canal | `failed_count++`, sigue con el siguiente | ✅ `test_scenario_deleted_message_marks_failed` |
| H6 | Error de red en un archivo | `failed_count++`, sigue | ✅ `test_scenario_network_error_marks_failed_continues` |
| H7 | Cancel en queued | Job nunca corre | ✅ `test_scenario_cancel_before_run` |
| H8 | Cancel en running | Corta al siguiente chunk → state cancelled | ✅ `test_scenario_cancel_during_running` |
| H9 | Múltiples jobs encolados | Procesan en orden FIFO secuencial | ✅ `test_scenario_jobs_run_sequentially` |
| H10 | `stop()` con job running | Marca paused (no cancelled) y persiste | ✅ `test_scenario_stop_marks_running_as_paused` |
| H11 | `channel_total_files` reflejado tras pre-inventario | Igual al state actual | ✅ `test_enqueue_creates_state_via_preinventory` |
| H12 | Encolar canal con state previo | `channel_total_files` = total absoluto | ✅ `test_enqueue_reads_channel_total_from_state` |
| H13 | Checkpoint cada 4 MiB | `store.save()` en bucle de chunks | ✅ implícito en H4 |
| H14 | sha256 al completar | Calculado y persistido | ✅ implícito en `Downloader._download_one` |

---

## I. Reanudar descargas pendientes

| # | Escenario | Resultado | Cobertura |
|---|---|---|---|
| I1 | Sin pending en state | "[yellow]No hay descargas pendientes ni interrumpidas.[/yellow]" | 🧪 manual |
| I2 | Tabla con Total/Completados/Pendientes/% | Render correcto | ✅ `test_pending_includes_completed_and_total_counts` |
| I3 | Selecciona canal → No sincronizar | Encola lo conocido | 🧪 manual |
| I4 | Selecciona canal → Sí sincronizar (sin nuevos) | "✓ No hay audios nuevos." | ✅ `test_sync_no_changes_when_all_known` |
| I5 | Sí sincronizar (con nuevos) | "✓ N nuevos agregados, M conocidos" | ✅ `test_sync_adds_new_audios_to_state` |
| I6 | Sí sincronizar pero falla (red) | "[red]Error sincronizando[/red] … Continuando con state actual" | 🧪 manual |
| I7 | Default carpeta = última usada o `<project_root>/<canal>` | Path correcto en prompt | 🧪 manual |

---

## J. Ver descargas en curso

| # | Escenario | Resultado | Cobertura |
|---|---|---|---|
| J1 | Sin jobs en historial | "No hay descargas en el historial" → vuelve | 🧪 manual |
| J2 | Tabla render con jobs | Resumen + tabla con todos los estados | 🧪 manual |
| J3 | Estado `running` cyan, `done` verde, `paused` magenta, etc. | Color correcto | 🧪 manual |
| J4 | Columna "Archivos" muestra `(canal: N)` cuando difiere | Render condicional | 🧪 manual |
| J5 | "🔄 Refrescar" | Re-render con datos actuales | 🧪 manual |
| J6 | "📺 Ver en vivo" | Live + Ctrl+C para volver | 🧪 manual |
| J7 | "❌ Cancelar trabajo" | Selector de jobs cancellables → marca cancel | ✅ `test_request_cancel_marks_queued_or_running_jobs` |
| J8 | "❌ Cancelar trabajo" sobre done | No aparece en lista | ✅ `test_request_cancel_skips_finished_jobs` |
| J9 | "↩️ Menú principal" | Vuelve al top | 🧪 manual |

---

## K. Reproductor

| # | Escenario | Resultado | Cobertura |
|---|---|---|---|
| K1 | mpv disponible → reproductor con controles | Panel con [Espacio], [← →], etc. | ✅ `test_render_player_panel_shows_controls_when_enabled` |
| K2 | mpv NO disponible → fallback afplay | Panel sin controles, mensaje "instala mpv" | ✅ `test_render_player_panel_shows_afplay_hint_when_disabled` |
| K3 | Panel con metadata completa | Título, artista, álbum, bitrate, MM:SS | ✅ `test_render_player_panel_with_metadata` |
| K4 | Panel sin tags ID3 | Usa `path.stem` como título | ✅ `test_render_player_panel_no_metadata_uses_filename` |
| K5 | Pausado | `⏸ PAUSADO` + amarillo | ✅ `test_render_player_panel_paused_shows_pause_label` |
| K6 | Cola — primer track | "Anterior: —" | ✅ `test_render_player_panel_queue_first_track_shows_no_previous` |
| K7 | Cola — track intermedio | Anterior + actual + 6 siguientes | ✅ `test_render_player_panel_shows_queue_section` |
| K8 | Cola — último track | "Siguientes: — (fin de cola)" | ✅ `test_render_player_panel_queue_last_track_shows_end` |
| K9 | "Reproducir uno" sin queue | Panel sin sección COLA | ✅ `test_render_player_panel_no_queue_omits_section` |
| K10 | Tecla Espacio | toggle_pause vía mpv IPC | 🧪 manual con mpv |
| K11 | Tecla → | seek +10s | 🧪 manual con mpv |
| K12 | Tecla ← | seek −10s | 🧪 manual con mpv |
| K13 | Tecla ↑ | seek +30s | 🧪 manual con mpv |
| K14 | Tecla ↓ | seek −30s | 🧪 manual con mpv |
| K15 | Tecla 0 | seek absoluto a 0 | 🧪 manual con mpv |
| K16 | Tecla q | termina mpv, vuelve al selector | 🧪 manual con mpv |
| K17 | Ctrl+C en cola | Termina cola completa | 🧪 manual |
| K18 | mpv socket timeout | Fallback a afplay con mensaje | 🧪 manual (timeout 10s) |

---

## L. Reproducir música de una carpeta

| # | Escenario | Resultado | Cobertura |
|---|---|---|---|
| L1 | Carpeta con extensiones soportadas | Lista archivos `.mp3 .m4a .flac .ogg .wav .aiff …` | ✅ `test_scan_audio_folder_returns_audio_files_only` |
| L2 | Carpeta con AppleDouble (`._*`) | Filtrados | ✅ `test_scan_audio_folder_skips_appledouble` |
| L3 | Recursivo activo | Incluye subcarpetas | ✅ `test_scan_audio_folder_recursive_includes_subdirs` |
| L4 | Recursivo desactivado | Solo el primer nivel | ✅ `test_scan_audio_folder_non_recursive_ignores_subdirs` |
| L5 | Path no es directorio | Lista vacía | ✅ `test_scan_audio_folder_empty_when_not_dir` |

---

## M. Salida y auto-reanudación

| # | Escenario | Resultado | Cobertura |
|---|---|---|---|
| M1 | Salir con job running → marca paused | Persiste en `_jobs_history.json` | ✅ `test_scenario_stop_marks_running_as_paused` |
| M2 | Reabrir CLI con jobs paused | Auto-resume al primer `ensure_client` | ✅ `test_auto_resume_paused_reencola_pendientes` |
| M3 | Auto-resume con state local | Reencola directo sin Telegram | ✅ `test_auto_resume_paused_reencola_pendientes` |
| M4 | Auto-resume con state vacío (huérfano) | Consulta Telegram, reconstruye state, encola | ✅ `test_auto_resume_paused_rebuilds_from_telegram_when_no_state` |
| M5 | Auto-resume sin paused | No-op | ✅ `test_auto_resume_paused_no_jobs` |
| M6 | Auto-resume con state todo done | Skipped | ✅ `test_auto_resume_paused_skips_when_no_pending` |
| M7 | Cargar history viejo con `interrupted` | Migra a `paused` | ✅ `test_history_migrates_old_interrupted_to_paused` |
| M8 | Cargar history con queued/running | Convierte a paused | ✅ `test_history_marks_active_jobs_as_paused_on_load` |
| M9 | History truncado a últimos 50 | `MAX_HISTORY` | ✅ `test_history_truncates_at_max` |
| M10 | History JSON corrupto | Lista vacía sin crash | ✅ `test_history_skips_corrupted_file` |

---

## N. Casos borde y robustez

| # | Escenario | Resultado | Cobertura |
|---|---|---|---|
| N1 | State file con AppleDouble | Filtrado en scan | ✅ `test_scan_pending_skips_appledouble` |
| N2 | State JSON corrupto | Salta, otros canales OK | ✅ `test_scan_pending_skips_corrupted_json` |
| N3 | Audio borrado del filesystem entre sesiones | `reconcile_with_disk` resetea entry | ✅ `test_reconcile_with_disk_file_missing` |
| N4 | Archivo en disco más grande que size esperado | `reconcile` reinicia desde 0 | ✅ `test_reconcile_with_disk_completed_but_size_mismatch` |
| N5 | Checkpoint atrasado vs disco | `reconcile` toma el tamaño del disco | ✅ `test_reconcile_with_disk_partial_file` |
| N6 | `_safe_filename` con caracteres inválidos | Reemplazados por `_` | ✅ `test_safe_filename_strips_invalid_chars` |
| N7 | Filename sin extensión | Agrega `.mp3 .m4a .ogg .audio` según mime | ✅ `test_safe_filename_adds_extension_from_mime` |
| N8 | sha256 calculado | Hash hex de 64 chars | ✅ `test_sha256_of_known_content` |
| N9 | Metadata: archivo no-audio | Retorna None | ✅ `test_read_audio_metadata_returns_none_for_non_audio` |
| N10 | Metadata: archivo no existe | Retorna None | ✅ `test_read_audio_metadata_returns_none_for_missing_file` |

---

## Total

**131 tests automatizados** corriendo en 0.46s.

```bash
~/.virtualenvs/telegram-audio-dl/bin/pytest tests/ -v
```

Distribución:
- `test_config.py` (5)
- `test_state.py` (7)
- `test_downloader_pure.py` (10)
- `test_client_pure.py` (7)
- `test_cli_format.py` (12)
- `test_library.py` (6)
- `test_metadata_and_format.py` (10)
- `test_pagination.py` (13)
- `test_pending.py` (11)
- `test_inventory_and_manager.py` (11)
- `test_history_and_player.py` (21)
- `test_logging_setup.py` (8)
- `test_scenarios.py` (10)

Pruebas manuales necesarias (requieren red Telegram real):
- B1, B2, B3 — login (primera vez, sesión guardada, sesión inválida)
- D1, D3, D5 — selectores con datos reales
- E2, E4 — checkbox y preview con mpv
- I3, I6 — sincronización con red
- J5, J6 — Live refresh
- K10–K18 — controles de mpv vivos

---

## Cómo simular escenarios localmente

### Forzar interrupción (para validar reanudación)

1. Iniciar descarga grande → `~/.virtualenvs/telegram-audio-dl/bin/telegram-audio-dl`.
2. Encolar canal → ver "Ver descargas en curso" → "Ver en vivo".
3. Ctrl+C → vuelve al menú.
4. Salir.
5. Re-abrir → ir a "Buscar canales" para que conecte → ver mensaje "▶ Reanudando N descarga(s) pausada(s)…"

### Forzar dedup

1. Descarga 5 archivos.
2. Repite el mismo encolado → debe decir "Ya estaban: 5".

### Forzar mensaje borrado

(Solo posible con red real.) Borra una canción del canal entre encoladas y reanudaciones — debería marcarla como `failed`.

### Validar pausa al salir

1. Encolar descarga.
2. Salir → "¿Salir? (quedarán pausadas…)" → Yes.
3. Inspecciona `state/_jobs_history.json` → debe contener `"state": "paused"`.

---

## Cómo correr la suite

```bash
cd "Proyectos/AUT-03 — Telegram Audio Downloader"
~/.virtualenvs/telegram-audio-dl/bin/pytest tests/ -v
~/.virtualenvs/telegram-audio-dl/bin/pytest tests/test_scenarios.py -v   # solo escenarios E2E
```
