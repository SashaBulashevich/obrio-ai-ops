"""Спільний клієнт для LLM-викликів (Task 1 і Task 2).

Що він робить поверх звичайного виклику API:
- timeout на кожен запит;
- retry з експоненційною затримкою при rate limit (429), timeout і 5xx;
- перевірку, що відповідь — валідний JSON потрібної схеми;
- при невалідному JSON — один повтор з підказкою моделі, що саме не так;
- fallback-ланцюжок моделей: якщо основна модель не впоралась, пробуємо наступну;
- метрики: модель, latency, токени, вартість, кількість спроб.
"""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import groq
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from common.pricing import cost_usd

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

REQUEST_TIMEOUT_S = 20
MAX_RETRIES = 3          # на одну модель, для rate limit / timeout / 5xx
BACKOFF_BASE_S = 1.5     # 1.5s, 3s, 6s ...


@dataclass
class LLMResult:
    data: BaseModel | None          # розпарсена і провалідована відповідь
    model: str | None               # яка модель реально відповіла
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    attempts: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.data is not None


_client = None


def get_client() -> groq.Groq:
    global _client
    if _client is None:
        # max_retries=0: повтори робимо самі, щоб контролювати й логувати їх
        _client = groq.Groq(api_key=os.environ["GROQ_API_KEY"], timeout=REQUEST_TIMEOUT_S, max_retries=0)
    return _client


def _parse(raw: str, schema: type[BaseModel]) -> BaseModel:
    """JSON -> pydantic-модель. Кидає ValueError з поясненням, якщо щось не так."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from e
    try:
        return schema.model_validate(obj)
    except ValidationError as e:
        raise ValueError(f"schema mismatch: {e.errors()[:3]}") from e


def call_json(
    messages: list[dict],
    schema: type[BaseModel],
    models: list[str],
    temperature: float = 0.0,
    max_tokens: int = 1200,
) -> LLMResult:
    """Викликає моделі по черзі (fallback), поки одна не поверне валідний JSON.

    Повертає LLMResult; якщо всі моделі впали — result.ok == False,
    і вирішувати, що робити далі (напр. відправити в ручну чергу), має викликач.
    """
    result = LLMResult(data=None, model=None)
    started = time.perf_counter()

    for model in models:
        msgs = list(messages)
        repaired = False
        retries = 0
        while True:
            result.attempts += 1
            try:
                resp = get_client().chat.completions.create(
                    model=model,
                    messages=msgs,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
            except (groq.RateLimitError, groq.APITimeoutError, groq.APIConnectionError, groq.InternalServerError) as e:
                result.errors.append(f"{model}: {type(e).__name__}")
                if retries < MAX_RETRIES:
                    time.sleep(_retry_delay(e, retries))
                    retries += 1
                    continue
                break  # ця модель недоступна -> наступна в ланцюжку
            except groq.BadRequestError as e:
                # Groq повертає 400 json_validate_failed, якщо модель видала не-JSON
                result.errors.append(f"{model}: BadRequest {_short(e)}")
                if not repaired:
                    repaired = True
                    msgs = msgs + [{"role": "user", "content": "Your previous output was not valid JSON. Return ONLY one valid JSON object matching the schema."}]
                    continue
                break

            usage = resp.usage
            result.prompt_tokens += usage.prompt_tokens
            result.completion_tokens += usage.completion_tokens
            details = getattr(usage, "prompt_tokens_details", None)
            cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
            result.cached_tokens += cached
            result.cost_usd += cost_usd(model, usage.prompt_tokens, usage.completion_tokens, cached)

            raw = resp.choices[0].message.content or ""
            try:
                result.data = _parse(raw, schema)
                result.model = model
                result.latency_s = time.perf_counter() - started
                return result
            except ValueError as e:
                result.errors.append(f"{model}: {e}")
                if not repaired:
                    # одна спроба "ремонту": показуємо моделі її відповідь і помилку
                    repaired = True
                    msgs = msgs + [
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": f"Your output failed validation ({e}). Return ONLY the corrected JSON object."},
                    ]
                    continue
                break

    result.latency_s = time.perf_counter() - started
    return result


def _retry_delay(err: Exception, attempt: int) -> float:
    """Поважаємо заголовок retry-after від API, інакше — експоненційна затримка."""
    response = getattr(err, "response", None)
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), 30.0)
            except ValueError:
                pass
    return BACKOFF_BASE_S * (2 ** attempt)


def _short(err: Exception) -> str:
    return str(err)[:120]
