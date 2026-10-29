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
from typing import Literal

from langdetect import DetectorFactory, LangDetectException, detect
from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "task1_classifier"))
sys.path.insert(0, str(HERE))

from common.llm_client import APIKeyError, call_json  # noqa: E402
from classifier import classify  # noqa: E402
import retrieval  # noqa: E402

DetectorFactory.seed = 0  # langdetect детермінований

PROMPTS_DIR = HERE / "prompts"
ANALYZE_PROMPT = "analyze_v2"  # v2 = v1 + поле about_nebula
GENERATE_PROMPT = "generate_v2"  # v1 лишається для порівняння, див. PROMPTS.md
# qwen — останній запасний: у 5–10 разів дорожчий і preview, але має окремий добовий ліміт Groq,
# тож демо не падає, коли gpt-oss вичерпали 200k токенів/добу
FALLBACK_MODEL = "qwen/qwen3.8-27b"
CLASSIFY_MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b", FALLBACK_MODEL]
ANALYZE_MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b", FALLBACK_MODEL]
GENERATE_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", FALLBACK_MODEL]

MIN_RETRIEVAL_SCORE = 2.0   # нижче — вважаємо, що релевантної статті немає
SHORT_MAX_WORDS = 60


# ---------- схеми відповідей LLM ----------

class Analysis(BaseModel):
    summary: str
    customer_language: str
    # для тікетів не про Nebula модель повертає [] — це не помилка, шукаємо тоді за текстом тікету
    search_queries: list[str] = Field(default_factory=list, max_length=3)
    key_facts: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    about_nebula: bool = True  # з analyze_v2; false → шаблонна відповідь без генерації


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
    # чи відповідають надані статті на питання клієнта (з generate_v2); BM25 сам цього не знає
    kb_coverage: Literal["full", "partial", "none"] = "full"


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

# Підпис дописує код: моделі часто пропускають його в короткому варіанті (є лише у 2 з 10)
SIGNATURES = {
    "uk": "З повагою, команда підтримки Nebula",
    "ru": "С уважением, команда поддержки Nebula",
    "en": "Best regards, Nebula Support Team",
    "es": "Atentamente, el equipo de soporte de Nebula",
    "de": "Mit freundlichen Grüßen, Ihr Nebula Support-Team",
    "pl": "Z poważaniem, zespół wsparcia Nebula",
    "fr": "Cordialement, l'équipe d'assistance Nebula",
    "it": "Cordiali saluti, il team di supporto Nebula",
    "pt": "Com os melhores cumprimentos, a equipa de apoio da Nebula",
    "nl": "Met vriendelijke groet, het Nebula-supportteam",
}


def add_signature(replies: dict, language: str) -> dict:
    """Дописує підпис мовою клієнта, якщо модель його пропустила."""
    signature = SIGNATURES.get(language, SIGNATURES["en"])
    for tone, text in replies.items():
        # модель інколи екранує перенос двічі: у тексті буквально «\n» (66 з 363 відповідей eval)
        text = replies[tone] = text.replace("\\r\\n", "\n").replace("\\n", "\n")
        # лише кінець тексту: «...у Nebula. Після цього ми допоможемо.» — це не підпис
        if "Nebula" not in text[-30:]:
            replies[tone] = text.rstrip() + "\n\n" + signature
    return replies


# Звернення не про Nebula («столиця Англії», «хочу пива»): готовий текст замість генерації.
# Модель на таких тікетах обіцяла «відділ підтримки перевірить і зв'яжеться» — шаблон нічого не обіцяє.
OFF_TOPIC_REPLIES = {
    "uk": {
        "formal": "Добрий день! Дякуємо за ваше повідомлення. Служба підтримки Nebula допомагає з питаннями щодо застосунку: підписки, оплати, акаунта, гороскопів і консультацій з астрологами. Якщо у вас є таке питання, опишіть його, будь ласка, детальніше, і ми з радістю допоможемо.",
        "empathetic": "Дякуємо, що написали нам! На жаль, із цим запитом ми допомогти не зможемо: ми підтримуємо лише користувачів застосунку Nebula. Якщо у вас є питання щодо підписки, акаунта, гороскопів чи консультацій, просто напишіть, ми на зв'язку.",
        "short": "Ми допомагаємо лише з питаннями щодо застосунку Nebula. Опишіть, будь ласка, що саме вам потрібно.",
    },
    "ru": {
        "formal": "Здравствуйте! Спасибо за ваше сообщение. Служба поддержки Nebula помогает с вопросами о приложении: подписка, оплата, аккаунт, гороскопы и консультации с астрологами. Если у вас есть такой вопрос, пожалуйста, опишите его подробнее, и мы с радостью поможем.",
        "empathetic": "Спасибо, что написали нам! К сожалению, с этим запросом мы помочь не сможем: мы поддерживаем только пользователей приложения Nebula. Если у вас есть вопрос о подписке, аккаунте, гороскопах или консультациях, просто напишите, мы на связи.",
        "short": "Мы помогаем только с вопросами о приложении Nebula. Пожалуйста, уточните, что именно вам нужно.",
    },
    "en": {
        "formal": "Hello, and thank you for your message. Nebula Support helps with questions about the Nebula app: subscriptions, payments, your account, horoscopes and consultations with astrologers. If you have a question like this, please describe it in more detail and we will be glad to help.",
        "empathetic": "Thank you for reaching out! Unfortunately, we can't help with this request: we support Nebula app users only. If you have a question about your subscription, account, horoscopes or consultations, just write to us, we're here for you.",
        "short": "We can only help with questions about the Nebula app. Please tell us what you need.",
    },
    "es": {
        "formal": "Hola, gracias por su mensaje. El soporte de Nebula ayuda con preguntas sobre la aplicación: suscripciones, pagos, su cuenta, horóscopos y consultas con astrólogos. Si tiene una pregunta de este tipo, descríbala con más detalle y con gusto le ayudaremos.",
        "empathetic": "¡Gracias por escribirnos! Lamentablemente, no podemos ayudar con esta solicitud: solo damos soporte a los usuarios de la aplicación Nebula. Si tiene alguna pregunta sobre su suscripción, cuenta, horóscopos o consultas, escríbanos, estamos aquí para ayudarle.",
        "short": "Solo podemos ayudar con preguntas sobre la aplicación Nebula. Indíquenos qué necesita.",
    },
    "de": {
        "formal": "Guten Tag, vielen Dank für Ihre Nachricht. Der Nebula-Support hilft bei Fragen zur App: Abonnements, Zahlungen, Ihr Konto, Horoskope und Beratungen mit Astrologen. Wenn Sie eine solche Frage haben, beschreiben Sie sie bitte etwas genauer, wir helfen Ihnen gern.",
        "empathetic": "Danke, dass Sie uns geschrieben haben! Leider können wir bei dieser Anfrage nicht helfen: Wir unterstützen nur Nutzer der Nebula-App. Wenn Sie eine Frage zu Ihrem Abonnement, Konto, Horoskopen oder Beratungen haben, schreiben Sie uns einfach, wir sind für Sie da.",
        "short": "Wir können nur bei Fragen zur Nebula-App helfen. Bitte teilen Sie uns mit, was Sie benötigen.",
    },
    "pl": {
        "formal": "Dzień dobry, dziękujemy za wiadomość. Wsparcie Nebula pomaga w sprawach dotyczących aplikacji: subskrypcji, płatności, konta, horoskopów i konsultacji z astrologami. Jeśli ma Pan/Pani takie pytanie, prosimy opisać je dokładniej, chętnie pomożemy.",
        "empathetic": "Dziękujemy, że do nas piszesz! Niestety nie możemy pomóc w tej sprawie: wspieramy wyłącznie użytkowników aplikacji Nebula. Jeśli masz pytanie o subskrypcję, konto, horoskopy lub konsultacje, po prostu napisz, jesteśmy tu dla Ciebie.",
        "short": "Pomagamy tylko w sprawach dotyczących aplikacji Nebula. Napisz, proszę, czego potrzebujesz.",
    },
    "fr": {
        "formal": "Bonjour, merci pour votre message. L'assistance Nebula répond aux questions concernant l'application : abonnements, paiements, votre compte, horoscopes et consultations avec des astrologues. Si vous avez une question de ce type, merci de la décrire plus en détail et nous vous aiderons volontiers.",
        "empathetic": "Merci de nous avoir écrit ! Malheureusement, nous ne pouvons pas vous aider pour cette demande : nous accompagnons uniquement les utilisateurs de l'application Nebula. Si vous avez une question sur votre abonnement, votre compte, vos horoscopes ou vos consultations, écrivez-nous simplement, nous sommes là pour vous.",
        "short": "Nous ne pouvons vous aider que pour des questions sur l'application Nebula. Dites-nous ce dont vous avez besoin.",
    },
    "it": {
        "formal": "Buongiorno, grazie per il suo messaggio. Il supporto Nebula risponde a domande sull'app: abbonamenti, pagamenti, account, oroscopi e consulenze con gli astrologi. Se ha una domanda di questo tipo, la descriva più nel dettaglio e saremo lieti di aiutarla.",
        "empathetic": "Grazie per averci scritto! Purtroppo non possiamo aiutarti con questa richiesta: assistiamo solo gli utenti dell'app Nebula. Se hai una domanda su abbonamento, account, oroscopi o consulenze, scrivici pure, siamo qui per te.",
        "short": "Possiamo aiutarti solo con domande sull'app Nebula. Dicci di cosa hai bisogno.",
    },
    "pt": {
        "formal": "Olá, obrigado pela sua mensagem. O apoio da Nebula ajuda com questões sobre a aplicação: subscrições, pagamentos, a sua conta, horóscopos e consultas com astrólogos. Se tiver uma questão deste tipo, descreva-a com mais detalhe e teremos todo o gosto em ajudar.",
        "empathetic": "Obrigado por nos escrever! Infelizmente, não conseguimos ajudar com este pedido: apoiamos apenas os utilizadores da aplicação Nebula. Se tiver alguma questão sobre a sua subscrição, conta, horóscopos ou consultas, basta escrever-nos, estamos aqui para ajudar.",
        "short": "Só conseguimos ajudar com questões sobre a aplicação Nebula. Diga-nos do que precisa.",
    },
    "nl": {
        "formal": "Goedendag, bedankt voor uw bericht. De Nebula-support helpt bij vragen over de app: abonnementen, betalingen, uw account, horoscopen en consulten met astrologen. Als u zo'n vraag heeft, beschrijf die dan iets uitgebreider en we helpen u graag.",
        "empathetic": "Bedankt dat je ons hebt geschreven! Helaas kunnen we je met dit verzoek niet helpen: we ondersteunen alleen gebruikers van de Nebula-app. Heb je een vraag over je abonnement, account, horoscopen of consulten, stuur ons dan gewoon een bericht, we staan voor je klaar.",
        "short": "We kunnen alleen helpen met vragen over de Nebula-app. Laat ons weten wat je nodig hebt.",
    },
}
# навіть якщо модель скаже «не про Nebula», ці тікети йдуть звичайним шляхом
SENSITIVE_CATEGORIES = {"safety_crisis", "refund_request", "expert_complaint", "account_privacy"}


def off_topic_replies(language: str) -> dict:
    lang = language if language in OFF_TOPIC_REPLIES else "en"
    return add_signature(dict(OFF_TOPIC_REPLIES[lang]), lang)


# uk/ru визначаємо кодом, а не моделлю: наприклад елементарний приклад тестової невалідної поведінки від користувача «хочу пива» однаково пишеться обома мовами
UK_ONLY = re.compile("[іїєґ]", re.IGNORECASE)
RU_ONLY = re.compile("[ыэъё]", re.IGNORECASE)
# часті слова без «особливих» літер, які існують лише в одній з мов
UK_WORDS = re.compile(r"\b(ти|ви|що|як|це|мене|чому|коли|зараз|дякую|можна|треба|скасув\w*|видал\w*)\b", re.IGNORECASE)
RU_WORDS = re.compile(r"\b(что|как|меня|нет|почему|когда|сейчас|спасибо|пожалуйста|можно|нужно|деньги|отмен\w*|удал\w*)\b", re.IGNORECASE)


def fix_uk_ru(text: str, language: str) -> tuple[str, bool]:
    """Повертає (мова, чи неоднозначна). Спершу літери, потім слова; інакше — українська."""
    if language not in ("uk", "ru"):
        return language, False
    for uk, ru in ((UK_ONLY, RU_ONLY), (UK_WORDS, RU_WORDS)):
        if uk.search(text) and not ru.search(text):
            return "uk", False
        if ru.search(text) and not uk.search(text):
            return "ru", False
    return "uk", True


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
        "kb_coverage": gen.kb_coverage,
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
    if "legal_threat" in flags or "legal_threat_override" in flags:
        reasons.append("Юридична погроза / chargeback: не визнавати провину, ескалювати Billing lead і Legal.")
    if "account_security_override" in flags:
        reasons.append("Злам або блокування акаунта: інцидент безпеки → Trust & Safety. Не змінювати пошту, пароль чи "
                       "платежі за проханням з тікету — доступ відновлюється лише після верифікації власника.")
    if cat == "refund_request":
        reasons.append("Рішення про повернення коштів ухвалює Billing після перевірки транзакції.")
    if cat == "expert_complaint":
        reasons.append("Скарга на експерта: не оцінювати експерта до перевірки логу чату; misconduct → Trust & Safety.")
    if cat == "account_privacy" and classification["priority"] in ("P1", "P2") and "account_security_override" not in flags:
        # запит на видалення / експорт даних (P2), а не просте питання про дані (P4)
        reasons.append("Видалення / експорт персональних даних: підтвердити, що запит від власника акаунта.")
    if classification["priority"] == "P1" and not reasons:
        reasons.append("Критичний пріоритет (P1): перевір, чи не потрібна ескалація, перш ніж відповідати.")
    no_kb = not hits or hits[0].score < MIN_RETRIEVAL_SCORE or bool(checks and checks.get("kb_coverage") == "none")
    # «хочу пива», «asdfgh», «напиши вірш»: не тривога, а запит не по темі — одна спокійна причина
    # або модель не знайшла жодної цитати: kb_coverage вона ставить непослідовно
    off_topic = cat == "other" and (no_kb or bool(checks and checks["no_citations"]))
    if off_topic:
        reasons.append("Звернення не стосується Nebula або незрозуміле: достатньо ввічливо попросити уточнити запит. "
                       "Перевір, що відповідь нічого не обіцяє (наприклад, «передамо команді»).")
    elif classification.get("confidence", 1) < 0.6 or cat == "other":
        reasons.append("AI не впевнений, про що тікет: прочитай оригінал уважно.")
    if no_kb and not off_topic:
        reasons.append("У базі знань немає відповіді на це питання: не покладайся на AI-варіанти, уточни в команді.")
    elif checks and checks.get("kb_coverage") == "partial":
        reasons.append("База знань відповідає лише частково: перевір, що решта відповіді не вигадана.")
    if checks:
        if checks["invalid_citations"] or (checks["no_citations"] and checks.get("kb_coverage") != "none" and not off_topic):
            reasons.append("Цитата не підтверджена базою знань: перевір факти перед відправкою.")
        if checks["refund_promise"]:
            reasons.append(f"Відповідь обіцяє повернення коштів ({', '.join(checks['refund_promise'])}) — "
                           "до перевірки Billing це заборонено.")
        if checks["language_mismatch"]:
            reasons.append(f"Мова відповіді не збігається з мовою клієнта ({', '.join(checks['language_mismatch'])}).")
    return {"level": "review" if reasons else "ok", "reasons": reasons, "off_topic": off_topic}


# ---------- конвеєр ----------

def _articles_block(hits: list[retrieval.Hit]) -> str:
    if not hits:
        return "No relevant articles found."
    return "\n\n".join(f'<article id="{h.article.id}" title="{h.article.title}">\n{h.article.body}\n</article>' for h in hits)


def _meta(res) -> dict:
    return {"model": res.model, "api_latency_s": round(res.api_latency_s, 3), "cost_usd": res.cost_usd,
            "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens,
            "cached_tokens": res.cached_tokens, "attempts": res.attempts, "errors": res.errors}


def analyze(text: str, models: list[str] | None = None, prompt: str = ANALYZE_PROMPT):
    messages = [{"role": "system", "content": load_prompt(prompt)},
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
        f_cls = pool.submit(classify, text, models=CLASSIFY_MODELS)
        f_an = pool.submit(analyze, text)
        classification, an_res = f_cls.result(), f_an.result()

    if an_res.ok:
        analysis = an_res.data
    else:
        # аналіз не вдався: шукаємо прямо за текстом тікету, summary немає
        analysis = Analysis(summary="(AI-аналіз недоступний)", customer_language=classification.get("language", "en"),
                            search_queries=[text[:300]])
    analysis.customer_language, lang_ambiguous = fix_uk_ru(text, analysis.customer_language)
    if not analysis.search_queries:
        analysis.search_queries = [text[:300]]

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
    # low_confidence — не ризик, а «незрозуміло»: такий тікет теж може піти в шаблон
    sensitive = (classification["category"] in SENSITIVE_CATEGORIES
                 or any(f != "low_confidence" for f in classification.get("guardrail_flags", []))
                 or classification["priority"] == "P1")
    # нісенітниця («ідоадфлоаф…»): модель інколи каже about_nebula = true, і генерація обіцяє «зв'яжемося з вами»
    unclear = classification["category"] == "other" and classification.get("confidence", 1) < 0.6
    if judgment["level"] == "blocked":
        out["human_judgment"] = judgment
    elif (not analysis.about_nebula or unclear) and not sensitive:
        lang = analysis.customer_language
        reasons = ["Звернення не стосується Nebula: замість AI-генерації — готова відповідь із проханням уточнити запит."]
        if lang not in OFF_TOPIC_REPLIES:
            reasons.append(f"Шаблону мовою «{lang}» немає — відповідь англійською.")
        out.update(replies=off_topic_replies(lang), missing_info=[])
        out["human_judgment"] = {"level": "ok", "reasons": reasons, "off_topic": True}
    else:
        gen_res, _ = generate(text, analysis, classification, hits, generate_models, generate_prompt)
        out["meta"]["generate"] = _meta(gen_res)
        if gen_res.ok:
            checks, citations = run_checks(gen_res.data, hits, analysis.customer_language)
            out.update(replies=add_signature(gen_res.data.replies.model_dump(), analysis.customer_language), citations=citations,
                       agent_notes=gen_res.data.agent_notes, checks=checks)
            out["human_judgment"] = human_judgment(classification, hits, checks)
        else:
            daily = any("(TPD)" in e or "(RPD)" in e for e in gen_res.errors)
            why = "добовий ліміт безкоштовного тарифу Groq вичерпано на всіх моделях" if daily else "збій API"
            out["human_judgment"] = {"level": "review", "reasons": [
                f"AI не зміг згенерувати відповіді ({why}). Напиши відповідь вручну, статті з бази знань нижче."]}

    if lang_ambiguous and out["human_judgment"]["level"] != "blocked":
        out["human_judgment"]["level"] = "review"
        out["human_judgment"]["reasons"].append(
            "Мова неоднозначна (uk/ru): відповідь українською. Перевір, якою мовою пише клієнт.")
            
    steps = [m for m in out["meta"].values() if isinstance(m, dict)]
    out["meta"]["total_cost_usd"] = sum(m.get("cost_usd", 0) for m in steps)
    out["meta"]["total_latency_s"] = round(time.perf_counter() - started, 3)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        print(json.dumps(assist(sys.argv[1]), ensure_ascii=False, indent=2))
    except APIKeyError as e:
        sys.exit(str(e))
