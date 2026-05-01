from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    phone: str
    session_name: str
    download_root: Path
    state_dir: Path
    project_root: Path


def load_config() -> Config:
    project_root = _find_project_root()
    load_dotenv(project_root / ".env")

    api_id_raw = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    phone = os.environ.get("TELEGRAM_PHONE", "").strip()

    missing = [
        name
        for name, value in (
            ("TELEGRAM_API_ID", api_id_raw),
            ("TELEGRAM_API_HASH", api_hash),
            ("TELEGRAM_PHONE", phone),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Faltan variables en .env: {', '.join(missing)}. "
            f"Copia .env.example a .env y completa los valores."
        )

    try:
        api_id = int(api_id_raw)
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_API_ID debe ser un entero.") from exc

    session_name = os.environ.get("TELEGRAM_SESSION", "telegram_audio_dl").strip()
    download_root = Path(
        os.environ.get("DOWNLOAD_ROOT") or (Path.home() / "Downloads")
    ).expanduser()

    return Config(
        api_id=api_id,
        api_hash=api_hash,
        phone=phone,
        session_name=session_name,
        download_root=download_root,
        state_dir=project_root / "state",
        project_root=project_root,
    )


def _find_project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()
