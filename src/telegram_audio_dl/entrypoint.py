"""Entry point with subcommands. Replaces the simple `main()` from cli.py.

Subcommands:
    (no args)       interactive mode (cli.interactive_main)
    daemon          runs the headless daemon in foreground (systemd Type=simple)
    daemon --detach forks + setsid (POSIX); the parent returns 0
    status          prints state of jobs of the running daemon
    status --watch  refreshes every N seconds (Ctrl+C to exit)
    cancel <id>     cancels an active job of the daemon
    stop-daemon     SIGTERM to the daemon, waits for shutdown
    player          local-library player (no Telethon, no daemon)

Design: one-shot subcommands open an IPC connection to the daemon, do
their work and exit. None of them touch Telethon directly, except the
interactive mode when there is NO daemon running.
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
            "CLI for downloading and playing audio from Telegram channels. "
            "No subcommand: interactive mode. Subcommands: daemon, status, "
            "cancel, stop-daemon, player."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", metavar="<command>")

    p_daemon = sub.add_parser(
        "daemon",
        help="Run the download daemon headless (for systemd / homelab).",
    )
    p_daemon.add_argument(
        "--detach",
        action="store_true",
        help="Hace fork y devuelve 0 inmediato (background). "
             "No usar con systemd Type=simple.",
    )

    p_status = sub.add_parser(
        "status",
        help="Print state of jobs of the running daemon.",
    )
    p_status.add_argument(
        "--watch",
        type=float,
        nargs="?",
        const=2.0,
        default=None,
        metavar="SEC",
        help="Refresh every SEC seconds (default 2.0). Ctrl+C to exit.",
    )
    p_status.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON (for scripts).",
    )

    p_cancel = sub.add_parser(
        "cancel",
        help="Cancel an active job of the daemon.",
    )
    p_cancel.add_argument("job_id", help="Job identifier to cancel.")

    sub.add_parser(
        "stop-daemon",
        help="Stop the daemon cleanly (active jobs become paused).",
    )

    sub.add_parser(
        "player",
        help="Local library player. Does not open Telethon or IPC.",
    )

    return parser


def _print_status_table(result: dict[str, Any]) -> None:
    """Render simple sin rich para que funcione en daemon-less / scripts."""
    jobs = result.get("jobs", [])
    if not jobs:
        print("No jobs registered.")
        return
    # Encabezado
    print(
        f"{'JOB':<10} {'STATE':<10} {'CHANNEL':<30} "
        f"{'PROGRESS':<14} {'CURRENT FILE'}"
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
            print(f"[telegram-audio-dl status — refresh {interval:.1f}s — Ctrl+C to exit]\n")
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
    print(f"Cancelled: {result.get('cancelled')}")
    return 0


def _cmd_stop_daemon(args: argparse.Namespace) -> int:
    config = load_config()
    sock = socket_path(config.state_dir)

    pid = daemon_running_pid(config.state_dir)
    if pid is None:
        print("No daemon running.")
        return 0

    try:
        send_command_sync(sock, {"cmd": "stop"})
    except IpcError as exc:
        print(f"ERROR sending stop: {exc}", file=sys.stderr)
        return 1

    # Esperar a que el daemon limpie su PID file
    print(f"Daemon (pid={pid}) shutting down. Waiting for clean shutdown…")
    deadline = time.time() + 30.0
    while time.time() < deadline:
        if daemon_running_pid(config.state_dir) is None:
            print("Daemon stopped.")
            return 0
        time.sleep(0.5)
    print("WARN: daemon did not terminate in 30s. Check with `status` or logs.",
          file=sys.stderr)
    return 1


def _cmd_player(args: argparse.Namespace) -> int:
    """Atajo: abre el modo interactivo pero salta directo a Local library.
    No requiere conexión a Telegram ni daemon running."""
    # Para no duplicar la lógica del menú "Local library", reusamos la
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
        console.print("\n[yellow]Interrupted.[/yellow]")
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

    parser.error(f"Unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
