"""Parser for the markdown conversation tree.

Turns ``tree.md`` into a list of :class:`Flow` objects. The format is meant
to be edited by non-developers; see ``docs/CONVERSATION_TREE.md``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_FLOW_HEADER = re.compile(r"^##\s+(\S.*)$")
_META = re.compile(r"^(Menu|Keywords|Description):\s*(.*)$", re.IGNORECASE)
_BULLET = re.compile(r"^-\s+(.*)$")
_LABEL = re.compile(r"^@(\S+)$")
_QUESTION = re.compile(r"^question:\s*(.*)$", re.IGNORECASE)
_OPTION = re.compile(r"^option:\s*(.+?)\s*->\s*@(\S+)$", re.IGNORECASE)
_BRANCH = re.compile(r"^branch:\s*(.+?)\s*->\s*@(\S+)$", re.IGNORECASE)
_ANSWER = re.compile(r"^answer\s*:?\s*(.*)$", re.IGNORECASE)
_ANSWER_LABEL = re.compile(r"^@(\S+):\s*(.*)$")
_MESSAGE = re.compile(r"^message:\s*(.*)$", re.IGNORECASE)
_REDIRECT = re.compile(r"^redirect:\s*(.+?)\s*$", re.IGNORECASE)
_FIELD = re.compile(r"\s*->\s*field=(\w+)\s*$")


@dataclass
class Branch:
    pattern: str  # keyword (lowercased); "*" = default route
    target: str  # label


@dataclass
class Step:
    kind: str  # "label" | "question" | "answer" | "redirect"
    text: str = ""
    save_as: str | None = None  # question: save the reply into this field
    options: list[Branch] = field(default_factory=list)  # question: choices
    branches: list[Branch] = field(default_factory=list)  # question: keyword routes
    number: str | None = None  # redirect: WhatsApp number (digits only)


@dataclass
class Flow:
    id: str
    menu_label: str | None = None
    description: str | None = None
    keywords: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    labels: dict[str, int] = field(default_factory=dict)  # label -> step index

    @property
    def display_name(self) -> str:
        return self.menu_label or self.id


def parse_tree(text: str) -> list[Flow]:
    flows: list[Flow] = []
    current: Flow | None = None
    pending_message: str | None = None

    def append_step(step: Step) -> None:
        nonlocal pending_message
        if current is None:
            return
        if pending_message is not None:
            if step.kind == "redirect":
                step.text = pending_message  # button body comes from - message:
            else:
                current.steps.append(Step(kind="answer", text=pending_message))
            pending_message = None
        current.steps.append(step)

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") and not line.startswith("##"):
            continue  # comment line

        header = _FLOW_HEADER.match(line)
        if header:
            current = Flow(id=header.group(1).strip())
            flows.append(current)
            pending_message = None
            continue
        if current is None:
            continue

        meta = _META.match(line)
        if meta:
            key = meta.group(1).lower()
            value = meta.group(2).strip()
            if key == "menu":
                current.menu_label = value
            elif key == "keywords":
                current.keywords = [
                    k.strip().lower() for k in value.split(",") if k.strip()
                ]
            elif key == "description":
                current.description = value
            continue

        bullet = _BULLET.match(line)
        if not bullet:
            continue
        content = bullet.group(1).strip()

        label = _LABEL.match(content)
        if label:
            append_step(Step(kind="label", text=label.group(1)))
            continue

        question = _QUESTION.match(content)
        if question:
            qtext = question.group(1).strip()
            field_match = _FIELD.search(qtext)
            field_name = None
            if field_match:
                field_name = field_match.group(1)
                qtext = qtext[: field_match.start()].rstrip()
            append_step(Step(kind="question", text=qtext, save_as=field_name))
            continue

        option = _OPTION.match(content)
        if option:
            if current.steps and current.steps[-1].kind == "question":
                current.steps[-1].options.append(
                    Branch(pattern=option.group(1).strip(), target=option.group(2))
                )
            else:
                logger.warning("'option:' without a preceding question: %s", line)
            continue

        branch = _BRANCH.match(content)
        if branch:
            if current.steps and current.steps[-1].kind == "question":
                current.steps[-1].branches.append(
                    Branch(pattern=branch.group(1).strip().lower(), target=branch.group(2))
                )
            else:
                logger.warning("'branch:' without a preceding question: %s", line)
            continue

        answer = _ANSWER.match(content)
        if answer:
            atext = answer.group(1).strip()
            label_match = _ANSWER_LABEL.match(atext)
            if label_match:
                append_step(Step(kind="label", text=label_match.group(1)))
                atext = label_match.group(2).strip()
            append_step(Step(kind="answer", text=atext))
            continue

        message = _MESSAGE.match(content)
        if message:
            pending_message = message.group(1).strip()
            continue

        redirect = _REDIRECT.match(content)
        if redirect:
            number = "".join(ch for ch in redirect.group(1) if ch.isdigit())
            append_step(Step(kind="redirect", number=number or None))
            continue

        logger.warning("Unrecognized tree line: %s", line)

    if pending_message is not None and current is not None:
        current.steps.append(Step(kind="answer", text=pending_message))

    for flow in flows:
        for index, step in enumerate(flow.steps):
            if step.kind == "label":
                flow.labels.setdefault(step.text, index)

    return flows


def load_tree(path) -> list[Flow]:
    """Read and parse a tree file. Returns [] if the file is missing."""
    text = path.read_text(encoding="utf-8")
    return parse_tree(text)
