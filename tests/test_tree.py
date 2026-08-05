"""Unit tests for the conversation-tree parser and engine."""

import pytest

from src.tree.engine import TreeEngine
from src.tree.parser import load_tree, parse_tree


SAMPLE = """\
# Comment line

## returns

Menu: Returns & refunds
Keywords: return, refund, devolucion
Description: Guide a return.

- question: What is your order number? -> field=order
- branch: * -> @found
- answer @found: Thanks! We found order {order}.
- answer: Ship it back within 30 days.
- message: Need more help?
- redirect: 1555 123 4567

## payment

Menu: Payment methods
Keywords: pay
Description: Payment.

- question: Which method? -> field=method
- option: card -> @card
- option: paypal -> @paypal
- option: * -> @other
- answer @card: We accept all cards.
- answer @paypal: We accept PayPal.
- answer @other: Bank transfer too.

## orderstatus

Menu: Order status
Keywords: order, status
Description: Check an order.

- question: Order number? -> field=order
- branch: * -> @status_result
- answer @status_result: Order {order} is being processed.

## contact

Menu: Talk to a human
Keywords: support
Description: Reach a human.

- answer: Talk to an agent directly.
- redirect: 15551234567
"""


@pytest.fixture()
def flows(tmp_path):
    tree_file = tmp_path / "tree.md"
    tree_file.write_text(SAMPLE, encoding="utf-8")
    return load_tree(tree_file)


class Config:
    menu_keywords = ["menu", "start", "help"]
    redirect_message = "Continue with a human agent here:"
    max_steps = 30


class TestParser:
    def test_flow_count_and_ids(self, flows):
        assert [f.id for f in flows] == ["returns", "payment", "orderstatus", "contact"]

    def test_meta_parsed(self, flows):
        returns = flows[0]
        assert returns.menu_label == "Returns & refunds"
        assert returns.keywords == ["return", "refund", "devolucion"]
        assert returns.description == "Guide a return."

    def test_question_and_field(self, flows):
        question = flows[0].steps[0]
        assert question.kind == "question"
        assert question.save_as == "order"
        assert question.text == "What is your order number?"

    def test_option_routes_attached_to_question(self, flows):
        question = flows[1].steps[0]
        assert question.kind == "question"
        assert [(b.pattern, b.target) for b in question.options] == [
            ("card", "card"),
            ("paypal", "paypal"),
            ("*", "other"),
        ]

    def test_redirect_number_cleaned(self, flows):
        steps = [s for s in flows[0].steps if s.kind == "redirect"]
        assert len(steps) == 1
        assert steps[0].number == "15551234567"

    def test_message_then_redirect(self, flows):
        steps = flows[0].steps
        redirect = [s for s in steps if s.kind == "redirect"][0]
        assert redirect.text == "Need more help?"

    def test_labels_mapped(self, flows):
        assert flows[0].labels["found"] == 1
        assert flows[2].labels["status_result"] == 1

    def test_invalid_line_ignored(self):
        text = "## f\n- question: hi? -> field=x\n- bogus line\n- answer: ok\n"
        flows = parse_tree(text)
        assert len(flows) == 1
        assert [s.kind for s in flows[0].steps] == ["question", "answer"]

    def test_plain_comment_lines_ignored(self):
        text = "# hello\n## f\n- answer: hi\n"
        flows = parse_tree(text)
        assert len(flows) == 1
        assert flows[0].steps[0].text == "hi"


class TestEngine:
    def _engine(self, flows):
        return TreeEngine({f.id: f for f in flows}, Config())

    def test_keyword_starts_flow(self, flows):
        engine = self._engine(flows)
        action = engine.handle("1001", "I want a refund")
        assert action.type == "text"
        assert "order number" in action.message
        assert engine.active("1001")

    def test_question_awaits_reply(self, flows):
        engine = self._engine(flows)
        action = engine.handle("1001", "return please")
        assert action.type == "text"
        assert engine.active("1001")
        assert not engine.active("9999")

    def test_flow_without_question_ends_immediately(self, flows):
        engine = self._engine(flows)
        action = engine.handle("1002", "support")
        assert action.type == "redirect"
        assert action.whatsapp == "15551234567"
        assert not engine.active("1002")

    def test_full_flow_with_branch_and_placeholder(self, flows):
        engine = self._engine(flows)
        engine.handle("1001", "order please")
        action = engine.handle("1001", "A-42")
        assert "Order A-42 is being processed" in action.message
        assert action.type == "text"

    def test_option_by_number_and_text(self, flows):
        engine = self._engine(flows)
        engine.handle("1001", "pay")
        action = engine.handle("1001", "2")
        assert "PayPal" in action.message
        assert not engine.active("1001")

        engine = self._engine(flows)
        engine.handle("1002", "pay")
        action = engine.handle("1002", "card")
        assert "cards" in action.message

    def test_option_wildcard_default(self, flows):
        engine = self._engine(flows)
        engine.handle("1001", "pay")
        action = engine.handle("1001", "whatever")
        assert "Bank transfer too" in action.message

    def test_option_unknown_answer_reasks(self, flows):
        text = (
            "## reaskflow\nKeywords: reask\n"
            "- question: Pick one -> field=pick\n"
            "- option: a -> @a\n- option: b -> @b\n"
            "- answer @a: You chose A.\n- answer @b: You chose B.\n"
        )
        flows = parse_tree(text)
        engine = self._engine(flows)
        engine.handle("1001", "reask")
        action = engine.handle("1001", "banana")
        assert "Pick one" in action.message
        assert engine.active("1001")

    def test_branch_without_match_continues_forward(self, flows):
        engine = self._engine(flows)
        engine.handle("1001", "order please")
        action = engine.handle("1001", "42")
        assert "Order 42 is being processed" in action.message

    def test_menu_command_lists_flows(self, flows):
        engine = self._engine(flows)
        action = engine.handle("1001", "menu")
        assert "Returns & refunds" in action.message
        assert "Payment methods" in action.message
        assert not engine.active("1001")

    def test_menu_reply_by_number_starts_flow(self, flows):
        engine = self._engine(flows)
        engine.handle("1001", "menu")
        action = engine.handle("1001", "3")
        assert "Order number?" in action.message
        assert engine.active("1001")

    def test_no_keyword_no_menu_falls_through(self, flows):
        engine = self._engine(flows)
        assert engine.handle("1001", "hello there") is None
        assert not engine.active("1001")

    def test_no_number_ignored_by_menu(self, flows):
        engine = self._engine(flows)
        engine.handle("1001", "menu")
        action = engine.handle("1001", "abc")
        assert action is None
        assert not engine.active("1001")

    def test_reset_conversation_clears_session(self, flows):
        engine = self._engine(flows)
        engine.handle("1001", "return please")
        assert engine.active("1001")
        engine.reset_conversation("1001")
        assert not engine.active("1001")
