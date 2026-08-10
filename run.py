#!/usr/bin/env python3
"""Entry point.

    python run.py --bot                      # Telegram mode (recommended)
    python run.py --url https://example.com  # one-shot CLI run
    python run.py --check                    # validate configuration only
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys

from config.settings import settings
from core.bus import Event, EventBus
from core.logger import console, get_logger, setup_logging

log = get_logger("main")


def check_config() -> int:
    problems = settings.validate()
    console.print(f"provider   : {settings.llm_provider} / {settings.llm_model or '(cli default)'}")
    if settings.is_local_provider:
        console.print(f"claude bin : {shutil.which(settings.claude_bin) or 'NOT FOUND'}")
        console.print(f"timeout    : {settings.claude_timeout}s")
        console.print("credential : held by the claude CLI, not by this process")
    else:
        console.print(f"base url   : {settings.resolved_base_url}")
        console.print(f"api root   : {settings.api_root}  [dim](path is derived)[/dim]")
        console.print(f"api key    : {'set' if settings.llm_api_key else 'MISSING'}")
    console.print(f"browser    : {settings.browser_mode} ({settings.cdp_url})")
    if settings.chrome_path:
        console.print(f"chromium   : {settings.chrome_path}")
    if settings.browser_args:
        console.print(f"extra args : {' '.join(settings.browser_args)}")
    console.print(f"dry run    : {settings.dry_run}")
    console.print(f"max actions: {settings.max_actions}")
    console.print(f"storage    : {settings.storage_dir}")
    console.print(f"telegram   : {'configured' if settings.telegram_token else 'MISSING'}")
    if problems:
        console.print("\n[bold red]Problems:[/bold red]")
        for p in problems:
            console.print(f"  - {p}")
        return 1
    for note in settings.warnings():
        console.print(f"\n[yellow]Warning:[/yellow] {note}")
    console.print("\n[bold green]Configuration looks good.[/bold green]")
    console.print("[dim]Tip: `python run.py --models` lists the exact model ids "
                  "your gateway accepts.[/dim]")
    return 0


async def list_models(filter_text: str = "") -> int:
    """Print every model id the configured gateway exposes."""
    from core.llm import LLMClient

    client = LLMClient()
    try:
        models = await client.list_models()
    except Exception as exc:
        console.print(f"[red]Could not list models:[/red] {exc}")
        return 1
    finally:
        await client.aclose()

    if filter_text:
        models = [m for m in models if filter_text.lower() in m.lower()]

    console.print(f"[bold]{len(models)} model(s)[/bold] on {settings.api_root}\n")
    for model in models:
        marker = " [green]<- LLM_MODEL[/green]" if model == settings.llm_model else ""
        console.print(f"  {model}{marker}")

    if settings.llm_model not in models and not filter_text:
        console.print(
            f"\n[yellow]Warning:[/yellow] LLM_MODEL={settings.llm_model!r} "
            "is not in this list. Copy an exact id from above into .env."
        )
    return 0


async def ping() -> int:
    """Prove the key, base URL and model all work together."""
    from core.llm import LLMClient

    client = LLMClient()
    console.print(f"Calling {settings.llm_model} at {settings.api_root} ...")
    try:
        reply = await client.ping()
    except Exception as exc:
        console.print(f"[red]Failed:[/red] {exc}")
        return 1
    finally:
        await client.aclose()
    console.print(f"[green]OK[/green] — model replied: {reply.strip()[:120]!r}")
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
    parser.add_argument(
        "--models", nargs="?", const="", metavar="FILTER",
        help="list model ids the gateway accepts (optionally filtered, e.g. --models claude)",
    )
    parser.add_argument("--ping", action="store_true", help="send one test completion and exit")
    args = parser.parse_args()

    setup_logging()
    settings.ensure_dirs()

    if args.check:
        return check_config()

    if args.models is not None:
        return asyncio.run(list_models(args.models))

    if args.ping:
        return asyncio.run(ping())

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
