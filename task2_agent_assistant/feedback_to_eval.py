"""Зведення виправлень агентів і кандидати в тестові набори.

    python task2_agent_assistant/feedback_to_eval.py                 # зведення + кандидати
    python task2_agent_assistant/feedback_to_eval.py --path файл.jsonl

Пише feedback/candidates_test_set.json і candidates_risk_set.json у форматі наборів Task 1.
У самі набори вони НЕ дописуються: людина перевіряє розмітку і переносить вручну,
потім — новий промпт і evaluation на всіх наборах (див. README, розділ 14).
"""

import argparse
import json
from collections import Counter
from pathlib import Path

import feedback


def summary(records: list[dict]) -> str:
    if not records:
        return "Виправлень немає."
    changed = Counter(k for r in records for k in r["changed"])
    missed = Counter(x for r in records for x in set(r["agent"]["risks"]) - set(r["ai"]["risks"]))
    extra = Counter(x for r in records for x in set(r["ai"]["risks"]) - set(r["agent"]["risks"]))
    softer = sum(1 for r in records if feedback.LEVELS.index(r["agent"]["level"]) < feedback.LEVELS.index(r["ai"]["level"]))
    lines = [f"Виправлень: {len(records)} (тікетів: {len({r['ticket'] for r in records})})",
             "Що змінювали: " + (", ".join(f"{k} — {n}" for k, n in changed.most_common()) or "нічого"),
             f"AI був м'якшим, ніж треба (рівень втручання): {softer} — переглянути першими"]
    if missed:
        lines.append("Пропущені ризики: " + ", ".join(f"{k} — {n}" for k, n in missed.most_common()))
    if extra:
        lines.append("Зайві ризики: " + ", ".join(f"{k} — {n}" for k, n in extra.most_common()))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, default=feedback.FEEDBACK_PATH)
    args = parser.parse_args()
    records = feedback.load(args.path)
    print(summary(records))
    if not records:
        return
    out_dir = args.path.parent
    for name, items in feedback.to_eval_candidates(records).items():
        path = out_dir / f"candidates_{name}.json"
        path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{path}: {len(items)} кандидатів")


if __name__ == "__main__":
    main()
