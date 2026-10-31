"""Evaluation детекції ризиків: ключові слова (старий підхід) проти окремої перевірки моделлю (RISK_PROMPT).

Набір data/risk_set.json: перефразовані кризи й погрози, захоплення акаунта, соціальна інженерія,
шахрайство експерта, а також «пастки» — звичайні тікети зі словами-тригерами («my lawyer friend…»).

    python task1_classifier/evaluate_risk.py                              # gpt-oss-20b (основна модель ризиків)
    python task1_classifier/evaluate_risk.py --model openai/gpt-oss-120b
    python task1_classifier/evaluate_risk.py --dataset risk_holdout       # відкладений набір (писав окремий агент)
    python task1_classifier/evaluate_risk.py --rescore                    # перебудувати звіт без API
    python task1_classifier/evaluate_risk.py --run 2                      # повторний прогін (стабільність моделі)
    python task1_classifier/evaluate_risk.py --run 2 --resume             # дозапитати лише тікети без відповіді
    python task1_classifier/evaluate_risk.py --summary                    # зведення всіх прогонів, без API

Результат: results/<RISK_PROMPT>/<model>[_<dataset>][_run<N>].md і .json; зведення — summary.md.
Денний ліміт Groq (200k токенів на модель) менший за обидва набори разом, тож прогін, який упав на ліміті,
продовжується наступного дня через --resume: тікети з відповіддю вдруге не оплачуються.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from classifier import KEYWORD_RULES, RISK_PROMPT, RISKS, assess_risk  # noqa: E402
from common import llm_client  # noqa: E402

llm_client.CALL_BUDGET_S = None  # batch: ліміт часу на виклик лише для інтерактивного режиму

OUT_DIR = HERE / "results" / RISK_PROMPT


def score(expected: set[str], found: set[str]) -> dict:
    return {"missed": sorted(expected - found), "false_alarm": sorted(found - expected), "ok": expected == found}


def run(model: str, tickets: list[dict], done: dict[int, dict] | None = None) -> list[dict]:
    """done — рядки попереднього запуску того самого прогону: тікети з відповіддю і тим самим текстом не повторюємо."""
    rows = []
    for t in tickets:
        prev = (done or {}).get(t["id"])
        if prev and prev["model_found"] is not None and prev["text"] == t["text"] and prev["expected"] == t["expected"]:
            rows.append(prev)
            continue
        expected = set(t["expected"])
        keywords = {name for name, p in KEYWORD_RULES.items() if p.search(t["text"])}
        risk, meta = assess_risk(t["text"], [model])
        model_found = {name for name in RISKS if risk and risk[name]} if risk else None
        row = {**t, "keywords": sorted(keywords), "kw": score(expected, keywords),
               "model_found": sorted(model_found) if model_found is not None else None,
               "evidence": (risk or {}).get("evidence", ""), "cost_usd": meta["cost_usd"],
               "latency_s": meta["api_latency_s"], "errors": meta["errors"]}
        row["llm"] = score(expected, model_found) if model_found is not None else None
        rows.append(row)
        mark = "—" if row["llm"] is None else ("✅" if row["llm"]["ok"] else "❌")
        print(f"  #{t['id']:>2} model {mark} {row['model_found']}  | слова {'✅' if row['kw']['ok'] else '❌'} {row['keywords']}")
    return rows


def metrics(rows: list[dict], key: str) -> dict:
    scored = [r for r in rows if r[key] is not None]
    risky = [r for r in scored if r["expected"]]
    clean = [r for r in scored if not r["expected"]]
    return {
        "n": len(scored),
        "exact": sum(r[key]["ok"] for r in scored),
        "risky_n": len(risky),
        "risky_caught": sum(not r[key]["missed"] for r in risky),  # знайдено всі очікувані ризики
        "clean_n": len(clean),
        "false_alarms": sum(bool(r[key]["false_alarm"]) for r in clean),  # зайвий ризик на «чистому» тікеті
    }


def file_name(model: str, dataset: str, run_no: int) -> str:
    # перший прогін — без суфікса, щоб старі результати лишились на своїх місцях
    return (model.replace("/", "__") + ("" if dataset == "risk_set" else f"_{dataset}")
            + ("" if run_no == 1 else f"_run{run_no}"))


def write_report(model: str, rows: list[dict], dataset: str = "risk_set", run_no: int = 1) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    kw, llm = metrics(rows, "kw"), metrics(rows, "llm")
    cost = sum(r["cost_usd"] for r in rows) / len(rows)

    def pct(a, b):
        return f"{a}/{b} ({a / b:.0%})" if b else "—"

    lines = [
        f"# Детекція ризиків ({dataset}) — ключові слова проти {model} ({RISK_PROMPT}, прогін {run_no})", "",
        "| Метрика | Ключові слова | Модель |", "|---|---|---|",
        f"| Тікети з ризиком: знайдено всі ризики | {pct(kw['risky_caught'], kw['risky_n'])} | {pct(llm['risky_caught'], llm['risky_n'])} |",
        f"| «Чисті» тікети і пастки: хибна тривога | {pct(kw['false_alarms'], kw['clean_n'])} | {pct(llm['false_alarms'], llm['clean_n'])} |",
        f"| Повний збіг з розміткою | {pct(kw['exact'], kw['n'])} | {pct(llm['exact'], llm['n'])} |",
        "", f"Вартість перевірки моделлю: **${cost:.6f}** за тікет (≈ ${cost * 10000:.2f} на 10 000 тікетів).", "",
        "| # | Теги | Тікет | Очікувано | Слова | Модель | Цитата моделі |", "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        text = r["text"].replace("|", "/")
        text = text if len(text) <= 70 else text[:69] + "…"
        kw_cell = ("✅ " if r["kw"]["ok"] else "❌ ") + (", ".join(r["keywords"]) or "—")
        if r["llm"] is None:
            llm_cell = "— (модель не відповіла)"
        else:
            llm_cell = ("✅ " if r["llm"]["ok"] else "❌ ") + (", ".join(r["model_found"]) or "—")
        ev = (r["evidence"] or "").replace("|", "/")[:60]
        lines.append(f"| {r['id']} | {', '.join(r['tags'])} | {text} | {', '.join(r['expected']) or '—'} | {kw_cell} | {llm_cell} | {ev} |")
    unanswered = sum(r["llm"] is None for r in rows)
    if unanswered:
        lines.insert(2, f"**Неповний прогін:** {unanswered} з {len(rows)} тікетів без відповіді моделі "
                        f"(ліміт Groq?) — дозапитати: `--run {run_no} --resume`.\n")
    name = file_name(model, dataset, run_no)
    (OUT_DIR / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT_DIR / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСлова: ризики {pct(kw['risky_caught'], kw['risky_n'])}, хибні тривоги {pct(kw['false_alarms'], kw['clean_n'])}")
    print(f"Модель: ризики {pct(llm['risky_caught'], llm['risky_n'])}, хибні тривоги {pct(llm['false_alarms'], llm['clean_n'])}")
    if unanswered:
        print(f"Без відповіді: {unanswered} — дозапитати з --resume")
    print(f"Звіт: {OUT_DIR}")


def summary() -> None:
    """Зведення всіх прогонів поточного промпту: метрики кожного прогону і тікети, де прогони розходяться."""
    groups = defaultdict(dict)  # (модель, набір) -> {прогін: рядки}
    for f in sorted(OUT_DIR.glob("*.json")):
        stem = f.stem
        run_no = int(stem.rsplit("_run", 1)[1]) if "_run" in stem else 1
        stem = stem.rsplit("_run", 1)[0]
        dataset = "risk_holdout" if stem.endswith("_risk_holdout") else "risk_set"
        model = stem.removesuffix("_risk_holdout").replace("__", "/")
        groups[(model, dataset)][run_no] = json.loads(f.read_text(encoding="utf-8"))

    lines = [f"# Зведення прогонів ({RISK_PROMPT})", "",
             "Кожен прогін — окремий запуск моделі на всьому наборі. Повних прогонів = без жодного тікета без відповіді.", "",
             "| Модель | Набір | Прогонів | Знайдено всі ризики (кожен прогін) | Хибні тривоги (кожен прогін) | Без відповіді |",
             "|---|---|---|---|---|---|"]
    unstable = []
    for (model, dataset), runs in sorted(groups.items()):
        caught, alarms, missing = [], [], []
        for run_no in sorted(runs):
            m = metrics(runs[run_no], "llm")
            caught.append(f"{m['risky_caught']}/{m['risky_n']}")
            alarms.append(f"{m['false_alarms']}/{m['clean_n']}")
            missing.append(str(sum(r["llm"] is None for r in runs[run_no])))
        lines.append(f"| {model} | {dataset} | {len(runs)} | {' · '.join(caught)} | {' · '.join(alarms)} | {' · '.join(missing)} |")
        if len(runs) > 1:
            by_id = defaultdict(set)
            for rows in runs.values():
                for r in rows:
                    if r["model_found"] is not None:
                        by_id[r["id"]].add(tuple(r["model_found"]))
            for tid, answers in sorted(by_id.items()):
                if len(answers) > 1:
                    unstable.append(f"| {model} | {dataset} | {tid} | {' / '.join(', '.join(a) or '—' for a in sorted(answers))} |")
    lines += ["", "## Тікети, на яких прогони розходяться", ""]
    lines += (["| Модель | Набір | # | Різні відповіді |", "|---|---|---|---|"] + unstable) if unstable else ["Немає (або лише по одному прогону)."]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--dataset", default="risk_set",
                        help="risk_set або risk_holdout (писав окремий агент, без доступу до промпту)")
    parser.add_argument("--rescore", action="store_true", help="перебудувати звіт зі збереженого JSON, без API")
    parser.add_argument("--run", type=int, default=1, help="номер прогону: 2, 3… — повтор для перевірки стабільності")
    parser.add_argument("--resume", action="store_true", help="дозапитати лише тікети без відповіді або змінені")
    parser.add_argument("--summary", action="store_true", help="зведення всіх прогонів (summary.md), без API")
    args = parser.parse_args()
    if args.summary:
        summary()
        return
    path = OUT_DIR / f"{file_name(args.model, args.dataset, args.run)}.json"
    if args.rescore:
        rows = json.loads(path.read_text(encoding="utf-8"))
    else:
        tickets = json.loads((HERE / "data" / f"{args.dataset}.json").read_text(encoding="utf-8"))
        done = ({r["id"]: r for r in json.loads(path.read_text(encoding="utf-8"))}
                if args.resume and path.exists() else None)
        rows = run(args.model, tickets, done)
    write_report(args.model, rows, args.dataset, args.run)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
