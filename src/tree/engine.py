"""Stateful conversation-tree engine.

Runs the markdown flows from ``tree.md``: starts flows on keyword matches or
the menu command, asks the questions (free-text fields or option lists), routes
on the customer's replies, and ends with a closed answer and/or a redirect
button to a human WhatsApp number.

Every phone number has at most one active session. If no flow matches, the
engine returns ``None`` and the caller falls back to the RAG answer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.agent.orchestrator import ReplyAction
from src.tree.parser import Flow, Step
from src.utils.pii import mask_phone

logger = logging.getLogger(__name__)


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


@dataclass
class TreeState:
    flow: Flow
    question_index: int  # index of the question step we are awaiting a reply to
    fields: dict[str, str] = field(default_factory=dict)


class TreeEngine:
    def __init__(self, flows: dict[str, Flow], config):
        self._flows = flows
        self._config = config
        self._sessions: dict[str, TreeState] = {}
        self._menu_flows = [f for f in flows.values()]

    # ------------------------------------------------------------------ API
    def handle(self, phone: str, text: str) -> ReplyAction | None:
        """Return a ReplyAction if the tree handled the message, else None."""
        state = self._sessions.get(phone)
        if state is not None:
            return self._continue(phone, state, text)

        command = text.strip().lower()
        if command in self._config.menu_keywords:
            return self._menu_action()

        flow = self._match_flow(text)
        if flow is None:
            return None
        return self._start(phone, flow)

    def reset_conversation(self, phone: str) -> None:
        self._sessions.pop(phone, None)

    def active(self, phone: str) -> bool:
        return phone in self._sessions

    # ---------------------------------------------- state (external storage)
    def export_session(self, phone: str) -> dict | None:
        """Serializable snapshot of the phone's session (None when idle)."""
        state = self._sessions.get(phone)
        if state is None:
            return None
        return {"flow": state.flow.id, "question_index": state.question_index, "fields": dict(state.fields)}

    def import_session(self, phone: str, data: dict | None) -> None:
        """Restore a snapshot produced by :meth:`export_session`."""
        self._sessions.pop(phone, None)
        if not data:
            return
        flow = self._flows.get(data.get("flow", ""))
        index = data.get("question_index", -1)
        if flow is None or not isinstance(index, int) or not 0 <= index < len(flow.steps):
            logger.warning("Discarding stale tree session for %s (flow changed?)", mask_phone(phone))
            return
        self._sessions[phone] = TreeState(flow=flow, question_index=index, fields=dict(data.get("fields") or {}))

    def forget(self, phone: str) -> None:
        """Drop the local copy after it was persisted to the state store."""
        self._sessions.pop(phone, None)

    # ------------------------------------------------------------ internals
    def _match_flow(self, text: str) -> Flow | None:
        lowered = text.strip().lower()
        if lowered.isdigit():  # "Reply with a number" from the menu
            index = int(lowered) - 1
            if 0 <= index < len(self._menu_flows):
                return self._menu_flows[index]
        for flow in self._menu_flows:
            if any(kw and kw in lowered for kw in flow.keywords):
                return flow
        return None

    def _menu_action(self) -> ReplyAction:
        lines = [getattr(self._config, "menu_header", "I can help you with one of these options:")]
        for index, flow in enumerate(self._menu_flows, 1):
            lines.append(f"{index}) {flow.display_name}")
        lines.append(getattr(self._config, "menu_footer", "Reply with a number or a keyword."))
        return ReplyAction(type="text", message="\n".join(lines))

    def _start(self, phone: str, flow: Flow) -> ReplyAction:
        logger.info("Tree action | phone=%s flow=%s started", mask_phone(phone), flow.id)
        state = TreeState(flow=flow, question_index=-1)
        action, awaiting = self._run(phone, state, 0)
        if awaiting is None:
            self._sessions.pop(phone, None)
        else:
            state.question_index = awaiting
            self._sessions[phone] = state
        return action

    def _continue(self, phone: str, state: TreeState, reply: str) -> ReplyAction:
        logger.info("Tree action | phone=%s flow=%s continued", mask_phone(phone), state.flow.id)
        flow = state.flow
        step = flow.steps[state.question_index]
        if step.save_as:
            state.fields[step.save_as] = reply.strip()

        target = self._route(step, reply)
        if target == "reask":
            return self._question_action(step, state.fields)

        if target == "next":
            next_index = state.question_index + 1
        else:
            resolved = flow.labels.get(target)
            if resolved is None:
                logger.warning("Flow '%s': unknown label '%s'", flow.id, target)
                next_index = state.question_index + 1
            else:
                next_index = resolved

        action, awaiting = self._run(phone, state, next_index)
        if awaiting is None:
            self._sessions.pop(phone, None)
        else:
            state.question_index = awaiting
            self._sessions[phone] = state
        return action

    def _route(self, step: Step, reply: str) -> str:
        """Return a label, 'next', or 'reask'."""
        lowered = reply.strip().lower()
        if step.options:
            for index, branch in enumerate(step.options, 1):
                pattern = branch.pattern.lower()
                if pattern == "*":
                    continue
                if lowered == str(index) or self._matches_option(lowered, pattern):
                    return branch.target
            for branch in step.options:
                if branch.pattern == "*":
                    return branch.target
            return "reask"
        if step.branches:
            default = None
            for branch in step.branches:
                if branch.pattern == "*":
                    default = branch.target
                elif branch.pattern and branch.pattern in lowered:
                    return branch.target
            if default is not None:
                return default
        return "next"

    @staticmethod
    def _matches_option(reply: str, pattern: str) -> bool:
        """Match a whole word/token, so 'a' does not match 'banana'."""
        if reply == pattern:
            return True
        return re.search(rf"(^|[\s.,;!?'-])({re.escape(pattern)})([\s.,;!?'-]|$)", reply) is not None

    def _run(self, phone: str, state: TreeState, start_index: int) -> tuple[ReplyAction, int | None]:
        """Execute steps from ``start_index`` until a question (await) or the end.

        Returns (action, awaiting_index). ``awaiting_index`` is None when the
        flow has finished.
        """
        flow = state.flow
        parts: list[str] = []
        index = start_index
        guard = 0
        while index < len(flow.steps):
            guard += 1
            if guard > self._config.max_steps:
                logger.error("Flow '%s' exceeded max_steps; aborting", flow.id)
                self._sessions.pop(phone, None)
                return (
                    ReplyAction(type="text", message="\n\n".join(parts) if parts else self._config.redirect_message),
                    None,
                )

            step = flow.steps[index]
            if step.kind == "label":
                index += 1
                continue
            if step.kind == "answer":
                parts.append(self._format(step.text, state.fields))
                index += 1
                continue
            if step.kind == "question":
                action = self._question_action(step, state.fields)
                if parts:
                    action = ReplyAction(type="text", message="\n\n".join(parts + [action.message]))
                return action, index
            if step.kind == "redirect":
                digits = step.number or ""
                if digits:
                    body = "\n\n".join(parts) if parts else step.text or self._config.redirect_message
                    action = ReplyAction(
                        type="redirect",
                        message=body,
                        url=f"https://wa.me/{digits}",
                        whatsapp=digits,
                    )
                else:
                    body = "\n\n".join(parts) if parts else self._config.redirect_message
                    action = ReplyAction(type="text", message=body)
                return action, None
            index += 1

        message = "\n\n".join(parts) if parts else self._config.redirect_message
        return ReplyAction(type="text", message=message), None

    def _question_action(self, step: Step, fields: dict[str, str]) -> ReplyAction:
        text = self._format(step.text, fields)
        if step.options:
            choices = [b for b in step.options if b.pattern != "*"]
            lines = "\n".join(f"{index + 1}) {b.pattern}" for index, b in enumerate(choices))
            footer = getattr(self._config, "options_footer", "Reply with a number or an option.")
            text = f"{text}\n\n{lines}\n\n{footer}"
        return ReplyAction(type="text", message=text)

    def _format(self, text: str, fields: dict[str, str]) -> str:
        try:
            return text.format_map(_SafeDict(fields))
        except (KeyError, IndexError, ValueError):
            return text
