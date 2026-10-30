"""Evaluation детекції ризиків: ключові слова (старий підхід) проти окремої перевірки моделлю (risk_v1).

Набір data/risk_set.json: перефразовані кризи й погрози, захоплення акаунта, соціальна інженерія,
шахрайство експерта, а також «пастки» — звичайні тікети зі словами-тригерами («my lawyer friend…»).

    python task1_classifier/evaluate_risk.py                              # gpt-oss-20b (основна модель ризиків)
    python task1_classifier/evaluate_risk.py --model openai/gpt-oss-120b
    python task1_classifier/evaluate_risk.py --dataset risk_holdout       # відкладений набір (писав окремий агент)
    python task1_classifier/evaluate_risk.py --rescore                    # перебудувати звіт без API

Результат: results/risk_v1/<model>.md і .json.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from classifier import KEYWORD_RULES, RISKS, assess_risk  # noqa: E402

OUT_DIR = HERE / "results" / "risk_v1"


def score(expected: set[str], found: set[str]) -> dict:
    return {"missed": sorted(expected - found), "false_alarm": sorted(found - expected), "ok": expected == found}


def run(model: str, tickets: list[dict]) -> list[dict]:
    rows = []
    for t in tickets:
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


def write_report(model: str, rows: list[dict], dataset: str = "risk_set") -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    kw, llm = metrics(rows, "kw"), metrics(rows, "llm")
    cost = sum(r["cost_usd"] for r in rows) / len(rows)

    def pct(a, b):
        return f"{a}/{b} ({a / b:.0%})" if b else "—"

    lines = [
        f"# Детекція ризиків ({dataset}) — ключові слова проти {model} (risk_v1)", "",
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
    name = model.replace("/", "__") + ("" if dataset == "risk_set" else f"_{dataset}")
    (OUT_DIR / f"{name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT_DIR / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСлова: ризики {pct(kw['risky_caught'], kw['risky_n'])}, хибні тривоги {pct(kw['false_alarms'], kw['clean_n'])}")
    print(f"Модель: ризики {pct(llm['risky_caught'], llm['risky_n'])}, хибні тривоги {pct(llm['false_alarms'], llm['clean_n'])}")
    print(f"Звіт: {OUT_DIR}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--dataset", default="risk_set",
                        help="risk_set або risk_holdout (писав окремий агент, без доступу до промпту)")
    parser.add_argument("--rescore", action="store_true", help="перебудувати звіт зі збереженого JSON, без API")
    args = parser.parse_args()
    if args.rescore:
        name = args.model.replace("/", "__") + ("" if args.dataset == "risk_set" else f"_{args.dataset}")
        rows = json.loads((OUT_DIR / f"{name}.json").read_text(encoding="utf-8"))
    else:
        tickets = json.loads((HERE / "data" / f"{args.dataset}.json").read_text(encoding="utf-8"))
        rows = run(args.model, tickets)
    write_report(args.model, rows, args.dataset)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
