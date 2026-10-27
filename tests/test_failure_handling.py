"""Тести failure handling: симулюємо збої API без реальних запитів.

Запуск з кореня репозиторію:
    python -m unittest tests.test_failure_handling -v
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import groq
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "task1_classifier"))

from common import llm_client  # noqa: E402
import classifier  # noqa: E402

VALID = (
    '{"category": "refund_request", "secondary_categories": [], "priority": "P2", '
    '"next_step": "Check the charge", "language": "en", "sentiment": "calm", '
    '"needs_human_review": true, "confidence": 0.9, "reasoning": "Explicit refund ask."}'
)
REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def ok(content: str):
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=100, prompt_tokens_details=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)


def rate_limit(retry_after: str | None = None):
    headers = {"retry-after": retry_after} if retry_after else {}
    return groq.RateLimitError("rate limited", response=httpx.Response(429, headers=headers, request=REQUEST), body=None)


def timeout():
    return groq.APITimeoutError(request=REQUEST)


def bad_json_400():
    return groq.BadRequestError("json_validate_failed", response=httpx.Response(400, request=REQUEST), body=None)


class FakeClient:
    """Видає заздалегідь задані відповіді/помилки по черзі і запам'ятовує, яку модель викликали."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, **kwargs):
        self.calls.append(model)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def run(script, models=("model-a", "model-b")):
    fake = FakeClient(script)
    with patch.object(llm_client, "get_client", return_value=fake), \
         patch.object(llm_client, "cost_usd", return_value=0.0), \
         patch.object(llm_client.time, "sleep") as sleep:
        res = llm_client.call_json([{"role": "user", "content": "x"}], classifier.LLMClassification, list(models))
    return res, fake, sleep


class FailureHandlingTest(unittest.TestCase):
    def test_happy_path(self):
        res, fake, _ = run([ok(VALID)])
        self.assertTrue(res.ok)
        self.assertEqual(res.attempts, 1)

    def test_invalid_json_is_repaired(self):
        # модель повернула обрізаний JSON -> repair-запит -> валідна відповідь
        res, fake, _ = run([ok('{"category": "refund_req'), ok(VALID)])
        self.assertTrue(res.ok)
        self.assertEqual(res.model, "model-a")
        self.assertEqual(res.attempts, 2)
        self.assertIn("invalid JSON", res.errors[0])

    def test_schema_violation_is_repaired(self):
        # валідний JSON, але неіснуюча категорія -> pydantic відхиляє -> repair
        bad = VALID.replace("refund_request", "angry_customer")
        res, _, _ = run([ok(bad), ok(VALID)])
        self.assertTrue(res.ok)
        self.assertIn("schema mismatch", res.errors[0])

    def test_invalid_json_twice_falls_back_to_next_model(self):
        res, fake, _ = run([ok("not json"), ok("still not json"), ok(VALID)])
        self.assertTrue(res.ok)
        self.assertEqual(res.model, "model-b")
        self.assertEqual(fake.calls, ["model-a", "model-a", "model-b"])

    def test_groq_json_validate_400_is_repaired(self):
        # Groq сам повертає 400, якщо в JSON mode модель видала не-JSON
        res, _, _ = run([bad_json_400(), ok(VALID)])
        self.assertTrue(res.ok)
        self.assertEqual(res.model, "model-a")

    def test_rate_limit_respects_retry_after(self):
        res, _, sleep = run([rate_limit("2"), ok(VALID)])
        self.assertTrue(res.ok)
        sleep.assert_called_once_with(2.0)

    def test_rate_limit_exponential_backoff_without_header(self):
        res, _, sleep = run([rate_limit(), rate_limit(), ok(VALID)])
        self.assertTrue(res.ok)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1.5, 3.0])

    def test_timeout_exhausts_retries_then_fallback_model(self):
        script = [timeout()] * (llm_client.MAX_RETRIES + 1) + [ok(VALID)]
        res, fake, _ = run(script)
        self.assertTrue(res.ok)
        self.assertEqual(res.model, "model-b")
        self.assertEqual(fake.calls.count("model-a"), llm_client.MAX_RETRIES + 1)

    def test_all_models_down_returns_not_ok(self):
        script = [timeout()] * (2 * (llm_client.MAX_RETRIES + 1))
        res, _, _ = run(script)
        self.assertFalse(res.ok)

    def test_classifier_routes_to_manual_queue_when_llm_down(self):
        with patch.object(classifier, "call_json", return_value=llm_client.LLMResult(data=None, model=None)):
            out = classifier.classify("I want my money back")
        self.assertEqual(out["source"], "fallback")
        self.assertTrue(out["needs_human_review"])
        self.assertEqual(out["priority"], "P2")

    def test_safety_guardrail_works_even_when_llm_down(self):
        with patch.object(classifier, "call_json", return_value=llm_client.LLMResult(data=None, model=None)):
            out = classifier.classify("I don't want to live anymore")
        self.assertEqual(out["category"], "safety_crisis")
        self.assertEqual(out["priority"], "P1")
        self.assertEqual(out["assigned_team"], "Trust & Safety")

    def test_legal_guardrail_overrides_model_priority(self):
        # модель повернула P2, але в тексті погроза chargeback -> guardrail піднімає до P1
        llm = llm_client.LLMResult(data=classifier.LLMClassification.model_validate_json(VALID), model="model-a")
        with patch.object(classifier, "call_json", return_value=llm):
            out = classifier.classify("I was charged twice, refund me or I'll dispute with my bank")
        self.assertEqual(out["priority"], "P1")
        self.assertIn("legal_threat_override", out["guardrail_flags"])

    def test_legal_guardrail_works_even_when_llm_down(self):
        with patch.object(classifier, "call_json", return_value=llm_client.LLMResult(data=None, model=None)):
            out = classifier.classify("Мій юрист вже готує позов")
        self.assertEqual(out["priority"], "P1")
        self.assertTrue(out["needs_human_review"])


if __name__ == "__main__":
    unittest.main()
