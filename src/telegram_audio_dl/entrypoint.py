"""Entry point con subcomandos. Reemplaza al `main()` simple de cli.py.

Subcomandos:
    (sin args)      modo interactivo (cli.interactive_main)
    daemon          corre el daemon headless en foreground (systemd Type=simple)
    daemon --detach hace fork+setsid (POSIX) y el padre devuelve 0
    status          imprime estado de jobs del daemon corriendo
    status --watch  refresca cada N segundos (Ctrl+C para salir)
    cancel <id>     cancela un job activo del daemon
    stop-daemon     SIGTERM al daemon, espera shutdown
    player          reproductor de biblioteca local (sin Telethon, sin daemon)

Diseño: los subcomandos one-shot abren conexión IPC al daemon, hacen su
trabajo y salen. Ninguno toca Telethon directamente, salvo el modo
interactivo cuando NO hay daemon.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from .config import load_config
from .ipc import IpcError, daemon_running_pid, send_command_sync, socket_path


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telegram-audio-dl",
        description=(
            "CLI de descarga + reproducción de audio de canales de Telegram. "
            "Sin subcomando: modo interactivo. Subcomandos: daemon, status, "
            "cancel, stop-daemon, player."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", metavar="<comando>")

    p_daemon = sub.add_parser(
        "daemon",
        help="Corre el daemon de descargas headless (para systemd / homelab).",
    )
    p_daemon.add_argument(
        "--detach",
        action="store_true",
        help="Hace fork y devuelve 0 inmediato (background). "
             "No usar con systemd Type=simple.",
    )

    p_status = sub.add_parser(
        "status",
        help="Imprime el estado de jobs del daemon corriendo.",
    )
    p_status.add_argument(
        "--watch",
        type=float,
        nargs="?",
        const=2.0,
        default=None,
        metavar="SEG",
        help="Refresca cada SEG segundos (default 2.0). Ctrl+C para salir.",
    )
    p_status.add_argument(
        "--json",
        action="store_true",
        help="Imprime JSON crudo (para scripts).",
    )

    p_cancel = sub.add_parser(
        "cancel",
        help="Cancela un job activo del daemon.",
    )
    p_cancel.add_argument("job_id", help="Identificador del job a cancelar.")

    sub.add_parser(
        "stop-daemon",
        help="Detiene el daemon limpiamente (jobs activos pasan a paused).",
    )

    sub.add_parser(
        "player",
        help="Reproductor de biblioteca local. No abre Telethon ni IPC.",
    )

    return parser


def _print_status_table(result: dict[str, Any]) -> None:
    """Render simple sin rich para que funcione en daemon-less / scripts."""
    jobs = result.get("jobs", [])
    if not jobs:
        print("Sin jobs registrados.")
        return
    # Encabezado
    print(
        f"{'JOB':<10} {'ESTADO':<10} {'CANAL':<30} "
        f"{'PROGRESO':<14} {'ARCHIVO ACTUAL'}"
    )
    print("─" * 90)
    for j in jobs:
        progress = f"{j['completed_count']}/{j['total_files']}"
        channel = (j.get("channel_name") or "")[:28]
        current = (j.get("current_file") or "")[:30]
        print(
            f"{j['job_id']:<10} {j['state']:<10} {channel:<30} "
            f"{progress:<14} {current}"
        )


def _cmd_status(args: argparse.Namespace) -> int:
    config = load_config()
    sock = socket_path(config.state_dir)

    def fetch_and_print() -> bool:
        """Devuelve False si el daemon no está disponible."""
        try:
            result = send_command_sync(sock, {"cmd": "status"})
        except IpcError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return False
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            _print_status_table(result)
        return True

    if args.watch is None:
        return 0 if fetch_and_print() else 1

    interval = max(0.5, args.watch)
    try:
        while True:
            # Limpiar pantalla con ANSI clear
            print("\033[2J\033[H", end="")
            print(f"[telegram-audio-dl status — refresh {interval:.1f}s — Ctrl+C para salir]\n")
            ok = fetch_and_print()
            if not ok:
                return 1
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def _cmd_cancel(args: argparse.Namespace) -> int:
    config = load_config()
    sock = socket_path(config.state_dir)
    try:
        result = send_command_sync(sock, {"cmd": "cancel", "job_id": args.job_id})
    except IpcError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Cancelado: {result.get('cancelled')}")
    return 0


def _cmd_stop_daemon(args: argparse.Namespace) -> int:
    config = load_config()
    sock = socket_path(config.state_dir)

    pid = daemon_running_pid(config.state_dir)
    if pid is None:
        print("No hay daemon corriendo.")
        return 0

    try:
        send_command_sync(sock, {"cmd": "stop"})
    except IpcError as exc:
        print(f"ERROR enviando stop: {exc}", file=sys.stderr)
        return 1

    # Esperar a que el daemon limpie su PID file
    print(f"Daemon (pid={pid}) está bajando. Esperando shutdown limpio…")
    deadline = time.time() + 30.0
    while time.time() < deadline:
        if daemon_running_pid(config.state_dir) is None:
            print("Daemon detenido.")
            return 0
        time.sleep(0.5)
    print("WARN: daemon no terminó en 30s. Verifica con `status` o logs.",
          file=sys.stderr)
    return 1


def _cmd_player(args: argparse.Namespace) -> int:
    """Atajo: abre el modo interactivo pero salta directo a Biblioteca local.
    No requiere conexión a Telegram ni daemon corriendo."""
    # Para no duplicar la lógica del menú "Biblioteca local", reusamos la
    # función `_library_flow` desde el modo interactivo. Pero `_library_flow`
    # solo necesita Config (state_dir), no Telethon.
    import asyncio

    from .cli import _library_flow, console
    from .logging_setup import setup_logging

    config = load_config()
    setup_logging(config.state_dir)
    try:
        asyncio.run(_library_flow(config))
        return 0
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrumpido.[/yellow]")
        return 130


def _cmd_daemon(args: argparse.Namespace) -> int:
    from .daemon import run_daemon
    return run_daemon(detach=args.detach)


def main(argv: list[str] | None = None) -> int:
    parser = _make_parser()
    args = parser.parse_args(argv)

    if args.cmd is None:
        from .cli import interactive_main
        return interactive_main()

    if args.cmd == "daemon":
        return _cmd_daemon(args)
    if args.cmd == "status":
        return _cmd_status(args)
    if args.cmd == "cancel":
        return _cmd_cancel(args)
    if args.cmd == "stop-daemon":
        return _cmd_stop_daemon(args)
    if args.cmd == "player":
        return _cmd_player(args)

    parser.error(f"Comando desconocido: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
