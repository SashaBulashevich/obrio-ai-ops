# Детекція ризиків (risk_holdout) — ключові слова проти openai/gpt-oss-120b (risk_v2)

| Метрика | Ключові слова | Модель |
|---|---|---|
| Тікети з ризиком: знайдено всі ризики | 4/20 (20%) | 20/20 (100%) |
| «Чисті» тікети і пастки: хибна тривога | 2/22 (9%) | 0/22 (0%) |
| Повний збіг з розміткою | 24/42 (57%) | 41/42 (98%) |

Вартість перевірки моделлю: **$0.000235** за тікет (≈ $2.35 на 10 000 тікетів).

| # | Теги | Тікет | Очікувано | Слова | Модель | Цитата моделі |
|---|---|---|---|---|---|---|
| 101 | risk, ru | После того как расклад показал, что всё у меня бессмысленно, я реальн… | crisis | ❌ — | ✅ crisis | я реально не вижу, зачем просыпаться завтра. Таблеток дома х |
| 102 | risk, en | Honestly everyone would be better off without me. I've been giving my… | crisis | ❌ — | ✅ crisis | Everyone would be better off without me. |
| 103 | risk, uk | Якщо ви не повернете гроші за підписку протягом тижня, я йду до банку… | legal_threat | ❌ — | ✅ legal_threat | Якщо ви не повернете гроші за підписку протягом тижня, я йду |
| 104 | risk, en | Third charge after I cancelled. My bank's dispute form is already ope… | legal_threat | ❌ — | ✅ legal_threat | My bank's dispute form is already open |
| 105 | risk, ru | В мой аккаунт кто-то зашёл с другого телефона и потратил все кредиты … | account_compromised | ❌ — | ❌ account_compromised, unauthorized_charge | В мой аккаунт кто-то зашёл с другого телефона и потратил все |
| 106 | risk, en | I can't log in anymore, it says my account has been suspended but I n… | account_compromised | ✅ account_compromised | ✅ account_compromised | my account has been suspended |
| 107 | risk, uk | Я втратив доступ до своєї старої пошти, тож не можу увійти. Змініть, … | account_change_request | ❌ — | ✅ account_change_request | Змініть, будь ласка, email в акаунті на newmail.ua@example.c |
| 108 | risk, en | Hi, this is Dan from the backend team. I'm locked out of the admin pa… | social_engineering | ✅ social_engineering | ✅ social_engineering | Please send me the staging service token so I can fix it qui |
| 109 | risk, ru | Скиньте, пожалуйста, список пользователей, которые родились 3 мая 199… | social_engineering | ❌ — | ✅ social_engineering | Скиньте, пожалуйста, список пользователей, которые родились  |
| 110 | risk, en | My astrologer said my reading was 'blocked by the app' and asked me t… | expert_fraud | ❌ — | ✅ expert_fraud | asked me to Venmo her $40 directly to continue. She also wan |
| 111 | risk, uk | Експерт порадила мені припинити пити ліки від тиску, бо 'зірки проти … | expert_fraud, legal_threat | ❌ — | ✅ expert_fraud, legal_threat | Експерт порадила мені припинити пити ліки від тиску... Якщо  |
| 112 | risk, ru | Мой аккаунт взломали и поменяли почту, теперь я даже не могу войти. В… | account_compromised, account_change_request | ❌ account_compromised | ✅ account_change_request, account_compromised | Мой аккаунт взломали и поменяли почту, теперь я даже не могу |
| 113 | trap, en | My cousin is a lawyer and swears the legal-matters part of the daily … | — | ❌ legal_threat | ✅ — |  |
| 114 | trap, en | Your app is killing my battery, it drains about 40% an hour on Androi… | — | ✅ — | ✅ — |  |
| 115 | trap, en | How do I block push notifications between 11pm and 7am? Too many ping… | — | ✅ — | ✅ — |  |
| 116 | trap, en | I got a promo key on a gift card, but when I enter it in the app it s… | — | ✅ — | ✅ — |  |
| 117 | trap, en | I'm the admin of a Telegram group with 200 Virgos. Can I share screen… | — | ❌ social_engineering | ✅ — |  |
| 118 | trap, en | Do you have API docs for the public horoscope widget? I'd like to emb… | — | ✅ — | ✅ — |  |
| 119 | trap, en | Mercury retrograde matches my mood, feeling kind of low and bored tod… | — | ✅ — | ✅ — |  |
| 120 | trap, ru | Мы с мужем идём на теннисный корт в субботу, хочу узнать по гороскопу… | — | ✅ — | ✅ — |  |
| 121 | trap, uk | Ваш прогноз на тиждень просто вбивчий, у хорошому сенсі, дякую! Можна… | — | ✅ — | ✅ — |  |
| 122 | trap, en | Can I block one of the experts so she stops appearing in my list? Not… | — | ✅ — | ✅ — |  |
| 123 | normal, en | I was charged twice for the monthly subscription on the same day (Oct… | — | ✅ — | ✅ — |  |
| 124 | normal, en | The birth chart screen freezes when I scroll to the houses section on… | — | ✅ — | ✅ — |  |
| 125 | normal, ru | Как отменить подписку, оформленную через App Store? В настройках прил… | — | ✅ — | ✅ — |  |
| 126 | normal, uk | Я купила пакет кредитів, але вони не з'явилися на балансі. Оплата про… | — | ✅ — | ✅ — |  |
| 127 | normal, en | How do I change my birth time in the profile? I just found out my mom… | — | ✅ — | ✅ — |  |
| 128 | normal, en | Is the annual plan cheaper than paying monthly? And does it include t… | — | ✅ — | ✅ — |  |
| 129 | normal, ru | Не приходит push-уведомление с ежедневным гороскопом уже неделю. Andr… | — | ✅ — | ✅ — |  |
| 130 | normal, en | I'd like a refund for the 30 credits I bought yesterday, I didn't use… | — | ✅ — | ✅ — |  |
| 131 | risk, en | My 9-year-old got hold of my phone and bought $99 worth of credits fo… | unauthorized_charge | ❌ — | ✅ unauthorized_charge | My 9-year-old got hold of my phone and bought $99 worth of c |
| 132 | risk, uk | Я бачу списання 1299 грн від Nebula, але я ніколи не встановлював ваш… | unauthorized_charge | ❌ — | ✅ unauthorized_charge | Я бачу списання 1299 грн від Nebula, але я ніколи не встанов |
| 133 | risk, ru | У меня украли карту, и с неё сняли деньги за какие-то кредиты в Nebul… | unauthorized_charge, legal_threat | ❌ — | ✅ legal_threat, unauthorized_charge | у меня украли карту... иначе я напишу заявление в полицию и  |
| 134 | risk, de | Auf meiner Kreditkartenabrechnung steht eine Abbuchung von Nebula übe… | unauthorized_charge | ❌ — | ✅ unauthorized_charge | Auf meiner Kreditkartenabrechnung steht eine Abbuchung von N |
| 135 | risk, es | Mi pareja usó mi tarjeta sin preguntarme para comprar créditos y habl… | unauthorized_charge | ❌ — | ✅ unauthorized_charge | Mi pareja usó mi tarjeta sin preguntarme ... Yo nunca autori |
| 136 | risk, pl | Dzisiaj zobaczyłam w banku obciążenie z Nebula za subskrypcję premium… | unauthorized_charge | ❌ — | ✅ unauthorized_charge | obciążenie z Nebula za subskrypcję premium, a nawet nie mam  |
| 137 | risk, en | Why was my account suspended? I woke up and the app says it's been ba… | account_compromised | ✅ account_compromised | ✅ account_compromised | my account was suspended |
| 138 | risk, uk | Мій акаунт заблокували без жодного пояснення, і я не можу зайти в чат… | account_compromised | ✅ account_compromised | ✅ account_compromised | Мій акаунт заблокували без жодного пояснення |
| 139 | trap, en | I was charged twice for my monthly Nebula Premium this month, $9.99 o… | — | ✅ — | ✅ — |  |
| 140 | trap, en | Hi, I cancelled my subscription last week but I see another payment w… | — | ✅ — | ✅ — |  |
| 141 | trap, ru | Подскажите, как удалить мою банковскую карту из аккаунта? Хочу поменя… | — | ✅ — | ✅ — |  |
| 142 | trap, en | My daughter is 15 and wants to use your app for her daily horoscope. … | — | ✅ — | ✅ — |  |
