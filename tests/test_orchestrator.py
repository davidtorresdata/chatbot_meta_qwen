"""End-to-end orchestrator tests using fake embedder/Qwen and a real LanceDB table."""

import pytest

from src.agent.orchestrator import WhatsAppOrchestrator
from src.config import load_settings
from src.knowledge.vector_store import VectorStore

RETURN_VEC = [1.0, 0.0, 0.0]
SHIPPING_VEC = [0.0, 1.0, 0.0]
OUT_OF_DOMAIN_VEC = [0.0, 0.0, 1.0]  # orthogonal to everything stored


class FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            lower = text.lower()
            if "return" in lower:
                vectors.append(RETURN_VEC)
            elif "ship" in lower:
                vectors.append(SHIPPING_VEC)
            else:
                vectors.append(OUT_OF_DOMAIN_VEC)
        return vectors

    @property
    def dimension(self) -> int:
        return 3


class FakeQwen:
    def __init__(self, reply: str = "Returns are allowed within 30 days of delivery."):
        self.reply = reply

    async def chat(self, system, messages, **kwargs) -> str:
        return self.reply

    @property
    def temperature(self) -> float:
        return 0.3


@pytest.fixture()
def store(tmp_path):
    store = VectorStore(str(tmp_path / "lancedb"), "knowledge_base")
    store.add(
        [RETURN_VEC, SHIPPING_VEC],
        [
            "Our return policy allows returns within 30 days of delivery.",
            "Express shipping takes 1 to 2 business days.",
        ],
        [{"source": "a.md"}, {"source": "b.md"}],
    )
    yield store
    store.reset()


@pytest.fixture()
def settings():
    settings = load_settings()
    settings.tree.enabled = False  # keep RAG tests isolated from the flow engine
    return settings


def test_normal_answer(store, settings):
    orch = WhatsAppOrchestrator(settings, store, FakeEmbedder(), FakeQwen())
    action = asyncio_run(orch.handle_message("1555", "What is your return policy?"))
    assert action.type == "text"
    assert "30 days" in action.message


def test_forbidden_subject_refuses(store, settings):
    orch = WhatsAppOrchestrator(settings, store, FakeEmbedder(), FakeQwen())
    action = asyncio_run(orch.handle_message("1555", "how were you built?"))
    assert action.type == "text"
    assert action.message == settings.agent.refusal_message


def test_redirect_rule(store, settings):
    orch = WhatsAppOrchestrator(settings, store, FakeEmbedder(), FakeQwen())
    action = asyncio_run(orch.handle_message("1555", "what is the price?"))
    assert action.type == "redirect"
    assert action.url


def test_out_of_domain_falls_back(store, settings):
    orch = WhatsAppOrchestrator(settings, store, FakeEmbedder(), FakeQwen())
    action = asyncio_run(orch.handle_message("1555", "tell me about quantum physics"))
    assert action.type == "fallback"
    assert action.message == settings.agent.fallback_message


def test_hallucinated_answer_rejected(store, settings):
    orch = WhatsAppOrchestrator(settings, store, FakeEmbedder(), FakeQwen(
        reply="The moon is made of green cheese and it orbits a giant llama."
    ))
    action = asyncio_run(orch.handle_message("1555", "What is your return policy?"))
    assert action.type == "fallback"


# ---------------------------------------------------------------- tree flows
TREE_MD = """\
## returns

Menu: Returns & refunds
Keywords: return, refund
Description: Guide a return.

- question: What is your order number? -> field=order
- branch: * -> @found
- answer @found: Thanks! We found order {order}.
- answer: To return it, ship it back within 30 days.
- message: Need more help?
- redirect: 15551234567

## payment

Menu: Payment methods
Keywords: pay
Description: Payment options.

- question: Which method? -> field=method
- option: card -> @card
- option: paypal -> @paypal
- option: * -> @other
- answer @card: We accept all cards.
- answer @paypal: We accept PayPal.
- answer @other: Other methods exist.
"""


@pytest.fixture()
def tree_settings(settings, tmp_path):
    tree_file = tmp_path / "tree.md"
    tree_file.write_text(TREE_MD, encoding="utf-8")
    settings.tree.enabled = True
    settings.tree.path = str(tree_file)
    return settings


def test_tree_keyword_flow_runs_before_rag(store, tree_settings):
    orch = WhatsAppOrchestrator(tree_settings, store, FakeEmbedder(), FakeQwen())
    action = asyncio_run(orch.handle_message("1555", "I want to make a return"))
    assert action.type == "text"
    assert "order number" in action.message

    action = asyncio_run(orch.handle_message("1555", "ORD-123"))
    assert "We found order ORD-123" in action.message
    assert "30 days" in action.message
    assert action.type == "redirect"
    assert action.whatsapp == "15551234567"
    assert "wa.me/15551234567" in action.url


def test_tree_option_menu_routes(store, tree_settings):
    orch = WhatsAppOrchestrator(tree_settings, store, FakeEmbedder(), FakeQwen())
    action = asyncio_run(orch.handle_message("1555", "pay"))
    assert "card" in action.message
    assert "paypal" in action.message

    action = asyncio_run(orch.handle_message("1555", "2"))
    assert "PayPal" in action.message


def test_tree_menu_command_lists_flows(store, tree_settings):
    orch = WhatsAppOrchestrator(tree_settings, store, FakeEmbedder(), FakeQwen())
    action = asyncio_run(orch.handle_message("1555", "menu"))
    assert action.type == "text"
    assert "Returns & refunds" in action.message
    assert "Payment methods" in action.message


def test_tree_no_match_falls_back_to_rag(store, tree_settings):
    orch = WhatsAppOrchestrator(tree_settings, store, FakeEmbedder(), FakeQwen())
    action = asyncio_run(orch.handle_message("1555", "tell me about quantum physics"))
    assert action.type == "fallback"


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
