"""Тести детермінованої частини Task 2 (без API): пошук, перевірка цитат, обіцянки, human judgment.

Запуск з кореня репозиторію:
    python -m unittest tests.test_assistant_checks -v
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "task2_agent_assistant"))

import assistant  # noqa: E402
import retrieval  # noqa: E402


def cls(category="product_question", priority="P4", flags=(), confidence=0.9):
    return {"category": category, "priority": priority, "guardrail_flags": list(flags), "confidence": confidence}


def gen(formal="Добрий день. Ми перевіримо списання.", empathetic="Розумію, як це неприємно. Ми перевіримо списання.",
        short="Ми перевіримо списання.", citations=()):
    return assistant.Generation(replies=assistant.Replies(formal=formal, empathetic=empathetic, short=short),
                                citations=[assistant.Citation(**c) for c in citations])


class RetrievalTest(unittest.TestCase):
    def test_kb_loads_all_articles(self):
        articles, _ = retrieval.load_kb()
        self.assertGreaterEqual(len(articles), 17)

    def test_search_finds_expected_article_first(self):
        cases = {
            "verification code email not received": "login-issues",
            "promo code invalid": "promo-codes",
            "delete my account and personal data": "account-deletion-gdpr",
            "expert asked for payment via PayPal": "expert-misconduct",
        }
        for query, expected in cases.items():
            self.assertEqual(retrieval.search([query])[0].article.id, expected, query)

    def test_category_boost_multiplies_score(self):
        plain = {h.article.id: h.score for h in retrieval.search(["refund"], k=17)}
        boosted = {h.article.id: h.score for h in retrieval.search(["refund"], k=17, boost_category="refund_request")}
        self.assertAlmostEqual(boosted["refund-policy"], plain["refund-policy"] * retrieval.CATEGORY_BOOST, places=2)
        self.assertEqual(boosted["double-charge"], plain["double-charge"])  # інша категорія — без бусту

    def test_quote_must_be_verbatim(self):
        art = retrieval.get_article("refund-policy")
        self.assertTrue(retrieval.quote_in_article(
            "Agents must never promise a refund before the billing team has verified the transaction.", art))
        # перефразування — не цитата
        self.assertFalse(retrieval.quote_in_article("Agents should not promise refunds until billing checks.", art))
        # занадто коротке — не рахується
        self.assertFalse(retrieval.quote_in_article("refund", art))


class ChecksTest(unittest.TestCase):
    hits = retrieval.search(["refund policy"], k=3)

    def test_invalid_citation_detected(self):
        g = gen(citations=[{"article_id": "refund-policy", "quote": "Refunds are always approved within 24 hours."}])
        checks, citations = assistant.run_checks(g, self.hits, "uk")
        self.assertEqual(len(checks["invalid_citations"]), 1)
        self.assertFalse(citations[0]["valid"])

    def test_citation_to_not_retrieved_article_is_invalid(self):
        g = gen(citations=[{"article_id": "promo-codes", "quote": "Agents cannot create new promo codes."}])
        checks, _ = assistant.run_checks(g, self.hits, "uk")
        self.assertEqual(len(checks["invalid_citations"]), 1)

    def test_refund_promise_detected_in_several_languages(self):
        for text in ["We will refund the duplicate payment today.", "Ми повернемо вам кошти протягом доби.",
                     "Мы вернем вам деньги.", "Le reembolsaremos el importe."]:
            checks, _ = assistant.run_checks(gen(short=text), self.hits, "uk")
            self.assertEqual(checks["refund_promise"], ["short"], text)

    def test_neutral_wording_is_not_a_promise(self):
        checks, _ = assistant.run_checks(gen(short="Our payments team will review your request."), self.hits, "en")
        self.assertEqual(checks["refund_promise"], [])

    def test_short_reply_length_limit(self):
        checks, _ = assistant.run_checks(gen(short="слово " * 70), self.hits, "uk")
        self.assertTrue(checks["short_too_long"])


class HumanJudgmentTest(unittest.TestCase):
    hits = retrieval.search(["cancel subscription"], k=3)

    def test_safety_is_blocked(self):
        self.assertEqual(assistant.human_judgment(cls("safety_crisis", "P1"), self.hits, None)["level"], "blocked")

    def test_refund_requires_review(self):
        self.assertEqual(assistant.human_judgment(cls("refund_request", "P2"), self.hits, None)["level"], "review")

    def test_legal_threat_requires_review(self):
        hj = assistant.human_judgment(cls("billing_subscription", "P1", ["legal_threat_override"]), self.hits, None)
        self.assertEqual(hj["level"], "review")

    def test_simple_question_is_ok(self):
        self.assertEqual(assistant.human_judgment(cls("billing_subscription", "P3"), self.hits, None)["level"], "ok")

    def test_no_relevant_article_requires_review(self):
        self.assertEqual(assistant.human_judgment(cls(), [], None)["level"], "review")

    def test_off_topic_gets_one_calm_reason(self):
        hj = assistant.human_judgment(cls("other", "P4"), [], None)
        self.assertEqual((hj["level"], hj["off_topic"], len(hj["reasons"])), ("review", True, 1))
        # статті знайшлись, модель поставила kb_coverage=full, але не процитувала нічого → теж не по темі
        checks, _ = assistant.run_checks(gen(citations=[]), self.hits, "uk")
        hj = assistant.human_judgment(cls("other", "P4"), self.hits, checks)
        self.assertEqual((hj["off_topic"], len(hj["reasons"])), (True, 1))

    def test_refund_promise_in_draft_requires_review(self):
        checks, _ = assistant.run_checks(gen(short="We will refund you."), self.hits, "en")
        self.assertEqual(assistant.human_judgment(cls("billing_subscription", "P3"), self.hits, checks)["level"], "review")


class OffTopicFlowTest(unittest.TestCase):
    """assist() з підміненими викликами LLM: звернення не про Nebula → шаблон без генерації."""

    def run_assist(self, text, category, priority="P4", about_nebula=False, lang="uk"):
        meta = dict(model="fake", api_latency_s=0, cost_usd=0, prompt_tokens=0, completion_tokens=0,
                    cached_tokens=0, attempts=1, errors=[])
        cls_ = {**cls(category, priority), "language": lang, "meta": meta}
        an = SimpleNamespace(ok=True, data=assistant.Analysis(summary="s", customer_language=lang, search_queries=[text],
                                                              about_nebula=about_nebula), **meta)
        failed = SimpleNamespace(ok=False, data=None, **meta)
        with patch.object(assistant, "classify", return_value=cls_), patch.object(assistant, "analyze", return_value=an), \
                patch.object(assistant, "generate", return_value=(failed, None)) as gen_mock:
            return assistant.assist(text), gen_mock

    def test_off_topic_uses_template_without_generation(self):
        r, gen_mock = self.run_assist("столиця англії", "product_question")
        gen_mock.assert_not_called()
        self.assertEqual((r["human_judgment"]["level"], r["human_judgment"]["off_topic"]), ("ok", True))
        self.assertTrue(r["replies"]["short"].endswith("команда підтримки Nebula"))

    def test_unknown_language_falls_back_to_english(self):
        r, _ = self.run_assist("quelle est la capitale de la France", "other", lang="fr")
        self.assertTrue(r["replies"]["short"].endswith("Nebula Support Team"))
        self.assertEqual(len(r["human_judgment"]["reasons"]), 2)

    def test_crisis_is_blocked_even_if_model_says_off_topic(self):
        # реальний випадок: на тікеті #7 analyze_v2 повернув about_nebula = false
        r, _ = self.run_assist("Nothing matters anymore.", "safety_crisis", "P1", lang="en")
        self.assertEqual(r["human_judgment"]["level"], "blocked")
        self.assertIsNone(r["replies"])

    def test_refund_goes_normal_path_even_if_model_says_off_topic(self):
        _, gen_mock = self.run_assist("Поверніть гроші", "refund_request", "P2")
        gen_mock.assert_called_once()


class LanguageAndSignatureTest(unittest.TestCase):
    def test_uk_ru_by_letters_and_words(self):
        cases = {"Хочу скасувати підписку": ("uk", False), "Вы можете помочь?": ("ru", False),
                 "Хочу отменить подписку": ("ru", False), "Як скасувати": ("uk", False),
                 "ти тупий бот": ("uk", False), "хочу пива": ("uk", True)}  # однаково пишеться обома мовами → uk + позначка
        for text, expected in cases.items():
            self.assertEqual(assistant.fix_uk_ru(text, "ru"), expected, text)

    def test_other_languages_untouched(self):
        self.assertEqual(assistant.fix_uk_ru("¿Cómo puedo ver mi signo?", "es"), ("es", False))

    def test_signature_added_only_when_missing(self):
        out = assistant.add_signature({"formal": "Text.\n\nNebula Support Team", "short": "Коротко."}, "uk")
        self.assertEqual(out["formal"], "Text.\n\nNebula Support Team")
        self.assertTrue(out["short"].endswith("З повагою, команда підтримки Nebula"))
        self.assertTrue(assistant.add_signature({"short": "x"}, "fr")["short"].endswith("Nebula Support Team"))
        # «Nebula» в останньому реченні — ще не підпис
        out = assistant.add_signature({"short": "Уточніть запит щодо Nebula. Після цього ми допоможемо."}, "uk")
        self.assertTrue(out["short"].endswith("команда підтримки Nebula"))


if __name__ == "__main__":
    unittest.main()
