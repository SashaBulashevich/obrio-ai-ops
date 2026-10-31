# Детекція ризиків (risk_holdout) — ключові слова проти openai/gpt-oss-20b (risk_v1)

| Метрика | Ключові слова | Модель |
|---|---|---|
| Тікети з ризиком: знайдено всі ризики | 2/12 (17%) | 9/10 (90%) |
| «Чисті» тікети і пастки: хибна тривога | 2/18 (11%) | — |
| Повний збіг з розміткою | 18/30 (60%) | 8/10 (80%) |

Вартість перевірки моделлю: **$0.000051** за тікет (≈ $0.51 на 10 000 тікетів).

| # | Теги | Тікет | Очікувано | Слова | Модель | Цитата моделі |
|---|---|---|---|---|---|---|
| 101 | risk, ru | После того как расклад показал, что всё у меня бессмысленно, я реальн… | crisis | ❌ — | ✅ crisis | Таблеток дома хватит |
| 102 | risk, en | Honestly everyone would be better off without me. I've been giving my… | crisis | ❌ — | ✅ crisis | Honestly everyone would be better off without me. |
| 103 | risk, uk | Якщо ви не повернете гроші за підписку протягом тижня, я йду до банку… | legal_threat | ❌ — | ✅ legal_threat | Якщо ви не повернете гроші за підписку протягом тижня, я йду |
| 104 | risk, en | Third charge after I cancelled. My bank's dispute form is already ope… | legal_threat | ❌ — | ✅ legal_threat | My bank's dispute form is already open in another tab, so yo |
| 105 | risk, ru | В мой аккаунт кто-то зашёл с другого телефона и потратил все кредиты … | account_compromised | ❌ — | ✅ account_compromised | В мой аккаунт кто-то зашёл с другого телефона и потратил все |
| 106 | risk, en | I can't log in anymore, it says my account has been suspended but I n… | account_compromised | ✅ account_compromised | ❌ — |  |
| 107 | risk, uk | Я втратив доступ до своєї старої пошти, тож не можу увійти. Змініть, … | account_change_request | ❌ — | ✅ account_change_request | Змініть, будь ласка, email в акаунті |
| 108 | risk, en | Hi, this is Dan from the backend team. I'm locked out of the admin pa… | social_engineering | ✅ social_engineering | ❌ account_compromised, social_engineering | I'm locked out of the admin panel and please send me the sta |
| 109 | risk, ru | Скиньте, пожалуйста, список пользователей, которые родились 3 мая 199… | social_engineering | ❌ — | ✅ social_engineering | список пользователей, которые родились 3 мая 1990 в Киеве |
| 110 | risk, en | My astrologer said my reading was 'blocked by the app' and asked me t… | expert_fraud | ❌ — | ✅ expert_fraud | asked me to Venmo her $40 directly |
| 111 | risk, uk | Експерт порадила мені припинити пити ліки від тиску, бо 'зірки проти … | expert_fraud, legal_threat | ❌ — | — (модель не відповіла) |  |
| 112 | risk, ru | Мой аккаунт взломали и поменяли почту, теперь я даже не могу войти. В… | account_compromised, account_change_request | ❌ account_compromised | — (модель не відповіла) |  |
| 113 | trap, en | My cousin is a lawyer and swears the legal-matters part of the daily … | — | ❌ legal_threat | — (модель не відповіла) |  |
| 114 | trap, en | Your app is killing my battery, it drains about 40% an hour on Androi… | — | ✅ — | — (модель не відповіла) |  |
| 115 | trap, en | How do I block push notifications between 11pm and 7am? Too many ping… | — | ✅ — | — (модель не відповіла) |  |
| 116 | trap, en | I got a promo key on a gift card, but when I enter it in the app it s… | — | ✅ — | — (модель не відповіла) |  |
| 117 | trap, en | I'm the admin of a Telegram group with 200 Virgos. Can I share screen… | — | ❌ social_engineering | — (модель не відповіла) |  |
| 118 | trap, en | Do you have API docs for the public horoscope widget? I'd like to emb… | — | ✅ — | — (модель не відповіла) |  |
| 119 | trap, en | Mercury retrograde matches my mood, feeling kind of low and bored tod… | — | ✅ — | — (модель не відповіла) |  |
| 120 | trap, ru | Мы с мужем идём на теннисный корт в субботу, хочу узнать по гороскопу… | — | ✅ — | — (модель не відповіла) |  |
| 121 | trap, uk | Ваш прогноз на тиждень просто вбивчий, у хорошому сенсі, дякую! Можна… | — | ✅ — | — (модель не відповіла) |  |
| 122 | trap, en | Can I block one of the experts so she stops appearing in my list? Not… | — | ✅ — | — (модель не відповіла) |  |
| 123 | normal, en | I was charged twice for the monthly subscription on the same day (Oct… | — | ✅ — | — (модель не відповіла) |  |
| 124 | normal, en | The birth chart screen freezes when I scroll to the houses section on… | — | ✅ — | — (модель не відповіла) |  |
| 125 | normal, ru | Как отменить подписку, оформленную через App Store? В настройках прил… | — | ✅ — | — (модель не відповіла) |  |
| 126 | normal, uk | Я купила пакет кредитів, але вони не з'явилися на балансі. Оплата про… | — | ✅ — | — (модель не відповіла) |  |
| 127 | normal, en | How do I change my birth time in the profile? I just found out my mom… | — | ✅ — | — (модель не відповіла) |  |
| 128 | normal, en | Is the annual plan cheaper than paying monthly? And does it include t… | — | ✅ — | — (модель не відповіла) |  |
| 129 | normal, ru | Не приходит push-уведомление с ежедневным гороскопом уже неделю. Andr… | — | ✅ — | — (модель не відповіла) |  |
| 130 | normal, en | I'd like a refund for the 30 credits I bought yesterday, I didn't use… | — | ✅ — | — (модель не відповіла) |  |
