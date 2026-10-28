"""Детерміноване порівняння generate_v1 і generate_v2 — без LLM-судді і без API.

Навіщо: LLM-суддя ставив v1 і v2 майже однакову оцінку, але не помічав саме тих проблем,
які виправляє v2 (фамільярний тон, англійські назви команд, неявні обіцянки refund).
Тут ці проблеми рахуються кодом на тих самих тікетах, де обидві версії згенерували відповідь.

Запуск з кореня репозиторію:  python -m task2_agent_assistant.compare_prompts
Результат: results/prompt_comparison.md
"""

import json
import re
import sys
from pathlib import Path

from task2_agent_assistant import assistant

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]

CASUAL = re.compile(r"^\s*(hi there|hi|hey|привіт|привет|hola)\b|super frustrating", re.I | re.M)
TEAM_EN = re.compile(r"Billing|Expert Quality|Trust ?& ?Safety|Legal team|Tech Support")
CYRILLIC = re.compile(r"[а-яіїєґ]", re.I)
# ширший за REFUND_PROMISE в assistant.py: ловить і умовні/неявні формулювання, тому лише для аналізу
SOFT_PROMISE = re.compile(
    r"get a refund processed|поверне зайві|вернем|refund (?:will be|is) returned|"
    r"will (?:issue|process) (?:a |the )?(?:full )?refund(?! according)", re.I)

METRICS = [
    ("casual", "Фамільярний тон («Hi there», «super frustrating»)"),
    ("team_en", "Англійські назви команд у кириличній відповіді"),
    ("lang_mismatch", "Відповідь не мовою клієнта (langdetect)"),
    ("regex_promise", "Явна обіцянка refund (regex у продукті)"),
    ("soft_promise", "Неявна / впевнена обіцянка refund (ширший regex)"),
    ("invalid_cit", "Недослівна цитата"),
    ("short_over_45", "Короткий варіант > 45 слів (ліміт v2)"),
    ("emoji", "Emoji"),
]


def load(prompt: str, model: str) -> dict:
    path = RESULTS / prompt / f"{model.replace('/', '__')}.json"
    return {r["id"]: r for r in json.loads(path.read_text(encoding="utf-8")) if r["generated"]}


def count(rows: dict, ids: list[int]) -> dict:
    """Кількість тікетів (а не відповідей), де проблема є хоча б в одному з трьох тонів."""
    c = {k: 0 for k, _ in METRICS}
    for i in ids:
        rep, ch = rows[i]["replies"], rows[i]["checks"]
        c["casual"] += any(CASUAL.search(t) for t in rep.values())
        c["team_en"] += any(CYRILLIC.search(t) and TEAM_EN.search(t) for t in rep.values())
        c["lang_mismatch"] += bool(ch["language_mismatch"])
        c["regex_promise"] += bool(ch["refund_promise"])
        c["soft_promise"] += any(SOFT_PROMISE.search(t) for t in rep.values())
        c["invalid_cit"] += bool(ch["invalid_citations"])
        c["short_over_45"] += len(rep["short"].split()) > 45
        c["emoji"] += bool(assistant.EMOJI.search(" ".join(rep.values())))
    return c


def main():
    lines = ["# generate_v1 vs generate_v2 — детерміновані перевірки", "",
             "Рахуємо **кількість тікетів**, де проблема є хоча б в одному з трьох тонів. "
             "Лише тікети, де **обидві** версії згенерували відповідь (решта — rate limits безкоштовного тарифу).", ""]
    for model in MODELS:
        v1, v2 = load("generate_v1", model), load("generate_v2", model)
        ids = sorted(set(v1) & set(v2))
        c1, c2 = count(v1, ids), count(v2, ids)
        lines += [f"## {model} — {len(ids)} спільних тікетів ({', '.join(f'#{i}' for i in ids)})", "",
                  "| Проблема | v1 | v2 |", "|---|---|---|"]
        lines += [f"| {label} | {c1[k]} | {c2[k]} |" for k, label in METRICS]
        if 18 in ids:
            lines.append(f"| `kb_coverage` для #18 (питання поза базою знань) | — (поля немає) | {v2[18]['checks'].get('kb_coverage')} |")
        lines.append("")
    out = RESULTS / "prompt_comparison.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nЗвіт: {out}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
