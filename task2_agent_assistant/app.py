"""Веб-інтерфейс асистента агента (Streamlit).

Запуск з кореня репозиторію:
    streamlit run task2_agent_assistant/app.py
"""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import assistant  # noqa: E402

st.set_page_config(page_title="Nebula Support Copilot", page_icon="✨", layout="wide")

SAMPLES = {
    "— обери приклад або встав свій тікет —": "",
    "Подвійне списання (uk)": "Добрий день, з мене списали гроші двічі за підписку цього місяця. Поверніть, будь ласка, один платіж.",
    "Скасування підписки (en)": "Hi, how do I cancel my subscription? I don't want it to renew next month.",
    "Агресивний тон + баг (en)": "your stupid app keeps freezing every 2 minutes, worst garbage I've ever paid for. fix it!!!",
    "Змішані теми (en)": "The app crashes whenever I open the chat, and I was charged for 30 minutes of credits I couldn't use. I want that money back.",
    "Експерт просить PayPal (en)": "An expert told me my aura is blocked and asked me to send $150 via PayPal directly to her for a cleansing ritual. Is this allowed?",
    "Погроза chargeback (en)": "I was charged twice, refund me or I'll dispute with my bank.",
    "Кризовий стан (en)": "Nothing matters anymore. My reading said things won't get better. I don't see the point in going on.",
    "Асцендент (es)": "¿Cómo puedo ver mi signo ascendente en la app? No lo encuentro.",
    "Видалення даних (ru)": "Удалите мой аккаунт и все мои данные. Больше не хочу пользоваться приложением.",
}
TONES = {"formal": "🎩 Формальний", "empathetic": "💛 Емпатійний", "short": "⚡ Короткий"}
PRIORITY_COLOR = {"P1": "red", "P2": "orange", "P3": "blue", "P4": "gray"}


@st.cache_data(ttl=3600, show_spinner=False)
def cached_assist(ticket: str, model: str, prompt: str) -> dict:
    # Кеш на рівні сесії сервера: повторний клік / перемикання вкладок не платить за API.
    # Ключ = текст + модель + версія промпту, тож зміна будь-чого інвалідує кеш.
    # обрана модель — перша, решта gpt-oss — fallback (у кожної моделі окремі rate limits)
    models = [model] + [m for m in assistant.GENERATE_MODELS if m != model]
    return assistant.assist(ticket, generate_models=models, generate_prompt=prompt)


# ---------- sidebar ----------
with st.sidebar:
    st.header("Налаштування")
    model = st.selectbox("Модель для відповідей", ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"])
    prompt_version = st.selectbox("Версія промпту", sorted(
        p.stem for p in assistant.PROMPTS_DIR.glob("generate_v*.md"))[::-1])
    st.divider()
    st.caption("Прототип для тестового AI Ops (OBRIO). База знань симульована — "
               "17 статей у `task2_agent_assistant/kb/`.")

# ---------- input ----------
st.title("✨ Nebula Support Copilot")
st.caption("Встав текст тікету — отримаєш summary, 3 варіанти відповіді й джерела з бази знань. "
           "Остаточне рішення завжди за агентом.")

sample = st.selectbox("Приклади", list(SAMPLES), label_visibility="collapsed")
ticket = st.text_area("Текст тікету", value=SAMPLES[sample], height=130, placeholder="Встав сюди звернення клієнта…")
run = st.button("Проаналізувати", type="primary", disabled=not ticket.strip())

if run:
    st.session_state["result"] = None
    with st.spinner("Аналізую тікет, шукаю в базі знань, готую відповіді…"):
        st.session_state["result"] = cached_assist(ticket.strip(), model, prompt_version)
    st.session_state["confirmed"] = False

r = st.session_state.get("result")
if not r:
    st.stop()

# ---------- human judgment banner ----------
hj = r["human_judgment"]
if hj["level"] == "blocked":
    st.error("⛔ **AI-варіанти приховано. Відповідь пише людина.**\n\n" + "\n".join(f"- {x}" for x in hj["reasons"]))
elif hj["level"] == "review":
    st.warning("⚠️ **Потрібне рішення агента — не надсилай без перевірки.**\n\n" + "\n".join(f"- {x}" for x in hj["reasons"]))
else:
    st.success("✅ Типовий тікет. Перевір відповідь і надсилай.")

# ---------- summary + replies ----------
left, right = st.columns([2, 3], gap="large")

with left:
    c = r["classification"]
    st.subheader("Summary")
    st.write(r["summary"])
    st.markdown(
        f":{PRIORITY_COLOR[c['priority']]}-badge[{c['priority']}] "
        f":violet-badge[{c['category']}] :gray-badge[{c['assigned_team']}] "
        f":gray-badge[мова: {r['customer_language']}] :gray-badge[тон: {c['sentiment']}]"
    )
    if r["key_facts"]:
        st.markdown("**Ключові факти**\n" + "\n".join(f"- {x}" for x in r["key_facts"]))
    if r["missing_info"]:
        st.markdown("**Чого бракує (запитати в клієнта)**\n" + "\n".join(f"- {x}" for x in r["missing_info"]))
    if r["agent_notes"]:
        st.info(f"**Нотатка AI для агента:** {r['agent_notes']}")

with right:
    st.subheader("Варіанти відповіді")
    if r["replies"] is None:
        st.write("Немає AI-варіантів для цього тікету.")
    else:
        locked = hj["level"] == "review" and not st.session_state.get("confirmed")
        if hj["level"] == "review":
            st.checkbox("Я перевірив(ла) факти й причини вище — розблокувати копіювання", key="confirmed")
        tabs = st.tabs([TONES[t] for t in TONES])
        for tab, tone in zip(tabs, TONES):
            with tab:
                text = r["replies"][tone]
                if locked:
                    st.text_area(tone, text, height=220, disabled=True, label_visibility="collapsed")
                    st.caption("🔒 Копіювання заблоковано до підтвердження перевірки.")
                else:
                    st.code(text, language=None, wrap_lines=True)
                    st.caption(f"{len(text.split())} слів · кнопка копіювання — у правому верхньому куті блоку")

# ---------- knowledge base ----------
st.subheader("📚 Джерела з бази знань")
if not r["articles"]:
    st.write("Релевантних статей не знайдено.")
for a in r["articles"]:
    quotes = [q for q in r["citations"] if q["article_id"] == a["id"]]
    with st.expander(f"{a['title']}  ·  `{a['id']}`  ·  BM25 score {a['score']}", expanded=bool(quotes)):
        for q in quotes:
            mark = "✅ дослівно є в статті" if q["valid"] else "❌ цитати НЕМАЄ в статті"
            st.markdown(f"> {q['quote']}\n\n{mark}")
        st.markdown("---\n" + a["body"])
bad = [q for q in r["citations"] if not q["valid"] and q["article_id"] not in {a["id"] for a in r["articles"]}]
for q in bad:
    st.markdown(f"❌ Посилання на неіснуючу статтю `{q['article_id']}`: {q['quote']}")

m = r["meta"]
models = ", ".join(sorted({v["model"] for v in m.values() if isinstance(v, dict) and v.get("model")}))
st.caption(f"⏱ {m['total_latency_s']} с · 💲 {m['total_cost_usd']:.5f} · моделі: {models} · "
           f"пошукові запити: {', '.join(r['search_queries'])}")
