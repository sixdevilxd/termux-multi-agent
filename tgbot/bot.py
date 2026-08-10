"""Telegram front-end: your remote control for the agent swarm."""
from __future__ import annotations

import asyncio
import html
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agents.orchestrator import Orchestrator
from agents.reporter import Reporter
from config.settings import settings
from core.bus import Event, EventBus
from core.logger import get_logger
from chatbox.agent import ChatboxAgent
from tgbot.human_gate import HumanGate

log = get_logger("telegram")

HELP = """*Bright Scout* — chatbox + web runner

Ngobrol biasa aja (tanpa command):
• coding / fix bug
• market crypto, scan token baru, sentimen
• buat/import wallet (kunci tidak dikirim ke chat)
• cari berita di web
• tanya DLMM / LP

Web automation (masih ada):
/run `<url>` — jalankan agent di website
/status · /stop · /report
/reply `<token>` `<answer>` · /skip `<token>`

Tips:
• Kirim URL polos = langsung /run
• Saat gate OTP/login terbuka, pesan teks = jawaban gate
• Hanya user di `TELEGRAM_ALLOWED_USERS`"""


class AgentBot:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.gate = HumanGate()
        self.chatbox = ChatboxAgent()
        self.app: Application | None = None
        self.chat_id: int | None = None
        self.orchestrator: Orchestrator | None = None
        self.task: asyncio.Task | None = None
        self.last_report: str = ""

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _authorised(self, update: Update) -> bool:
        user = update.effective_user
        if not user:
            return False
        allowed = settings.telegram_allowed_users
        if not allowed:
            log.error("TELEGRAM_ALLOWED_USERS is empty — refusing every command.")
            return False
        return user.id in allowed

    async def _send(self, text: str) -> None:
        if not (self.app and self.chat_id):
            return
        try:
            await self.app.bot.send_message(
                chat_id=self.chat_id, text=text, parse_mode=ParseMode.MARKDOWN
            )
        except Exception:  # fall back to plain text if markdown is malformed
            try:
                await self.app.bot.send_message(chat_id=self.chat_id, text=html.unescape(text))
            except Exception as exc:
                log.error("Telegram send failed: %s", exc)

    async def _on_event(self, event: Event) -> None:
        if event.kind in {"step", "info"} and event.agent == "browser":
            return  # too chatty for a phone
        await self._send(event.pretty())

    # ── commands ─────────────────────────────────────────────────────────────
    async def cmd_start(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self.chat_id = update.effective_chat.id
        if not self._authorised(update):
            await update.message.reply_text(
                f"Not authorised. Your Telegram user id is {update.effective_user.id} — "
                "add it to TELEGRAM_ALLOWED_USERS."
            )
            return
        await update.message.reply_text(HELP, parse_mode=ParseMode.MARKDOWN)

    async def cmd_run(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorised(update):
            return
        self.chat_id = update.effective_chat.id

        if self.task and not self.task.done():
            await update.message.reply_text("A run is already in progress. /stop it first.")
            return
        if not ctx.args:
            await update.message.reply_text("Usage: /run https://example.com")
            return

        url = ctx.args[0]
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        problems = settings.validate()
        if problems:
            await update.message.reply_text("Config problems:\n- " + "\n- ".join(problems))
            return

        self.orchestrator = Orchestrator(url, self.bus, self.gate)
        await update.message.reply_text(
            f"Starting run `{self.orchestrator.state.run_id}` on `{url}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        self.task = asyncio.create_task(self._run_pipeline())

    async def _run_pipeline(self) -> None:
        assert self.orchestrator is not None
        try:
            state = await self.orchestrator.run()
            self.last_report = Reporter.telegram_summary(state)
            await self._send(self.last_report)
        except Exception as exc:  # noqa: BLE001
            log.exception("Pipeline crashed")
            await self._send(f"Run crashed: `{exc}`")

    async def cmd_status(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorised(update):
            return
        if not self.orchestrator:
            await update.message.reply_text("Idle. Send /run <url> to begin.")
            return
        state = self.orchestrator.state
        gate = self.gate.open_question
        lines = [
            f"Run `{state.run_id}` — phase *{state.phase}*",
            f"Pages: {len(state.pages)} · Tasks: {len(state.tasks)} · Actions: {state.actions_used}",
            "  ".join(f"{k}={v}" for k, v in sorted(state.counts().items())) or "no task results yet",
        ]
        if gate:
            lines.append(f"\nWaiting on you: `#{gate.token}` — {gate.prompt}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def cmd_reply(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorised(update):
            return
        if len(ctx.args) < 2:
            await update.message.reply_text("Usage: /reply <token> <answer>")
            return
        token, answer = ctx.args[0], " ".join(ctx.args[1:])
        ok = self.gate.answer(token, answer)
        await update.message.reply_text("Accepted." if ok else f"No open gate `#{token}`.")
        try:  # secrets should not linger in the chat history
            await update.message.delete()
        except Exception:
            pass

    async def cmd_skip(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorised(update):
            return
        if not ctx.args:
            await update.message.reply_text("Usage: /skip <token>")
            return
        ok = self.gate.skip(ctx.args[0])
        await update.message.reply_text("Skipped." if ok else "No such gate.")

    async def cmd_stop(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorised(update):
            return
        if self.orchestrator:
            self.orchestrator.cancel()
        if self.task and not self.task.done():
            self.task.cancel()
        await update.message.reply_text("Cancelling the current run.")

    async def cmd_report(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorised(update):
            return
        await update.message.reply_text(
            self.last_report or "No report yet.", parse_mode=ParseMode.MARKDOWN
        )

    async def on_text(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Human-gate reply, bare URL run, or natural chatbox conversation."""
        if not self._authorised(update):
            return
        self.chat_id = update.effective_chat.id
        text = (update.message.text or "").strip()
        if not text:
            return

        # 1) Open human gate always wins (OTP / password / done)
        if self.gate.open_question and self.gate.answer_latest(text):
            await update.message.reply_text("Accepted.")
            try:
                await update.message.delete()
            except Exception:
                pass
            return

        # 2) Bare URL (or "run/jalanin <url>") starts a website run
        run_url = self.chatbox.extract_run_url(text)
        if run_url:
            if self.task and not self.task.done():
                await update.message.reply_text("A run is already in progress. /stop it first.")
                return
            url = run_url if run_url.startswith(("http://", "https://")) else "https://" + run_url
            problems = settings.validate()
            if problems:
                await update.message.reply_text("Config problems:\n- " + "\n- ".join(problems))
                return
            self.orchestrator = Orchestrator(url, self.bus, self.gate)
            await update.message.reply_text(
                f"Starting run `{self.orchestrator.state.run_id}` on `{url}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            self.task = asyncio.create_task(self._run_pipeline())
            return

        # 3) Natural chatbox (coding, crypto, research, wallets, ...)
        if self.app is not None:
            await self.app.bot.send_chat_action(chat_id=self.chat_id, action="typing")
        try:
            answer = await self.chatbox.reply(self.chat_id, text)
        except Exception as exc:  # noqa: BLE001
            log.exception("chatbox failed")
            answer = f"Chat error: `{exc}`"
        # Telegram message limit ~4096
        if len(answer) > 4000:
            answer = answer[:3990] + "…"
        try:
            await update.message.reply_text(answer, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            await update.message.reply_text(answer)

    # ── entry point ──────────────────────────────────────────────────────────
    def build(self) -> Application:
        self.app = ApplicationBuilder().token(settings.telegram_token).build()
        self.app.add_handler(CommandHandler(["start", "help"], self.cmd_start))
        self.app.add_handler(CommandHandler("run", self.cmd_run))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("reply", self.cmd_reply))
        self.app.add_handler(CommandHandler("skip", self.cmd_skip))
        self.app.add_handler(CommandHandler("stop", self.cmd_stop))
        self.app.add_handler(CommandHandler("report", self.cmd_report))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))

        self.gate.set_notifier(self._send)
        self.bus.subscribe(self._on_event)
        return self.app

    def run_forever(self) -> None:
        app = self.build()
        log.info("Telegram bot polling. Send /start to your bot.")
        app.run_polling(drop_pending_updates=True)
