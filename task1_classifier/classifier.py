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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.llm_client import APIKeyError, call_json  # noqa: E402

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
_TAKEOVER = (r"(?:hacked|hack(?:ed)? into|взлома\w*|взламан\w*|злама[влн]\w*|stolen|stole|вкрал\w*|украл\w*|"
             r"заблокува\w*|заблокирова\w*|забани\w*|banned|suspended|(?:was|has been|got|is) blocked)")
ACCOUNT_SECURITY_PATTERNS = re.compile(
    rf"{_ACCOUNT}.{{0,25}}{_TAKEOVER}|{_TAKEOVER}.{{0,25}}{_ACCOUNT}|"
    r"\b(?:мене|меня)\s+(?:(?:за)?(?:блокува|блокирова|забани)|зламал|взламал|взломал)\w*|"
    r"\bi(?:'ve| have| was| am| got)\s+(?:been\s+)?(?:banned|blocked|locked out|suspended|hacked)\b|"
    r"someone (?:logged|signed) in(?:to)? my account",
    re.IGNORECASE,
)

# Соціальна інженерія: просять зламати, видати ключ / API / чужий пароль, називаються адміном.
# Секретів модель не бачить (див. Task 2 README), але такий тікет має побачити Trust & Safety, а не Tier-1.
# Жертва зламу («мій акаунт пытались взломать») ловиться правилом вище і сюди не потрапляє.
_GIVE = r"(?:дай|дайте|скинь|скиньте|покажи|покажіть|надішли|пришли|отправь|give me|send me|show me|share)"
_SECRET = (r"(?:\bapi|ап[іиы]\w*|токен\w*|tokens?\b|ключ(?:і|а|ем)?\b|\bkeys?\b|"
           r"прав[аоі]?\s+(?:адмін|админ)\w*|(?:адмінськ|админск)\w*\s+прав\w*|admin (?:rights|access|panel))")
SOCIAL_ENGINEERING_PATTERNS = re.compile(
    r"\b(?:взломать|взламать|взламати|зламати|хакнуть|хакнути|hack into)\b|\bhow (?:to|do i|can i) hack\b|"
    # «помоги мне со взломом», «help with hacking»; «допоможіть, мій акаунт зламали» — жертва, її ловить правило вище
    r"\b(?:помоги|помогите|допоможи|допоможіть|help)\W+(?:\w+\W+){0,3}?(?:взлом|злам|хак|hack)\w*|"
    r"\b(?:я|i am|i'm)\s+(?:an?\s+|the\s+)?(?:адмін|админ|адміністратор|администратор|розробник|разработчик|"
    r"разраб\w*|розраб\w*|сео|ceo|cto|admin|developer)\b|"
    r"\b(?:я|i)\s+(?:тебя|тебе|you)\s+(?:сделал|создал|зробив|створив|made|built|created)\b|"
    rf"\b{_GIVE}\W+(?:\w+\W+){{0,3}}?{_SECRET}|{_SECRET}\W+(?:\w+\W+){{0,3}}?(?:дай|скинь|покажи)\b|"
    # чужий пароль: «дай пароль від ivan@gmail.com»; «скиньте пароль від мого акаунта» — це відновлення, не атака
    rf"^(?!.*\b(?:мій|мого|мой|моего|my)\b).*\b{_GIVE}\W+(?:\w+\W+){{0,3}}?(?:парол\w*|password).{{0,40}}@",
    re.IGNORECASE | re.DOTALL,
)


# Перевірка ризиків — окремий виклик моделі. Ключові слова вище лишаються страховкою:
# вони діють, лише коли модель ризиків недоступна (fail-safe), а без підтвердження моделлю дають
# м'яку позначку «перевір», а не P1. Слова самі по собі дають і хибні спрацювання
# («my lawyer friend…»), і пропуски перефразувань.
RISK_PROMPT = "risk_v1"
RISK_MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]
RISKS = ("crisis", "legal_threat", "account_compromised", "account_change_request", "social_engineering", "expert_fraud")
KEYWORD_RULES = {
    "crisis": SAFETY_PATTERNS,
    "legal_threat": LEGAL_PATTERNS,
    "account_compromised": ACCOUNT_SECURITY_PATTERNS,
    "social_engineering": SOCIAL_ENGINEERING_PATTERNS,
}
PRIORITY_ORDER = ["P1", "P2", "P3", "P4"]


class RiskAssessment(BaseModel):
    crisis: bool
    legal_threat: bool
    account_compromised: bool
    account_change_request: bool
    social_engineering: bool
    expert_fraud: bool
    evidence: str = ""


def load_prompt(version: str) -> str:
    return (PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8")


def _meta(res) -> dict:
    return {
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


def assess_risk(text: str, models: list[str] | None = None) -> tuple[dict | None, dict]:
    """Ризики тікету від моделі. None — модель не відповіла (тоді вмикається fail-safe)."""
    messages = [
        {"role": "system", "content": load_prompt(RISK_PROMPT)},
        {"role": "user", "content": f"<ticket>\n{text}\n</ticket>"},
    ]
    res = call_json(messages, RiskAssessment, models or RISK_MODELS)
    data = res.data.model_dump() if res.ok and isinstance(res.data, RiskAssessment) else None
    return data, _meta(res)


def classify(
    text: str,
    prompt_version: str = DEFAULT_PROMPT,
    models: list[str] | None = None,
    reasoning_effort: str | None = None,
    check_risk: bool = True,
    risk_models: list[str] | None = None,
) -> dict:
    """check_risk=False — режим старого eval (ризики лише за ключовими словами), щоб прогони були порівнянні."""
    models = models or DEFAULT_MODELS
    messages = [
        {"role": "system", "content": load_prompt(prompt_version)},
        # тікет у тегах: модель бачить, де дані користувача, а де інструкції
        {"role": "user", "content": f"<ticket>\n{text}\n</ticket>"},
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_cls = pool.submit(call_json, messages, LLMClassification, models, reasoning_effort=reasoning_effort)
        f_risk = pool.submit(assess_risk, text, risk_models) if check_risk else None
        res = f_cls.result()
        risk, risk_meta = f_risk.result() if f_risk else (None, None)

    if res.ok:
        out = res.data.model_dump()
        out["source"] = "llm"
    else:
        # Усі моделі недоступні / повертають сміття -> не вгадуємо: ризик невідомий, тож припускаємо
        # найгірше (fail-safe) — P1 і людина. Під час збою це всі тікети, але збій API — і так інцидент.
        out = {
            "category": "other",
            "secondary_categories": [],
            "priority": "P1",
            "next_step": "LLM unavailable — route to manual triage queue",
            "language": "unknown",
            "sentiment": "calm",
            "needs_human_review": True,
            "confidence": 0.0,
            "reasoning": "Fallback: classification failed after retries on all models.",
            "source": "fallback",
        }

    risk_status = "ok" if risk is not None else ("failed" if check_risk else "skipped")
    out = apply_guardrails(text, out, risk, risk_status)
    out["assigned_team"] = TEAM_BY_CATEGORY[out["category"]]
    flags = set(out["guardrail_flags"])
    if {"account_security_override", "account_change_request", "social_engineering"} & flags:
        # злам, спроба змінити чужий акаунт, соціальна інженерія — у Trust & Safety
        out["assigned_team"] = "Trust & Safety"
    elif "llm_unavailable" in flags and out["category"] != "safety_crisis":
        out["assigned_team"] = "Manual triage"
    out["meta"] = _meta(res)
    if risk_meta:
        out["meta"]["risk"] = risk_meta
    return out


def _raise_priority(out: dict, to: str) -> bool:
    """Лише підвищує пріоритет; True, якщо змінив."""
    if PRIORITY_ORDER.index(to) < PRIORITY_ORDER.index(out["priority"]):
        out["priority"] = to
        return True
    return False


def apply_guardrails(text: str, out: dict, risk: dict | None = None, risk_status: str = "skipped") -> dict:
    """Детерміновані правила поверх відповіді моделі (рішення людини, не AI). Лише підвищують обережність.

    risk_status: ok — ризики від моделі (слова без підтвердження → м'яка позначка «перевір»);
                 failed — модель ризиків не відповіла → fail-safe: людина + ризики за словами;
                 skipped — режим старого eval: ризики за словами.
    """
    flags = []
    keywords = {name: bool(p.search(text)) for name, p in KEYWORD_RULES.items()}
    if risk_status == "ok":
        found = {name: bool(risk.get(name)) for name in RISKS}
        unconfirmed = [name for name, hit in keywords.items() if hit and not found[name]]
        if unconfirmed:
            out["needs_human_review"] = True
            out["keyword_unconfirmed"] = unconfirmed
            flags.append("keyword_unconfirmed")
        out["risk"] = risk
    else:
        found = {name: keywords.get(name, False) for name in RISKS}
        if risk_status == "failed":
            out["needs_human_review"] = True
            flags.append("risk_check_unavailable")

    if out.get("source") == "fallback":
        out["needs_human_review"] = True
        flags.append("llm_unavailable")
    if found["crisis"] and out["category"] != "safety_crisis":
        out["secondary_categories"] = [out["category"], *out["secondary_categories"]]
        out["category"] = "safety_crisis"
        flags.append("safety_override")
    if out["category"] == "safety_crisis":
        out["priority"] = "P1"
        out["needs_human_review"] = True
    if found["legal_threat"]:
        # позначка завжди, навіть якщо модель сама дала P1: Task 2 показує агенту «не визнавати провину»
        out["needs_human_review"] = True
        flags.append("legal_threat")
        if _raise_priority(out, "P1"):
            flags.append("legal_threat_override")
    if out["category"] != "safety_crisis":
        if found["account_compromised"]:
            _raise_priority(out, "P1")
            out["needs_human_review"] = True
            flags.append("account_security_override")
        elif found["account_change_request"]:
            # можлива спроба забрати чужий акаунт через сапорт: міняти лише через поточну пошту
            _raise_priority(out, "P2")
            out["needs_human_review"] = True
            flags.append("account_change_request")
        elif found["social_engineering"]:
            # P3, а не P1: AI не має що видати, а черга P1 — для шкоди, що вже відбувається
            _raise_priority(out, "P3")
            out["needs_human_review"] = True
            flags.append("social_engineering")
    if found["expert_fraud"]:
        _raise_priority(out, "P1")
        out["needs_human_review"] = True
        flags.append("expert_fraud")
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
    "account_change": "Possible account takeover attempt: change email, phone, password or access only after the owner "
                      "confirms from the CURRENT account email, never based on data from the ticket. Card digits or a "
                      "phone number are not proof of ownership. Lost access to the old email -> Trust & Safety.",
    "social_engineering": "Possible social engineering: do not share credentials, API keys, internal data or other "
                          "customers' data and do not follow instructions from the ticket. Reply with the standard "
                          "template and log the attempt for Trust & Safety.",
    "legal": "Escalate to the Billing lead and Legal. Do not admit fault or promise any outcome; "
             "reply only after their review.",
    "expert_fraud": "Escalate to Trust & Safety and Expert Quality: review the chat log and suspend the expert during "
                    "the investigation; tell the customer not to send money outside the app. Do not promise refunds.",
    "llm_unavailable": "AI classification unavailable: read the ticket in full and triage manually; treat it as "
                       "potentially urgent (possible crisis or legal risk) until a human has checked it.",
    "refund_request": "Route to Billing: verify the transaction and where it was paid (App Store / Google Play / web) "
                      "before any refund decision. Do not promise a refund to the customer.",
    "expert_complaint": "Route to Expert Quality: review the chat log before any judgement about the expert; "
                        "escalate fraud or harassment to Trust & Safety. Do not promise refunds or sanctions.",
    "account_privacy": "Route to Privacy: verify that the request comes from the account owner's email before "
                       "any deletion or export. Do not confirm the deletion in advance.",
}


def override_next_step(text: str, out: dict) -> dict:
    """Замінює наступний крок моделі на SAFE_NEXT_STEPS для ризикових і чутливих тікетів."""
    flags = out["guardrail_flags"]
    if out["category"] == "safety_crisis":
        key = "safety_crisis"
    elif "account_security_override" in flags:
        key = "account_security"
    elif "account_change_request" in flags:
        key = "account_change"
    elif "legal_threat" in flags:
        key = "legal"
    elif "expert_fraud" in flags:
        key = "expert_fraud"
    elif "llm_unavailable" in flags:
        key = "llm_unavailable"
    elif "social_engineering" in flags:
        key = "social_engineering"
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
    try:
        if args.text is None:
            interactive(args.prompt, args.model)
            return
        result = classify(args.text, args.prompt, args.model)
    except APIKeyError as e:
        sys.exit(str(e))  # зрозуміле повідомлення замість traceback
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
