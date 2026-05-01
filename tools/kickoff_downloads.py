"""Encola y ejecuta la descarga completa de un set fijo de canales.

Útil para correr en background (nohup / systemd / launchd) mientras el CLI
interactivo se usa en otra máquina. Reusa el state SQLite del proyecto.

Edita la lista `CHANNELS` con tus channel_ids y nombres antes de ejecutar.
Para obtener un channel_id desde el CLI interactivo, busca el canal y revisa
los logs (`logger.info` imprime el id en `_select_channel`).

Si quieres mantener tu lista personal fuera de git, copia este archivo a
`tools/kickoff_downloads.local.py` (gitignored) y edita ahí.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path

from telegram_audio_dl.client import TelegramAudioClient
from telegram_audio_dl.config import load_config
from telegram_audio_dl.download_manager import DownloadManager
from telegram_audio_dl.logging_setup import get_logger, setup_logging


# Lista de canales a descargar. Formato: (channel_id, channel_name).
# channel_id es el entero negativo que Telegram asigna a chats supergroup/channel.
CHANNELS: list[tuple[int, str]] = [
    # (-1001234567890, "My Music Channel"),
]


def safe_dirname(name: str) -> str:
    safe = re.sub(r"[\\/:*?\"<>|]", "_", name).strip()
    return safe or "telegram_channel"


async def run() -> int:
    cfg = load_config()
    log_file = setup_logging(cfg.state_dir)
    logger = get_logger(__name__)
    logger.info("=" * 60)
    logger.info("kickoff_downloads start | log=%s", log_file)
    logger.info("download_root=%s state_dir=%s", cfg.download_root, cfg.state_dir)

    async with TelegramAudioClient(cfg) as wrapper:
        if not await wrapper.raw.is_user_authorized():
            logger.error("Sesión Telegram no autorizada en este host")
            return 2

        manager = DownloadManager(wrapper.raw, cfg.state_dir)
        manager.start()

        for channel_id, channel_name in CHANNELS:
            try:
                audios = await wrapper.list_audios(channel_id)
            except Exception as exc:
                logger.exception("list_audios falló para %s: %s", channel_name, exc)
                continue
            destination = cfg.download_root / safe_dirname(channel_name)
            destination.mkdir(parents=True, exist_ok=True)
            job = manager.enqueue(channel_id, channel_name, audios, destination)
            logger.info(
                "enqueued: %s | files=%d total_bytes=%d job_id=%s dest=%s",
                channel_name, len(audios), job.total_bytes, job.job_id, destination,
            )

        last_report = 0.0
        while manager.has_active_jobs():
            await asyncio.sleep(10)
            now = time.monotonic()
            if now - last_report >= 30:
                last_report = now
                for j in manager.jobs:
                    if j.state in ("queued", "running"):
                        pct = (j.bytes_done_total / j.total_bytes * 100) if j.total_bytes else 0
                        logger.info(
                            "progress | %s %s | %d/%d files | %.1f%% (%d/%d B)",
                            j.job_id, j.channel_name,
                            j.completed_count, j.total_files,
                            pct, j.bytes_done_total, j.total_bytes,
                        )

        await manager.stop()
        logger.info("kickoff_downloads done")
        for j in manager.jobs:
            logger.info(
                "final | %s %s state=%s ok=%d skipped=%d failed=%d",
                j.job_id, j.channel_name, j.state,
                j.completed_count, j.skipped_count, j.failed_count,
            )
    return 0


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
