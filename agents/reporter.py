"""Final human-readable report, written to disk and sent to Telegram."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agents.base import Agent
from config.settings import settings
from core.state import RunState

STATUS_ICON = {
    "verified": "[v]",
    "failed": "[x]",
    "skipped": "[-]",
    "pending": "[ ]",
    "running": "[~]",
    "planning": "[~]",
}


class Reporter(Agent):
    name = "reporter"

    async def run(self) -> tuple[str, Path]:
        state = self.state
        state.finished_at = datetime.now(timezone.utc).isoformat()
        markdown = self._render(state)

        settings.ensure_dirs()
        path = settings.reports_dir / f"{state.run_id}.md"
        path.write_text(markdown, encoding="utf-8")
        state.save()

        await self.emit("done", f"Report written to {path.name}")
        return markdown, path

    @staticmethod
    def _render(state: RunState) -> str:
        counts = state.counts()
        lines = [
            f"# Run report — {state.run_id}",
            "",
            f"- **Target:** {state.target_url}",
            f"- **Started:** {state.started_at}",
            f"- **Finished:** {state.finished_at}",
            f"- **Authenticated:** {'yes' if state.logged_in else 'no'}"
            + (f" (`{state.login_method}`)" if state.login_method else ""),
            f"- **Pages explored:** {len(state.pages)}",
            f"- **Browser actions used:** {state.actions_used}/{settings.max_actions}",
            f"- **Tasks:** {len(state.tasks)}  "
            + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())),
            "",
        ]

        if state.error:
            lines += ["> **Run error:** " + state.error, ""]

        lines += ["## Tasks", ""]
        if not state.tasks:
            lines.append("_No actionable tasks were found on this site._")
        for task in state.tasks:
            icon = STATUS_ICON.get(task.status, "[?]")
            lines.append(f"### {icon} {task.title}")
            lines += [
                f"- type: `{task.type}` · confidence: {task.confidence:.2f} · attempts: {task.attempts}",
                f"- url: {task.url}",
            ]
            if task.why:
                lines.append(f"- rationale: {task.why}")
            if task.evidence:
                lines.append(
                    f"- evidence ({task.evidence.get('source', '?')}): "
                    f"{task.evidence.get('evidence', '')[:200]}"
                )
            if task.error:
                lines.append(f"- error: {task.error}")
            lines.append("")

        if state.notes:
            lines += ["## Notes", ""] + [f"- {n}" for n in state.notes]

        return "\n".join(lines)

    @staticmethod
    def telegram_summary(state: RunState) -> str:
        counts = state.counts()
        verified = counts.get("verified", 0)
        head = [
            f"*Run {state.run_id} finished*",
            f"Target: `{state.target_url}`",
            f"Login: {'yes' if state.logged_in else 'no'} · Pages: {len(state.pages)} · "
            f"Actions: {state.actions_used}",
            f"Tasks: {verified}/{len(state.tasks)} verified",
            "",
        ]
        for task in state.tasks[:12]:
            head.append(f"{STATUS_ICON.get(task.status, '[?]')} {task.title}")
        if len(state.tasks) > 12:
            head.append(f"... and {len(state.tasks) - 12} more")
        return "\n".join(head)
