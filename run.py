#!/usr/bin/env python3
"""Entry point.

    python run.py --bot                      # Telegram mode (recommended)
    python run.py --url https://example.com  # one-shot CLI run
    python run.py --check                    # validate configuration only
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from config.settings import settings
from core.bus import Event, EventBus
from core.logger import console, get_logger, setup_logging

log = get_logger("main")


def check_config() -> int:
    problems = settings.validate()
    console.print(f"provider   : {settings.llm_provider} / {settings.llm_model}")
    console.print(f"browser    : {settings.browser_mode} ({settings.cdp_url})")
    console.print(f"dry run    : {settings.dry_run}")
    console.print(f"max actions: {settings.max_actions}")
    console.print(f"storage    : {settings.storage_dir}")
    console.print(f"telegram   : {'configured' if settings.telegram_token else 'MISSING'}")
    if problems:
        console.print("\n[bold red]Problems:[/bold red]")
        for p in problems:
            console.print(f"  - {p}")
        return 1
    console.print("\n[bold green]Configuration looks good.[/bold green]")
    return 0


async def cli_run(url: str, max_tasks: int) -> int:
    from agents.orchestrator import Orchestrator
    from agents.reporter import Reporter
    from tgbot.human_gate import HumanGate

    bus = EventBus()

    async def printer(event: Event) -> None:
        console.print(event.pretty())

    bus.subscribe(printer)

    gate = HumanGate(timeout=settings.human_gate_timeout)

    async def console_notifier(text: str) -> None:
        console.print(f"[bold yellow]{text}[/bold yellow]")
        console.print("[dim]CLI mode cannot read replies — use --bot for interactive gates.[/dim]")

    gate.set_notifier(console_notifier)

    orchestrator = Orchestrator(url, bus, gate, max_tasks=max_tasks)
    state = await orchestrator.run()
    console.print("\n" + Reporter.telegram_summary(state))
    console.print(f"\nFull report: {settings.reports_dir / (state.run_id + '.md')}")
    return 1 if state.error else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-agent web task runner")
    parser.add_argument("--bot", action="store_true", help="run the Telegram bot")
    parser.add_argument("--url", help="run once against this URL and exit")
    parser.add_argument("--max-tasks", type=int, default=10, help="cap tasks per run")
    parser.add_argument("--check", action="store_true", help="validate config and exit")
    args = parser.parse_args()

    setup_logging()
    settings.ensure_dirs()

    if args.check:
        return check_config()

    if args.bot:
        if not settings.telegram_token:
            console.print("[red]TELEGRAM_BOT_TOKEN is not set.[/red]")
            return 1
        if not settings.telegram_allowed_users:
            console.print("[red]TELEGRAM_ALLOWED_USERS is empty — refusing to start.[/red]")
            return 1
        from tgbot.bot import AgentBot

        AgentBot().run_forever()
        return 0

    if args.url:
        problems = settings.validate()
        if problems:
            console.print("[red]Config problems:[/red] " + "; ".join(problems))
            return 1
        return asyncio.run(cli_run(args.url, args.max_tasks))

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
