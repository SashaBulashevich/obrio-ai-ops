"""Evaluation асистента агента.

Що міряємо:
  - класифікація (Task 1): category / priority
  - retrieval: hit@1, hit@3 (очікувана стаття в топі)
  - human judgment: рівень blocked / review / ok збігається з очікуваним
  - якість відповідей (на кожну модель генерації):
      перевірки в коді — валідні цитати, мова, довжина короткого варіанту, regex-обіцянки повернення;
      LLM-суддя (gpt-oss-120b) — тон кожного варіанту, grounded, неавторизовані обіцянки, чи відповідає на запит

Використання:
    python task2_agent_assistant/evaluate.py --prompt generate_v1
    python task2_agent_assistant/evaluate.py --prompt generate_v2 --model openai/gpt-oss-20b
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import assistant  # noqa: E402
import retrieval  # noqa: E402
from common import llm_client  # noqa: E402
from common.llm_client import APIKeyError, call_json  # noqa: E402

ALL_MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
# Суддя — модель іншої родини, ніж основний кандидат (gpt-oss): менше упередження "сам себе хвалить".
# Додатково: окрема квота rate limits (free tier Groq: 8k токенів/хв і 200k/добу на модель).
JUDGE_MODEL = "qwen/qwen3.8-27b"
EVAL_ANALYZE_PROMPT = "analyze_v1"
STAGE1_CACHE = HERE / "results" / "_stage1_cache.json"
TICKETS_PER_MONTH = 10_000

# Batch-прогін може чекати довше, ніж інтерактивний UI: хвилинний ліміт токенів скидається за ≤60 с
llm_client.MAX_RETRIES = 6
llm_client.RETRY_AFTER_CAP_S = 65.0
llm_client.CALL_BUDGET_S = None  # batch: ліміт часу на виклик лише для інтерактивного режиму


class Verdict(BaseModel):
    formal_ok: bool
    empathetic_ok: bool
    short_ok: bool
    tone_distinct: int = Field(ge=1, le=5)
    grounded: bool
    unauthorized_promise: bool
    addresses_request: bool
    same_language_as_ticket: bool
    issues: str = ""


def stage1(tickets: list[dict]) -> dict:
    """Класифікація + аналіз не залежать від моделі генерації — рахуємо один раз і кешуємо на диск.
    Інвалідація: видалити _stage1_cache.json (або змінити промпт аналізу / класифікатора)."""
    cache = json.loads(STAGE1_CACHE.read_text(encoding="utf-8")) if STAGE1_CACHE.exists() else {}
    # аналіз зафіксовано на v1: на ньому зроблено всі прогони; v2 лише додає about_nebula,
    # а всі 18 тікетів — про Nebula (перевірено окремо, див. README розділ 5)
    key_version = f"{EVAL_ANALYZE_PROMPT}|task1-v3"
    for t in tickets:
        key = f"{key_version}|{t['text']}"
        if key in cache:
            continue
        cls = assistant.classify(t["text"])
        an = assistant.analyze(t["text"], prompt=EVAL_ANALYZE_PROMPT)
        cache[key] = {"classification": cls, "analysis": an.data.model_dump() if an.ok else None,
                      "analyze_meta": assistant._meta(an)}
        print(f"  stage1 #{t['id']}: {cls['category']}/{cls['priority']}")
    STAGE1_CACHE.parent.mkdir(parents=True, exist_ok=True)
    STAGE1_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return {t["id"]: cache[f"{key_version}|{t['text']}"] for t in tickets}


def judge(ticket: str, hits, replies: dict) -> tuple[Verdict | None, float]:
    user = (f"<ticket>\n{ticket}\n</ticket>\n\nArticles:\n{assistant._articles_block(hits)}\n\n"
            f"Drafts:\n{json.dumps(replies, ensure_ascii=False, indent=1)}")
    res = call_json([{"role": "system", "content": (HERE / "prompts" / "judge_v1.md").read_text(encoding="utf-8")},
                     {"role": "user", "content": user}], Verdict, [JUDGE_MODEL], max_tokens=2500)
    return (res.data if res.ok else None), res.cost_usd


def quality_ok(row: dict) -> bool | None:
    j = row["judge"]
    return None if not row["generated"] else bool(
        j and j["grounded"] and not j["unauthorized_promise"] and j["addresses_request"]
        and j["formal_ok"] and j["empathetic_ok"] and j["short_ok"] and j["same_language_as_ticket"]
        and row["checks"] and not row["checks"]["invalid_citations"]
    )


def rejudge(model: str, s1: dict, out_dir: Path) -> list[dict]:
    """Переоцінити вже згенеровані відповіді поточним суддею — без повторної генерації.
    Потрібно, щоб порівнювати версії промпту одним і тим самим суддею."""
    path = out_dir / f"{model.replace('/', '__')}.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        if not row["generated"] or (row["judge"] and row.get("judge_model") == JUDGE_MODEL):
            continue
        st = s1[row["id"]]
        queries = st["analysis"]["search_queries"] if st["analysis"] else [row["text"][:300]]
        hits = retrieval.search(queries, k=3, boost_category=st["classification"]["category"])
        verdict, _ = judge(row["text"], hits, row["replies"])
        if verdict:
            row.update(judge=verdict.model_dump(), judge_model=JUDGE_MODEL)
            row["quality_ok"] = quality_ok(row)
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        q = {None: "—", True: "✅", False: "❌"}[row["quality_ok"]]
        print(f"  #{row['id']:>2} rejudge {'ok' if verdict else 'FAILED'}  quality {q}  {verdict.issues if verdict else ''}")
    return rows


def evaluate_model(model: str, prompt: str, tickets: list[dict], s1: dict, out_dir: Path) -> list[dict]:
    """Результати по кожному тікету кешуються: повторний запуск (після rate limit або вимкнення світла)
    дозаповнює лише тікети, де генерація чи суддя не вдалися."""
    cache_path = out_dir / f"{model.replace('/', '__')}.json"
    done = {}
    if cache_path.exists():
        done = {r["id"]: r for r in json.loads(cache_path.read_text(encoding="utf-8"))
                if r["level"] == "blocked" or (r["generated"] and r["judge"] and r.get("judge_model") == JUDGE_MODEL)}
    rows = []
    for t in tickets:
        if t["id"] in done:
            rows.append(done[t["id"]])
            print(f"  #{t['id']:>2} (з кешу)")
            continue
        st = s1[t["id"]]
        cls = st["classification"]
        analysis = assistant.Analysis(**st["analysis"]) if st["analysis"] else assistant.Analysis(
            summary="", customer_language=cls["language"], search_queries=[t["text"][:300]])
        hits = retrieval.search(analysis.search_queries, k=3, boost_category=cls["category"])
        ids = [h.article.id for h in hits]
        row = {
            "id": t["id"], "tags": t["tags"], "text": t["text"],
            "expected_category": t["expected_category"], "expected_priority": t["expected_priority"],
            "category": cls["category"], "priority": cls["priority"],
            "expected_articles": t["expected_articles"], "retrieved": ids,
            "hit1": (ids[:1] and ids[0] in t["expected_articles"]) or (not t["expected_articles"] and (not hits or hits[0].score < assistant.MIN_RETRIEVAL_SCORE)),
            "hit3": bool(set(ids) & set(t["expected_articles"])) or (not t["expected_articles"] and (not hits or hits[0].score < assistant.MIN_RETRIEVAL_SCORE)),
            "expected_level": t["expected_level"],
            "base_cost": cls["meta"]["cost_usd"] + st["analyze_meta"]["cost_usd"],
            "judge_model": JUDGE_MODEL,
        }
        level0 = assistant.human_judgment(cls, hits, None)
        if level0["level"] == "blocked":
            row.update(level="blocked", reasons=level0["reasons"], replies=None, generated=False,
                       gen_cost=0.0, gen_latency=0.0, judge=None, checks=None, citations=[])
        else:
            gen, _ = assistant.generate(t["text"], analysis, cls, hits, [model], prompt)
            if gen.ok:
                checks, citations = assistant.run_checks(gen.data, hits, analysis.customer_language)
                hj = assistant.human_judgment(cls, hits, checks)
                replies = gen.data.replies.model_dump()
                verdict, _ = judge(t["text"], hits, replies)
            else:
                checks, citations, replies, verdict = None, [], None, None
                hj = {"level": "review", "reasons": ["generation failed"]}
            row.update(level=hj["level"], reasons=hj["reasons"], replies=replies, generated=gen.ok,
                       gen_cost=gen.cost_usd, gen_latency=round(gen.api_latency_s, 3),
                       judge=verdict.model_dump() if verdict else None, checks=checks, citations=citations,
                       gen_errors=gen.errors)
        row["level_ok"] = row["level"] == t["expected_level"]
        row["quality_ok"] = quality_ok(row)
        j = row["judge"]
        rows.append(row)
        cache_path.write_text(json.dumps(rows + [r for i, r in done.items() if i not in {x["id"] for x in rows}],
                                         ensure_ascii=False, indent=2), encoding="utf-8")
        q = {None: "—", True: "✅", False: "❌"}[row["quality_ok"]]
        print(f"  #{t['id']:>2} level {row['level']:<7} {'✅' if row['level_ok'] else '❌'}  "
              f"retr {'✅' if row['hit3'] else '❌'}  quality {q}  {j['issues'] if j else ''}")
    return rows


def pct(xs) -> str:
    xs = [x for x in xs if x is not None]
    return f"{sum(map(bool, xs)) / len(xs):.0%}" if xs else "—"


def summarize(rows: list[dict]) -> dict:
    gen = [r for r in rows if r["generated"]]
    js = [r["judge"] for r in gen if r["judge"]]
    cits = [c for r in gen for c in r["citations"]]
    return {
        "quality": pct(r["quality_ok"] for r in gen),
        "grounded": pct(j["grounded"] for j in js),
        "no_promise": pct(not j["unauthorized_promise"] for j in js),
        "tones_ok": pct(j["formal_ok"] and j["empathetic_ok"] and j["short_ok"] for j in js),
        "tone_distinct": f"{statistics.mean(j['tone_distinct'] for j in js):.1f}" if js else "—",
        "language": pct(j["same_language_as_ticket"] for j in js),
        "citations_valid": pct(c["valid"] for c in cits),
        "regex_promise": sum(bool(r["checks"]["refund_promise"]) for r in gen if r["checks"]),
        "short_len_ok": pct(not r["checks"]["short_too_long"] for r in gen if r["checks"]),
        # тікети, де генерація впала через rate limit, не рахуємо: рівень там "review" через збій, а не через логіку
        "level_acc": pct(r["level_ok"] for r in rows if r["generated"] or r["level"] == "blocked"),
        "gen_latency": statistics.mean(r["gen_latency"] for r in gen) if gen else 0,
        # як і level_acc — без тікетів, де генерація впала (інакше вартість занижена: $0 за генерацію)
        "cost_ticket": statistics.mean(r["base_cost"] + r["gen_cost"] for r in rows if r["generated"] or r["level"] == "blocked"),
        "failed": sum(not r["generated"] and r["level"] != "blocked" for r in rows),
        "judge": ", ".join(sorted({r["judge_model"].split("/")[-1] for r in gen if r.get("judge")})) or "—",
        "generated": f"{len(gen)}/{sum(r['level'] != 'blocked' or r['generated'] for r in rows)}",
    }


def short(text: str, n: int = 60) -> str:
    text = text.replace("|", "/").replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def write_reports(out_dir: Path, prompt: str, per_model: dict[str, list[dict]]):
    any_rows = next(iter(per_model.values()))
    n = len(any_rows)
    shared = [
        f"# Task 2 evaluation — {prompt}", "",
        "## Спільні метрики (не залежать від моделі генерації)", "",
        f"- Category accuracy: **{pct(r['category'] == r['expected_category'] for r in any_rows)}**, "
        f"priority accuracy: **{pct(r['priority'] == r['expected_priority'] for r in any_rows)}** ({n} тікетів)",
        f"- Retrieval hit@1: **{pct(r['hit1'] for r in any_rows)}**, hit@3: **{pct(r['hit3'] for r in any_rows)}**", "",
        "## Порівняння моделей генерації", "",
        "| Модель | Суддя | Згенеровано | Quality pass | Grounded | Без неавторизованих обіцянок | 3 тони ок | Різниця тонів (1–5) | Мова | Цитати валідні | Human-judgment level acc | Latency генерації, с | $ / тікет (весь конвеєр) | $ / 10k |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model, rows in per_model.items():
        s = summarize(rows)
        shared.append(
            f"| {model} | {s['judge']} | {s['generated']} | {s['quality']} | {s['grounded']} | {s['no_promise']} | {s['tones_ok']} | {s['tone_distinct']} | "
            f"{s['language']} | {s['citations_valid']} | {s['level_acc']} | {s['gen_latency']:.2f} | "
            f"${s['cost_ticket']:.5f} | ${s['cost_ticket'] * TICKETS_PER_MONTH:.2f} |"
        )
    shared += ["", "Quality pass = суддя: grounded, без неавторизованих обіцянок, відповідає на запит, усі 3 тони ок, "
               "мова клієнта; і всі цитати дослівно є в статтях. Колонка «Суддя» — яка модель реально оцінювала відповіді; "
               "якщо вона збігається з моделлю генерації, це самооцінка (див. README).",
               "", "Згенеровано = тікети, для яких модель повернула відповідь (без заблокованих safety). "
               "Решта — rate limits безкоштовного тарифу; якісні метрики рахуються лише на згенерованих."]
    (out_dir / "summary.md").write_text("\n".join(shared) + "\n", encoding="utf-8")

    for model, rows in per_model.items():
        lines = [f"# {model} — {prompt}", "",
                 "| # | Теги | Input | Expected cat / pri / level | Actual cat / pri / level | Статті (топ-3) | Retrieval | Level | Quality | Проблема (суддя) |",
                 "|---|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            q = {None: "— (заблоковано)", True: "✅ pass", False: "❌ fail"}[r["quality_ok"]]
            issue = (r["judge"] or {}).get("issues", "") if r["judge"] else ""
            lines.append(
                f"| {r['id']} | {', '.join(r['tags'])} | {short(r['text'])} | "
                f"{r['expected_category']} / {r['expected_priority']} / {r['expected_level']} | "
                f"{r['category']} / {r['priority']} / {r['level']} | {', '.join(r['retrieved'])} | "
                f"{'✅' if r['hit3'] else '❌'} | {'✅' if r['level_ok'] else '❌'} | {q} | {short(issue, 90)} |"
            )
        (out_dir / f"{model.replace('/', '__')}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", default="generate_v1")
    parser.add_argument("--model", action="append")
    parser.add_argument("--report-only", action="store_true", help="лише перебудувати звіти з наявних JSON, без API")
    parser.add_argument("--rejudge", action="store_true", help="переоцінити наявні відповіді поточним суддею, без генерації")
    args = parser.parse_args()

    tickets = json.loads((HERE / "data" / "test_set.json").read_text(encoding="utf-8"))
    out_dir = HERE / "results" / args.prompt
    out_dir.mkdir(parents=True, exist_ok=True)
    per_model = {}
    if not args.report_only:
        print("=== stage 1: класифікація + аналіз ===")
        s1 = stage1(tickets)
    for model in [] if args.report_only else args.model or ALL_MODELS:
        print(f"\n=== {model} / {args.prompt}{' (rejudge)' if args.rejudge else ''} ===")
        per_model[model] = (rejudge(model, s1, out_dir) if args.rejudge
                            else evaluate_model(model, args.prompt, tickets, s1, out_dir))
    # у summary — усі моделі, вже прогнані з цим промптом, а не лише з цього запуску
    for f in sorted(out_dir.glob("*.json")):
        model = f.stem.replace("__", "/")
        if model not in per_model and model in ALL_MODELS:
            per_model[model] = json.loads(f.read_text(encoding="utf-8"))
    per_model = {m: per_model[m] for m in ALL_MODELS if m in per_model}
    write_reports(out_dir, args.prompt, per_model)
    print(f"\nЗвіти: {out_dir}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except APIKeyError as e:
        sys.exit(str(e))
