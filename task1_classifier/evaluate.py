"""Evaluation: проганяє зафіксований тестовий набір через одну або кілька моделей.

Використання:
    python task1_classifier/evaluate.py --prompt v1
    python task1_classifier/evaluate.py --prompt v2 --model openai/gpt-oss-20b
    python task1_classifier/evaluate.py --prompt v3 --rescore    # перерахувати метрики зі збережених JSON, без API

Результати пишуться в task1_classifier/results/<prompt>/:
    <model>.md     — таблиця input -> expected -> actual -> pass/fail
    <model>.json   — сирі відповіді (для розбору помилок)
    summary.md     — порівняння моделей: якість, швидкість, вартість
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from classifier import classify, next_step_problem, override_next_step  # noqa: E402
from common.llm_client import APIKeyError  # noqa: E402

ALL_MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
TICKETS_PER_MONTH = 10_000


def slug(model: str) -> str:
    return model.replace("/", "__")


def short(text: str, n: int = 70) -> str:
    text = text.replace("|", "/").replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def run_model(model: str, prompt: str, tickets: list[dict], reasoning_effort: str | None = None,
              check_risk: bool = False) -> list[dict]:
    rows = []
    for t in tickets:
        # без fallback: міряємо саму модель, а не ланцюжок
        # check_risk=False — як у збережених прогонах (ризики за словами); --risk — нова логіка з моделлю ризиків
        out = classify(t["text"], prompt_version=prompt, models=[model], reasoning_effort=reasoning_effort,
                       check_risk=check_risk)
        rows.append({
            "id": t["id"],
            "tags": t["tags"],
            "text": t["text"],
            "expected_category": t["expected_category"],
            "expected_priority": t["expected_priority"],
            "actual_category": out["category"],
            "actual_priority": out["priority"],
            "cat_ok": out["category"] == t["expected_category"],
            "pri_ok": out["priority"] == t["expected_priority"],
            "output": out,
        })
        score_next_step(rows[-1])
        mark = "✅" if rows[-1]["cat_ok"] and rows[-1]["pri_ok"] else "❌"
        print(f"  #{t['id']:>2} {mark} {out['category']}/{out['priority']}  ({out['meta']['latency_s']}s)")
    return rows


def score_next_step(row: dict) -> dict:
    """Наступний крок: що запропонувала модель і що отримує агент після правил у коді."""
    out = row["output"]
    if "next_step_model" not in out:  # результат, збережений до появи override — застосовуємо правила зараз
        out = override_next_step(row["text"], {**out, "guardrail_flags": list(out.get("guardrail_flags", []))})
    row["next_problem_model"] = next_step_problem(out.get("next_step_model", out["next_step"]))
    row["next_problem"] = next_step_problem(out["next_step"])
    row["next_step_final"] = out["next_step"]
    return row


def p95(values: list[float]) -> float:
    return sorted(values)[max(0, int(round(0.95 * len(values))) - 1)]


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    lat = [r["output"]["meta"]["latency_s"] for r in rows]
    api_lat = [r["output"]["meta"].get("api_latency_s", 0) for r in rows]
    cost = [r["output"]["meta"]["cost_usd"] + r["output"]["meta"].get("risk", {}).get("cost_usd", 0) for r in rows]
    return {
        "n": n,
        "cat_acc": sum(r["cat_ok"] for r in rows) / n,
        "pri_acc": sum(r["pri_ok"] for r in rows) / n,
        "both_acc": sum(r["cat_ok"] and r["pri_ok"] for r in rows) / n,
        "next_ok_model": sum(not r["next_problem_model"] for r in rows) / n,
        "next_ok": sum(not r["next_problem"] for r in rows) / n,
        "lat_avg": statistics.mean(lat),
        "lat_p95": p95(lat),
        "api_lat_avg": statistics.mean(api_lat),
        "api_lat_p95": p95(api_lat),
        "cost_avg": statistics.mean(cost),
        "prompt_tokens_avg": statistics.mean(r["output"]["meta"]["prompt_tokens"] for r in rows),
        "completion_tokens_avg": statistics.mean(r["output"]["meta"]["completion_tokens"] for r in rows),
        "fallbacks": sum(r["output"]["source"] == "fallback" for r in rows),
        "retries": sum(r["output"]["meta"]["attempts"] - 1 for r in rows),
    }


def write_model_report(path: Path, model: str, prompt: str, rows: list[dict], s: dict):
    lines = [
        f"# {model} — prompt {prompt}",
        "",
        f"Category accuracy: **{s['cat_acc']:.0%}** · Priority accuracy: **{s['pri_acc']:.0%}** · "
        f"Both: **{s['both_acc']:.0%}** ({s['n']} тікетів)",
        "",
        f"Next step для агента: модель **{s['next_ok_model']:.0%}** → після правил у коді **{s['next_ok']:.0%}** "
        "(❌ = звернений до клієнта або обіцяє refund / видалення)",
        "",
        "| # | Теги | Input | Expected | Actual | Category | Priority | Next step (модель → фінал) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {', '.join(r['tags'])} | {short(r['text'])} | "
            f"{r['expected_category']} / {r['expected_priority']} | "
            f"{r['actual_category']} / {r['actual_priority']} | "
            f"{'✅ pass' if r['cat_ok'] else '❌ fail'} | {'✅ pass' if r['pri_ok'] else '❌ fail'} | "
            f"{NEXT_MARK[r['next_problem_model']]} → {NEXT_MARK[r['next_problem']]} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


NEXT_MARK = {"": "✅", "to_customer": "❌ клієнту", "promise": "❌ обіцянка"}


def write_summary(path: Path, prompt: str, summaries: dict[str, dict]):
    lines = [
        f"# Порівняння моделей — prompt {prompt}",
        "",
        "| Модель | Category acc | Priority acc | Both | Next step ok (модель → фінал) | API latency avg / p95, с | Повна latency avg / p95, с | Токени in / out | $ / тікет | $ / 10k тікетів | Fallback / retries |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model, s in summaries.items():
        lines.append(
            f"| {model} | {s['cat_acc']:.0%} | {s['pri_acc']:.0%} | {s['both_acc']:.0%} | "
            f"{s['next_ok_model']:.0%} → {s['next_ok']:.0%} | "
            f"{s['api_lat_avg']:.2f} / {s['api_lat_p95']:.2f} | "
            f"{s['lat_avg']:.2f} / {s['lat_p95']:.2f} | "
            f"{s['prompt_tokens_avg']:.0f} / {s['completion_tokens_avg']:.0f} | "
            f"${s['cost_avg']:.6f} | ${s['cost_avg'] * TICKETS_PER_MONTH:.2f} | "
            f"{s['fallbacks']} / {s['retries']} |"
        )
    lines += [
        "",
        "API latency — час успішного запиту (швидкість моделі). Повна latency — включно з retry і паузами "
        "після rate limit на безкоштовному тарифі Groq. Next step ok — крок адресований агенту і нічого не обіцяє "
        "клієнту; «фінал» — після заміни кроку для чутливих категорій кодом (`override_next_step`).",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="v1")
    parser.add_argument("--model", action="append", help="за замовчуванням — усі моделі")
    parser.add_argument("--dataset", default="test_set", help="test_set або holdout_set")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], help="лише для gpt-oss")
    parser.add_argument("--rescore", action="store_true", help="перерахувати метрики зі збережених JSON, без API")
    parser.add_argument("--risk", action="store_true", help="з окремою перевіркою ризиків моделлю (results/<prompt>_risk)")
    args = parser.parse_args()

    tickets = json.loads((HERE / "data" / f"{args.dataset}.json").read_text(encoding="utf-8"))
    out_dir = HERE / "results" / args.prompt
    if args.reasoning_effort:
        out_dir = HERE / "results" / f"{args.prompt}_reasoning-{args.reasoning_effort}"
    if args.risk:
        out_dir = HERE / "results" / f"{args.prompt}_risk"
    if args.dataset != "test_set":
        out_dir = out_dir / args.dataset
    out_dir.mkdir(parents=True, exist_ok=True)

    summaries = {}
    for model in args.model or ALL_MODELS:
        print(f"\n=== {model} / prompt {args.prompt} ===")
        if args.rescore:
            path = out_dir / f"{slug(model)}.json"
            if not path.exists():
                continue
            rows = [score_next_step(r) for r in json.loads(path.read_text(encoding="utf-8"))]
        else:
            rows = run_model(model, args.prompt, tickets, args.reasoning_effort, check_risk=args.risk)
        s = summarize(rows)
        summaries[model] = s
        write_model_report(out_dir / f"{slug(model)}.md", model, args.prompt, rows, s)
        (out_dir / f"{slug(model)}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  -> category {s['cat_acc']:.0%}, priority {s['pri_acc']:.0%}, both {s['both_acc']:.0%}, "
              f"next step {s['next_ok_model']:.0%} -> {s['next_ok']:.0%}")

    write_summary(out_dir / "summary.md", args.prompt, summaries)
    print(f"\nЗвіти: {out_dir}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except APIKeyError as e:
        sys.exit(str(e))
