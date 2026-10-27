"""База знань і пошук BM25.

Статті — markdown-файли в kb/ з метаданими (id, title, category) у шапці.
Пошук — BM25 (класичний алгоритм ранжування за словами) по англійському запиту,
який формує LLM на кроці аналізу тікету (тікет може бути будь-якою мовою, база — англійською).
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rank_bm25 import BM25Okapi

KB_DIR = Path(__file__).resolve().parent / "kb"

STOPWORDS = set(
    "a an the and or but if to of in on at for with by from is are was were be been it its this that "
    "i me my we our you your they them he she his her do does did not no can could will would should "
    "have has had so as up out about into than then there here what which who how when why all any".split()
)


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    category: str
    body: str


@dataclass(frozen=True)
class Hit:
    article: Article
    score: float


def _stem(word: str) -> str:
    """Дуже простий стемер: charges/charged/charging -> charg. Достатньо для 17 статей."""
    for suffix in ("ations", "ation", "ings", "ing", "ies", "ied", "ed", "es", "s", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [_stem(w) for w in words if w not in STOPWORDS]


def _parse(path: Path) -> Article:
    raw = path.read_text(encoding="utf-8")
    _, header, body = raw.split("---", 2)
    meta = dict(line.split(":", 1) for line in header.strip().splitlines())
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    return Article(id=meta["id"], title=meta["title"], category=meta["category"], body=body.strip())


@lru_cache(maxsize=1)
def load_kb() -> tuple[list[Article], BM25Okapi]:
    """Індекс будується один раз на процес (кеш у пам'яті)."""
    articles = [_parse(p) for p in sorted(KB_DIR.glob("*.md"))]
    # заголовок повторюємо двічі — слова з заголовка важливіші
    corpus = [tokenize(f"{a.title} {a.title} {a.body}") for a in articles]
    return articles, BM25Okapi(corpus)


CATEGORY_BOOST = 1.3


def search(queries: list[str], k: int = 3, boost_category: str | None = None) -> list[Hit]:
    """Пошук за кількома запитами: для кожної статті беремо найкращий score.

    boost_category — категорія з класифікатора Task 1: статті цієї категорії отримують
    множник CATEGORY_BOOST (гібрид "ключові слова + намір клієнта").
    """
    articles, bm25 = load_kb()
    best = [0.0] * len(articles)
    for q in queries:
        for i, s in enumerate(bm25.get_scores(tokenize(q))):
            best[i] = max(best[i], float(s))
    if boost_category:
        best = [s * CATEGORY_BOOST if a.category == boost_category else s for s, a in zip(best, articles)]
    ranked = sorted(range(len(articles)), key=lambda i: best[i], reverse=True)
    return [Hit(articles[i], round(best[i], 3)) for i in ranked[:k] if best[i] > 0]


def get_article(article_id: str) -> Article | None:
    articles, _ = load_kb()
    return next((a for a in articles if a.id == article_id), None)


def _norm(text: str) -> str:
    text = text.replace("’", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_in_article(quote: str, article: Article) -> bool:
    """Чи є цитата дослівно в статті (з нормалізацією пробілів, лапок, тире)."""
    q = _norm(quote).strip(" .\"'")
    return len(q) >= 15 and q in _norm(article.body)
