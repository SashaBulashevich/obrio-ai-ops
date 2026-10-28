"""AI-класифікатор тікетів Nebula.

Використання:
    python task1_classifier/classifier.py                  # інтерактивний режим: вводиш тікети по одному
    python task1_classifier/classifier.py "текст тікету"   # один тікет -> повний JSON
    python task1_classifier/classifier.py "текст" --model openai/gpt-oss-20b --prompt v1
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.llm_client import call_json  # noqa: E402

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

DEFAULT_PROMPT = "v3"
# Основна модель + fallback-ланцюжок (вибір обґрунтований у README, розділ 4)
DEFAULT_MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]

Category = Literal[
    "billing_subscription", "refund_request", "technical_issue", "expert_complaint",
    "account_privacy", "product_question", "feedback", "safety_crisis", "other",
]
Priority = Literal["P1", "P2", "P3", "P4"]

# Маршрутизація — детермінована, в коді, а не в LLM: команда залежить лише від категорії.
TEAM_BY_CATEGORY = {
    "billing_subscription": "Billing",
    "refund_request": "Billing",
    "technical_issue": "Tech Support",
    "expert_complaint": "Expert Quality",
    "account_privacy": "Privacy / Legal",
    "product_question": "Tier-1 Support",
    "feedback": "Product",
    "safety_crisis": "Trust & Safety",
    "other": "Tier-1 Support",
}


class LLMClassification(BaseModel):
    """Схема, яку має повернути модель. Невалідні значення -> retry/fallback."""
    category: Category
    secondary_categories: list[Category] = Field(default_factory=list)
    priority: Priority
    next_step: str
    language: str
    sentiment: Literal["calm", "frustrated", "aggressive"]
    needs_human_review: bool
    confidence: float = Field(ge=0, le=1)
    reasoning: str


# Safety net: детермінована перевірка, яка НЕ залежить від LLM.
# Якщо модель помилиться або впаде — кризовий тікет все одно піде людині з P1.
SAFETY_PATTERNS = re.compile(
    r"suicid|kill myself|end my life|self[- ]?harm|don'?t want to live|"
    r"самогубств|покінчити з собою|не хочу жити|вбити себе|"
    r"суицид|покончить с собой|не хочу жить|убить себя",
    re.IGNORECASE,
)

# Юридичні ризики: юрист, регулятор або chargeback — завжди P1, незалежно від моделі.
LEGAL_PATTERNS = re.compile(
    r"chargeback|dispute (it |this |the charges? )?with my bank|lawyer|attorney|\bsue\b|\bFTC\b|"
    r"юрист|адвокат|чарджбек|до суду|в суд",
    re.IGNORECASE,
)

# Безпека акаунта (злам, блокування): своєї категорії немає, модель обирала other / account_privacy і P2.
# Ловимо лише «мій акаунт», а не «допоможи зламати Nebula».
_ACCOUNT = r"(?:account|акаунт\w*|аккаунт\w*|профіль|профиль)"
_TAKEOVER = (r"(?:hacked|hack(?:ed)? into|взлома\w*|зламал\w*|stolen|stole|вкрал\w*|украл\w*|"
             r"заблокува\w*|заблокирова\w*|забани\w*|banned|suspended|(?:was|has been|got|is) blocked)")
ACCOUNT_SECURITY_PATTERNS = re.compile(
    rf"{_ACCOUNT}.{{0,25}}{_TAKEOVER}|{_TAKEOVER}.{{0,25}}{_ACCOUNT}|"
    r"\b(?:мене|меня)\s+(?:за)?(?:блокува|блокирова|забани)\w*|"
    r"\bi(?:'ve| have| was| am| got)\s+(?:been\s+)?(?:banned|blocked|locked out|suspended)\b|"
    r"someone (?:logged|signed) in(?:to)? my account",
    re.IGNORECASE,
)


def load_prompt(version: str) -> str:
    return (PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8")


def classify(
    text: str,
    prompt_version: str = DEFAULT_PROMPT,
    models: list[str] | None = None,
    reasoning_effort: str | None = None,
) -> dict:
    models = models or DEFAULT_MODELS
    messages = [
        {"role": "system", "content": load_prompt(prompt_version)},
        # тікет у тегах: модель бачить, де дані користувача, а де інструкції
        {"role": "user", "content": f"<ticket>\n{text}\n</ticket>"},
    ]
    res = call_json(messages, LLMClassification, models, reasoning_effort=reasoning_effort)

    meta = {
        "model": res.model,
        "latency_s": round(res.latency_s, 3),
        "api_latency_s": round(res.api_latency_s, 3),
        "prompt_tokens": res.prompt_tokens,
        "completion_tokens": res.completion_tokens,
        "cached_tokens": res.cached_tokens,
        "cost_usd": res.cost_usd,
        "attempts": res.attempts,
        "errors": res.errors,
    }

    if res.ok:
        out = res.data.model_dump()
        out["source"] = "llm"
    else:
        # Усі моделі недоступні / повертають сміття -> не вгадуємо, віддаємо людині
        out = {
            "category": "other",
            "secondary_categories": [],
            "priority": "P2",
            "next_step": "LLM unavailable — route to manual triage queue",
            "language": "unknown",
            "sentiment": "calm",
            "needs_human_review": True,
            "confidence": 0.0,
            "reasoning": "Fallback: classification failed after retries on all models.",
            "source": "fallback",
        }

    out = apply_guardrails(text, out)
    out["assigned_team"] = TEAM_BY_CATEGORY[out["category"]]
    if "account_security_override" in out["guardrail_flags"]:
        out["assigned_team"] = "Trust & Safety"  # злам / блокування — інцидент безпеки, а не Tier-1 чи Privacy
    out["meta"] = meta
    return out


def apply_guardrails(text: str, out: dict) -> dict:
    """Детерміновані правила поверх відповіді моделі (рішення людини, не AI)."""
    flags = []
    if SAFETY_PATTERNS.search(text) and out["category"] != "safety_crisis":
        out["secondary_categories"] = [out["category"], *out["secondary_categories"]]
        out["category"] = "safety_crisis"
        flags.append("safety_keyword_override")
    if out["category"] == "safety_crisis":
        out["priority"] = "P1"
        out["needs_human_review"] = True
    if LEGAL_PATTERNS.search(text) and out["priority"] != "P1":
        # Модель пропускала погрозу chargeback, якщо вона після refund-запиту
        out["priority"] = "P1"
        out["needs_human_review"] = True
        flags.append("legal_threat_override")
    if ACCOUNT_SECURITY_PATTERNS.search(text) and out["category"] != "safety_crisis":
        out["priority"] = "P1"
        out["needs_human_review"] = True
        flags.append("account_security_override")
    if out["category"] in ("refund_request", "account_privacy", "expert_complaint"):
        # гроші, персональні дані, звинувачення людей — тільки з людиною
        out["needs_human_review"] = True
    if out["confidence"] < 0.6:
        out["needs_human_review"] = True
        flags.append("low_confidence")
    out["guardrail_flags"] = flags
    return override_next_step(text, out)


# Наступний крок для чутливих тікетів — фіксований текст для агента, а не від моделі.
# У 14 з 48 тікетів eval модель писала його клієнту, а частина обіцяла refund чи видалення акаунта.
SAFE_NEXT_STEPS = {
    "safety_crisis": "Escalate to the Trust & Safety on-call now. A human writes the reply using the crisis "
                     "protocol (local emergency services, findahelpline.com); no templates or bot replies.",
    "account_security": "Escalate to Trust & Safety: check active sessions and recent login, email and payment changes; "
                        "freeze payment changes and restore access only after verifying the account owner.",
    "legal": "Escalate to the Billing lead and Legal. Do not admit fault or promise any outcome; "
             "reply only after their review.",
    "refund_request": "Route to Billing: verify the transaction and where it was paid (App Store / Google Play / web) "
                      "before any refund decision. Do not promise a refund to the customer.",
    "expert_complaint": "Route to Expert Quality: review the chat log before any judgement about the expert; "
                        "escalate fraud or harassment to Trust & Safety. Do not promise refunds or sanctions.",
    "account_privacy": "Route to Privacy: verify that the request comes from the account owner's email before "
                       "any deletion or export. Do not confirm the deletion in advance.",
}


def override_next_step(text: str, out: dict) -> dict:
    """Замінює наступний крок моделі на SAFE_NEXT_STEPS для кризи, юридичних погроз, грошей, експертів і даних."""
    if out["category"] == "safety_crisis":
        key = "safety_crisis"
    elif "account_security_override" in out["guardrail_flags"]:
        key = "account_security"
    elif LEGAL_PATTERNS.search(text):
        key = "legal"
    elif out["category"] in ("refund_request", "expert_complaint"):
        key = out["category"]
    elif out["category"] == "account_privacy" and out["priority"] in ("P1", "P2"):
        key = "account_privacy"  # видалення / експорт даних, а не просте питання про дані (P4)
    else:
        return out
    out["next_step_model"] = out["next_step"]  # що запропонувала модель — для розбору в eval
    out["next_step"] = SAFE_NEXT_STEPS[key]
    out["guardrail_flags"] = [*out["guardrail_flags"], "next_step_override"]
    return out


# Перевірка наступного кроку в eval: він має бути дією для агента, без обіцянок клієнту.
NEXT_STEP_TO_CUSTOMER = re.compile(r"^(please|thank|sorry|dear|hi|hello)\b|\b(you|your|you're|you’re)\b", re.I)
# «we will…» — обіцянка клієнту від імені компанії. Умовний refund в інструкції агенту
# («verify…, issue a refund if confirmed») не рахуємо: це рішення Billing після перевірки.
NEXT_STEP_PROMISE = re.compile(r"\bwe(?:'ll| will)\b", re.I)


def next_step_problem(step: str) -> str:
    """'' якщо крок нормальний, інакше коротка причина."""
    if NEXT_STEP_PROMISE.search(step):
        return "promise"
    if NEXT_STEP_TO_CUSTOMER.search(step):
        return "to_customer"
    return ""


def format_card(r: dict) -> str:
    """Коротка людська картка замість повного JSON (для інтерактивного режиму)."""
    m = r["meta"]
    lines = [
        f"  Категорія:     {r['category']}" + (f"  (+ {', '.join(r['secondary_categories'])})" if r["secondary_categories"] else ""),
        f"  Пріоритет:     {r['priority']}",
        f"  Команда:       {r['assigned_team']}",
        f"  Наступний крок: {r['next_step']}",
        f"  Мова / тон:    {r['language']} / {r['sentiment']}",
        f"  Потрібна людина: {'ТАК' if r['needs_human_review'] else 'ні'}   (confidence {r['confidence']:.2f})",
    ]
    if r["guardrail_flags"]:
        lines.append(f"  Guardrails:    {', '.join(r['guardrail_flags'])}")
    lines.append(f"  Пояснення:     {r['reasoning']}")
    lines.append(
        f"  [{m['model'] or 'fallback'} · {m['api_latency_s']} с · ${m['cost_usd']:.6f} · "
        f"cached {m['cached_tokens']} токенів · спроб {m['attempts']}]"
    )
    return "\n".join(lines)


def interactive(prompt: str, models: list[str] | None):
    print("Nebula ticket classifier — інтерактивний режим.")
    print("Встав текст тікету й натисни Enter. Команди: 'json' — повна відповідь останнього тікету, 'exit' — вихід.\n")
    last = None
    while True:
        try:
            text = input("Тікет > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in ("exit", "quit", "вихід"):
            break
        if text.lower() == "json":
            print(json.dumps(last, ensure_ascii=False, indent=2) if last else "  Ще немає жодного тікету.")
            continue
        last = classify(text, prompt, models)
        print(format_card(last) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Nebula ticket classifier")
    parser.add_argument("text", nargs="?", help="текст тікету; без нього — інтерактивний режим")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--model", action="append", help="модель (можна кілька разів — буде fallback-ланцюжок)")
    args = parser.parse_args()
    if args.text is None:
        interactive(args.prompt, args.model)
        return
    result = classify(args.text, args.prompt, args.model)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
