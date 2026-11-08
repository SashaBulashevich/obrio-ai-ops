"""Виправлення агента → нові розмічені тікети для evaluation (human-in-the-loop).

Модель сама нічого не «довчає»: виправлення лише збираються, людина їх переглядає і переносить
у тестові набори, а нова версія промпту виходить, тільки якщо evaluation на всіх наборах не гірша.
Автоматичне навчання на тікетах тут небезпечне: розмітки «як правильно» без людини немає, а будь-хто
може писати тікети так, щоб модель перестала помічати кризу чи шахрайство.

Записи — JSON Lines у feedback/corrections.jsonl (не в git: тікети можуть містити персональні дані).
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "task1_classifier"))

from classifier import DEFAULT_PROMPT, RISK_PROMPT, RISKS  # noqa: E402

FEEDBACK_PATH = HERE / "feedback" / "corrections.jsonl"
LEVELS = ("blocked", "review", "ok")


def ai_risks(classification: dict) -> list[str]:
    risk = classification.get("risk") or {}
    return sorted(name for name in RISKS if risk.get(name) is True)


def make_record(result: dict, category: str, priority: str, level: str, risks: list[str],
                comment: str = "", generate_prompt: str = "") -> dict:
    """Що сказав AI і що правильно на думку агента; changed — лише поля, які агент змінив."""
    cls = result["classification"]
    ai = {"category": cls["category"], "priority": cls["priority"],
          "level": result["human_judgment"]["level"], "risks": ai_risks(cls)}
    agent = {"category": category, "priority": priority, "level": level, "risks": sorted(risks)}
    if level not in LEVELS:
        raise ValueError(f"level має бути одним з {LEVELS}")
    unknown = set(risks) - set(RISKS)
    if unknown:
        raise ValueError(f"невідомі ризики: {sorted(unknown)}")
    return {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ticket": result["ticket"],
        "ai": ai,
        "agent": agent,
        "changed": sorted(k for k in ai if ai[k] != agent[k]),
        "comment": comment.strip(),
        # версії, на яких AI помилився: щоб потім перевірити, чи нова версія це виправила
        "versions": {"classify_prompt": DEFAULT_PROMPT, "risk_prompt": RISK_PROMPT, "generate_prompt": generate_prompt},
    }


def save(record: dict, path: Path | None = None) -> Path:
    path = path or FEEDBACK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def load(path: Path | None = None) -> list[dict]:
    path = path or FEEDBACK_PATH
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def to_eval_candidates(records: list[dict], first_id: int = 1000) -> dict[str, list[dict]]:
    """Кандидати в набори Task 1 у їхньому форматі. Не дописуються в набори автоматично —
    людина переглядає розмітку агента (агент теж помиляється) і переносить вручну."""
    latest = {}
    for r in records:  # той самий тікет виправили кілька разів — беремо останнє виправлення
        latest[r["ticket"]] = r
    test_set, risk_set = [], []
    for i, r in enumerate(latest.values(), start=first_id):
        tags = ["from_feedback", *[f"fixed_{k}" for k in r["changed"]]]
        if {"category", "priority"} & set(r["changed"]):
            test_set.append({"id": i, "tags": tags, "text": r["ticket"],
                             "expected_category": r["agent"]["category"],
                             "expected_priority": r["agent"]["priority"]})
        if "risks" in r["changed"]:
            risk_set.append({"id": i, "tags": tags, "text": r["ticket"], "expected": r["agent"]["risks"]})
    return {"test_set": test_set, "risk_set": risk_set}
