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
from common.llm_client import call_json  # noqa: E402

ALL_MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
# Суддя — модель іншої родини, ніж основний кандидат (gpt-oss): менше упередження "сам себе хвалить".
# Додатково: окрема квота rate limits (free tier Groq: 8k токенів/хв і 200k/добу на модель).
JUDGE_MODEL = "qwen/qwen3.8-27b"
STAGE1_CACHE = HERE / "results" / "_stage1_cache.json"
TICKETS_PER_MONTH = 10_000

# Batch-прогін може чекати довше, ніж інтерактивний UI: хвилинний ліміт токенів скидається за ≤60 с
llm_client.MAX_RETRIES = 6
llm_client.RETRY_AFTER_CAP_S = 65.0


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
    key_version = f"{assistant.ANALYZE_PROMPT}|task1-v3"
    for t in tickets:
        key = f"{key_version}|{t['text']}"
        if key in cache:
            continue
        cls = assistant.classify(t["text"])
        an = assistant.analyze(t["text"])
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
        j = row["judge"]
        row["quality_ok"] = None if not row["generated"] else bool(
            j and j["grounded"] and not j["unauthorized_promise"] and j["addresses_request"]
            and j["formal_ok"] and j["empathetic_ok"] and j["short_ok"] and j["same_language_as_ticket"]
            and row["checks"] and not row["checks"]["invalid_citations"]
        )
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
        "level_acc": pct(r["level_ok"] for r in rows),
        "gen_latency": statistics.mean(r["gen_latency"] for r in gen) if gen else 0,
        "cost_ticket": statistics.mean(r["base_cost"] + r["gen_cost"] for r in rows),
        "failed": sum(not r["generated"] and r["level"] != "blocked" for r in rows),
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
        "| Модель | Quality pass | Grounded | Без неавторизованих обіцянок | 3 тони ок | Різниця тонів (1–5) | Мова | Цитати валідні | Human-judgment level acc | Latency генерації, с | $ / тікет (весь конвеєр) | $ / 10k |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model, rows in per_model.items():
        s = summarize(rows)
        shared.append(
            f"| {model} | {s['quality']} | {s['grounded']} | {s['no_promise']} | {s['tones_ok']} | {s['tone_distinct']} | "
            f"{s['language']} | {s['citations_valid']} | {s['level_acc']} | {s['gen_latency']:.2f} | "
            f"${s['cost_ticket']:.5f} | ${s['cost_ticket'] * TICKETS_PER_MONTH:.2f} |"
        )
    shared += ["", "Quality pass = суддя: grounded, без неавторизованих обіцянок, відповідає на запит, усі 3 тони ок, "
               f"мова клієнта; і всі цитати дослівно є в статтях. Суддя — {JUDGE_MODEL} (для цієї моделі це самооцінка, див. README)."]
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
    args = parser.parse_args()

    tickets = json.loads((HERE / "data" / "test_set.json").read_text(encoding="utf-8"))
    print("=== stage 1: класифікація + аналіз ===")
    s1 = stage1(tickets)
    out_dir = HERE / "results" / args.prompt
    out_dir.mkdir(parents=True, exist_ok=True)
    per_model = {}
    for model in args.model or ALL_MODELS:
        print(f"\n=== {model} / {args.prompt} ===")
        per_model[model] = evaluate_model(model, args.prompt, tickets, s1, out_dir)
    write_reports(out_dir, args.prompt, per_model)
    print(f"\nЗвіти: {out_dir}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
