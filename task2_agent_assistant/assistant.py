"""AI-асистент support-агента Nebula: тікет -> summary, 3 тони відповіді, цитати з бази знань.

Конвеєр:
  1. паралельно: класифікація (Task 1) + аналіз тікету (summary, мова, англійські пошукові запити)
  2. пошук BM25 у базі знань (з бустом за категорією з кроку 1)
  3. генерація 3 варіантів відповіді мовою клієнта з цитатами
  4. перевірки в коді: цитати дослівно є в статтях, немає обіцянок повернення коштів,
     мова відповіді = мова клієнта, короткий варіант справді короткий
  5. рівень human judgment: blocked / review / ok

Використання:
    python task2_agent_assistant/assistant.py "текст тікету"
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langdetect import DetectorFactory, LangDetectException, detect
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "task1_classifier"))
sys.path.insert(0, str(HERE))

from common.llm_client import call_json  # noqa: E402
from classifier import classify  # noqa: E402
import retrieval  # noqa: E402

DetectorFactory.seed = 0  # langdetect детермінований

PROMPTS_DIR = HERE / "prompts"
ANALYZE_PROMPT = "analyze_v1"
GENERATE_PROMPT = "generate_v1"
ANALYZE_MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]
GENERATE_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

MIN_RETRIEVAL_SCORE = 2.0   # нижче — вважаємо, що релевантної статті немає
SHORT_MAX_WORDS = 60


# ---------- схеми відповідей LLM ----------

class Analysis(BaseModel):
    summary: str
    customer_language: str
    search_queries: list[str] = Field(min_length=1, max_length=3)
    key_facts: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


class Replies(BaseModel):
    formal: str = Field(min_length=1)
    empathetic: str = Field(min_length=1)
    short: str = Field(min_length=1)


class Citation(BaseModel):
    article_id: str
    quote: str


class Generation(BaseModel):
    replies: Replies
    citations: list[Citation] = Field(default_factory=list)
    agent_notes: str = ""


# ---------- перевірки в коді ----------

# Обіцянка повернення коштів до перевірки Billing — заборонена політикою (kb/refund-policy.md)
REFUND_PROMISE = re.compile(
    r"\bwe(?:'ll| will| have| are going to)\s+(?:refund|reimburse)|"
    r"refund (?:has been|was|will be) (?:processed|issued|approved|sent)|"
    r"you will (?:get|receive) (?:a |your )?(?:full )?refund|"
    r"поверн(?:емо|ули) (?:вам )?(?:кошти|гроші)|кошти (?:буде|будуть|було) повернен|"
    r"верн(?:ем|ули) (?:вам )?(?:деньги|средства)|(?:деньги|средства) (?:будут|были) возвращен|"
    r"le (?:reembolsaremos|devolveremos)|wir (?:erstatten|werden .{0,20}erstatten)",
    re.IGNORECASE,
)
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def detect_language(text: str) -> str | None:
    try:
        return detect(text) if len(text) >= 20 else None
    except LangDetectException:
        return None


def run_checks(gen: Generation, hits: list[retrieval.Hit], customer_language: str) -> tuple[dict, list[dict]]:
    """Детерміновані перевірки згенерованого тексту. Повертає (checks, citations з полем valid)."""
    found = {h.article.id: h.article for h in hits}
    citations = []
    for c in gen.citations:
        article = found.get(c.article_id)
        valid = bool(article and retrieval.quote_in_article(c.quote, article))
        citations.append({"article_id": c.article_id, "quote": c.quote, "valid": valid})

    replies = gen.replies.model_dump()
    langs = {tone: detect_language(text) for tone, text in replies.items()}
    checks = {
        "invalid_citations": [c for c in citations if not c["valid"]],
        "no_citations": len(citations) == 0,
        "refund_promise": [tone for tone, text in replies.items() if REFUND_PROMISE.search(text)],
        # uk/ru langdetect іноді плутає між собою — тоді не сигналимо
        "language_mismatch": [
            tone for tone, lang in langs.items()
            if lang and lang != customer_language and {lang, customer_language} != {"uk", "ru"}
        ],
        "short_too_long": len(replies["short"].split()) > SHORT_MAX_WORDS,
        "formal_has_emoji": bool(EMOJI.search(replies["formal"])),
        "detected_languages": langs,
    }
    return checks, citations


def human_judgment(classification: dict, hits: list[retrieval.Hit], checks: dict | None) -> dict:
    """Рівень втручання людини: blocked (AI-варіанти приховані) / review (потрібна перевірка) / ok."""
    reasons = []
    cat, flags = classification["category"], classification.get("guardrail_flags", [])

    if cat == "safety_crisis":
        return {"level": "blocked", "reasons": [
            "Можливий кризовий стан клієнта. Шаблонні відповіді заборонені протоколом — "
            "відповідь пише людина, тікет ескалюється в Trust & Safety."
        ]}
    if "legal_threat_override" in flags:
        reasons.append("Юридична погроза / chargeback: не визнавати провину, ескалювати Billing lead і Legal.")
    if cat == "refund_request":
        reasons.append("Рішення про повернення коштів ухвалює Billing після перевірки транзакції.")
    if cat == "expert_complaint":
        reasons.append("Скарга на експерта: не оцінювати експерта до перевірки логу чату; misconduct → Trust & Safety.")
    if cat == "account_privacy":
        reasons.append("Персональні дані / видалення акаунта: підтвердити, що запит від власника акаунта.")
    if classification.get("confidence", 1) < 0.6 or cat == "other":
        reasons.append("AI не впевнений, про що тікет: прочитай оригінал уважно.")
    if not hits or hits[0].score < MIN_RETRIEVAL_SCORE:
        reasons.append("У базі знань немає релевантної статті: факти у відповідях можуть бути вигадані.")
    if checks:
        if checks["invalid_citations"] or checks["no_citations"]:
            reasons.append("Цитата не підтверджена базою знань: перевір факти перед відправкою.")
        if checks["refund_promise"]:
            reasons.append(f"Відповідь обіцяє повернення коштів ({', '.join(checks['refund_promise'])}) — "
                           "до перевірки Billing це заборонено.")
        if checks["language_mismatch"]:
            reasons.append(f"Мова відповіді не збігається з мовою клієнта ({', '.join(checks['language_mismatch'])}).")
    return {"level": "review" if reasons else "ok", "reasons": reasons}


# ---------- конвеєр ----------

def _articles_block(hits: list[retrieval.Hit]) -> str:
    if not hits:
        return "No relevant articles found."
    return "\n\n".join(f'<article id="{h.article.id}" title="{h.article.title}">\n{h.article.body}\n</article>' for h in hits)


def _meta(res) -> dict:
    return {"model": res.model, "api_latency_s": round(res.api_latency_s, 3), "cost_usd": res.cost_usd,
            "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens,
            "cached_tokens": res.cached_tokens, "attempts": res.attempts, "errors": res.errors}


def analyze(text: str, models: list[str] | None = None):
    messages = [{"role": "system", "content": load_prompt(ANALYZE_PROMPT)},
                {"role": "user", "content": f"<ticket>\n{text}\n</ticket>"}]
    return call_json(messages, Analysis, models or ANALYZE_MODELS, max_tokens=1500)


def generate(text: str, analysis: Analysis, classification: dict, hits: list[retrieval.Hit],
             models: list[str] | None = None, prompt: str = GENERATE_PROMPT):
    user = (
        f"<ticket>\n{text}\n</ticket>\n\n"
        f"Customer language: {analysis.customer_language}\n"
        f"Ticket summary: {analysis.summary}\n"
        f"Category: {classification['category']}, priority: {classification['priority']}\n"
        f"Missing info to ask for: {', '.join(analysis.missing_info) or 'none'}\n\n"
        f"Help-center articles:\n{_articles_block(hits)}"
    )
    messages = [{"role": "system", "content": load_prompt(prompt)}, {"role": "user", "content": user}]
    res = call_json(messages, Generation, models or GENERATE_MODELS, temperature=0.4, max_tokens=4000)
    if not res.ok:
        return res, messages
    # Одна спроба виправити цитати, яких немає в статтях (захист від галюцинацій)
    _, citations = run_checks(res.data, hits, analysis.customer_language)
    bad = [c for c in citations if not c["valid"]]
    if bad:
        fix = messages + [
            {"role": "assistant", "content": res.data.model_dump_json()},
            {"role": "user", "content": "These citations are not exact quotes from the provided articles: "
             + json.dumps(bad, ensure_ascii=False)
             + ". Copy quotes word-for-word from the articles (or remove them). Return the full corrected JSON."},
        ]
        retry = call_json(fix, Generation, [res.model], temperature=0.0, max_tokens=4000)
        if retry.ok:
            retry.cost_usd += res.cost_usd
            retry.attempts += res.attempts
            retry.errors = res.errors + ["citation_repair"] + retry.errors
            return retry, messages
    return res, messages


def assist(text: str, generate_models: list[str] | None = None, generate_prompt: str = GENERATE_PROMPT) -> dict:
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_cls = pool.submit(classify, text)
        f_an = pool.submit(analyze, text)
        classification, an_res = f_cls.result(), f_an.result()

    if an_res.ok:
        analysis = an_res.data
    else:
        # аналіз не вдався: шукаємо прямо за текстом тікету, summary немає
        analysis = Analysis(summary="(AI-аналіз недоступний)", customer_language=classification.get("language", "en"),
                            search_queries=[text[:300]])

    hits = retrieval.search(analysis.search_queries, k=3, boost_category=classification["category"])
    out = {
        "ticket": text,
        "summary": analysis.summary,
        "customer_language": analysis.customer_language,
        "key_facts": analysis.key_facts,
        "missing_info": analysis.missing_info,
        "search_queries": analysis.search_queries,
        "classification": {k: v for k, v in classification.items() if k != "meta"},
        "articles": [{"id": h.article.id, "title": h.article.title, "score": h.score, "body": h.article.body} for h in hits],
        "replies": None, "citations": [], "agent_notes": "", "checks": None,
        "meta": {"classify": classification["meta"], "analyze": _meta(an_res)},
    }

    judgment = human_judgment(classification, hits, None)
    if judgment["level"] == "blocked":
        out["human_judgment"] = judgment
    else:
        gen_res, _ = generate(text, analysis, classification, hits, generate_models, generate_prompt)
        out["meta"]["generate"] = _meta(gen_res)
        if gen_res.ok:
            checks, citations = run_checks(gen_res.data, hits, analysis.customer_language)
            out.update(replies=gen_res.data.replies.model_dump(), citations=citations,
                       agent_notes=gen_res.data.agent_notes, checks=checks)
            out["human_judgment"] = human_judgment(classification, hits, checks)
        else:
            out["human_judgment"] = {"level": "review", "reasons": [
                "AI не зміг згенерувати відповіді (збій API). Напиши відповідь вручну, статті з бази знань нижче."]}

    steps = [m for m in out["meta"].values() if isinstance(m, dict)]
    out["meta"]["total_cost_usd"] = sum(m.get("cost_usd", 0) for m in steps)
    out["meta"]["total_latency_s"] = round(time.perf_counter() - started, 3)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(assist(sys.argv[1]), ensure_ascii=False, indent=2))
