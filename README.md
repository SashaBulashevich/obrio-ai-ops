# OBRIO · AI Ops — тестове завдання (Nebula)

| Завдання | Що це | Документація |
|---|---|---|
| **Task 1** | AI-класифікатор тікетів: категорія, пріоритет, наступний крок, команда | [task1_classifier/README.md](task1_classifier/README.md) · [еволюція промпту](task1_classifier/PROMPTS.md) |
| **Task 2** | Internal AI-асистент support-агента: summary, 3 тони відповіді, цитати з бази знань | [task2_agent_assistant/README.md](task2_agent_assistant/README.md) |

**▶ Живе демо Task 2:** https://nebula-support-copilot-test-task.streamlit.app/ (безкоштовний тариф Groq: ~200k токенів на добу на модель; якщо gpt-oss вичерпали ліміт, підхоплює запасна qwen3.8-27b. Якщо застосунок «спить», натисни «Yes, get this app back up» і зачекай ~30 с).

Спільний код для обох задач — [common/](common/):
- `llm_client.py` — timeout, retry з backoff, перевірка JSON-схеми, repair-запит, fallback між моделями;
- `pricing.py` — ціни моделей і розрахунок вартості кожного виклику.

## Стек

- **Python 3.12**
- **Groq API** — моделі `openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`. Одна точка доступу до моделей двох різних розробників, дуже низька latency, автоматичний prompt caching.
- **pydantic** — валідація структурованого виходу моделі.

## Встановлення (Windows / macOS / Linux)

```bash
git clone https://github.com/SashaBulashevich/obrio-ai-ops.git
cd obrio-ai-ops
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # і встав свій ключ з https://console.groq.com/keys
```

Ключ API зберігається лише в локальному `.env`. Файл внесено в `.gitignore`.

**Без встановлення на свій комп'ютер:** на GitHub натисни **Code → Codespaces → Create codespace on main**. Налаштування з [.devcontainer/](.devcontainer/devcontainer.json) встановлять Python 3.12 і залежності та запустять Task 2. Ключ Groq додай так само: `cp .env.example .env` і встав ключ у `.env`.

## Швидка перевірка

```bash
python task1_classifier/classifier.py      # інтерактивний режим: вводиш тікети, отримуєш рішення
python -m unittest tests.test_failure_handling -v
```

Детальніше, з прикладами тікетів: [Task 1 → Як запустити](task1_classifier/README.md#як-запустити).
