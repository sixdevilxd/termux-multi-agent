"""Final human-readable report, written to disk and sent to Telegram."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agents.base import Agent
from config.settings import settings
from core.rewards import describe_delta
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

        understanding = state.understanding
        if understanding.get("site_purpose"):
            lines += [
                "## What this site is",
                "",
                understanding["site_purpose"],
                "",
            ]
            if understanding.get("site_type"):
                lines.append(f"- category: `{understanding['site_type']}`")
            if state.vocabulary:
                lines.append(
                    "- reward vocabulary: "
                    + ", ".join(f"`{w}`" for w in state.vocabulary)
                )
            for system in understanding.get("reward_systems", []):
                lines.append(
                    f"- reward system: **{system.get('name', '?')}** "
                    f"— {system.get('where', '')} {system.get('evidence', '')}".rstrip()
                )
            lines.append("")

        delta = state.reward_delta()
        if state.reward_baseline or delta:
            lines += ["## Rewards", ""]
            if state.reward_baseline:
                lines.append(
                    "- before: "
                    + ", ".join(f"{k}={v:g}" for k, v in sorted(state.reward_baseline.items()))
                )
            if state.reward_final:
                lines.append(
                    "- after: "
                    + ", ".join(f"{k}={v:g}" for k, v in sorted(state.reward_final.items()))
                )
            lines.append(f"- change: **{describe_delta(delta) or 'none detected'}**")
            lines.append("")

        lines += ["## Tasks", ""]
        if not state.tasks:
            lines.append("_No actionable tasks were found on this site._")
        for task in state.tasks:
            icon = STATUS_ICON.get(task.status, "[?]")
            lines.append(f"### {icon} {task.title}")
            meta = [
                f"type: `{task.type}`",
                f"priority: P{task.priority}",
                f"confidence: {task.confidence:.2f}",
                f"attempts: {task.attempts}",
            ]
            if task.effort:
                meta.insert(2, f"effort: {task.effort}")
            lines += [
                "- " + " · ".join(meta),
                f"- url: {task.url}",
            ]
            if task.reward:
                lines.append(f"- reward: {task.reward}")
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
        ]
        if not state.logged_in and settings.require_login:
            head += [
                "",
                "*STOPPED — not authenticated.*",
                "Rewards only accrue on a logged-in account, so nothing was attempted.",
            ]
            for note in state.notes[:3]:
                head.append(f"_{note}_")
            return "\n".join(head)

        head += [
            f"Login: {'yes' if state.logged_in else 'no'} · Pages: {len(state.pages)} · "
            f"Actions: {state.actions_used}",
            f"Tasks: {verified}/{len(state.tasks)} verified",
        ]
        if state.understanding.get("site_type"):
            head.insert(2, f"Site: {state.understanding['site_type']}")
        delta = describe_delta(state.reward_delta())
        if delta:
            head.append(f"Rewards: {delta}")
        head.append("")
        for task in state.tasks[:12]:
            head.append(f"{STATUS_ICON.get(task.status, '[?]')} P{task.priority} {task.title}")
        if len(state.tasks) > 12:
            head.append(f"... and {len(state.tasks) - 12} more")
        return "\n".join(head)
