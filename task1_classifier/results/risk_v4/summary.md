# Зведення прогонів (risk_v4)

Кожен прогін — окремий запуск моделі на всьому наборі. Повних прогонів = без жодного тікета без відповіді.

| Модель | Набір | Прогонів | Знайдено всі ризики (кожен прогін) | Хибні тривоги (кожен прогін) | Без відповіді |
|---|---|---|---|---|---|
| openai/gpt-oss-120b | risk_holdout | 3 | 44/44 · 44/44 · 0/0 | 1/33 · 2/33 · 0/0 | 0 · 0 · 77 |
| openai/gpt-oss-120b | risk_set | 3 | 45/45 · 11/11 · 0/0 | 0/25 · 0/5 · 0/0 | 0 · 54 · 70 |
| openai/gpt-oss-20b | risk_holdout | 3 | 43/44 · 43/44 · 0/0 | 1/33 · 1/33 · 0/0 | 0 · 0 · 77 |
| openai/gpt-oss-20b | risk_set | 3 | 44/45 · 11/11 · 0/0 | 0/25 · 0/5 · 0/0 | 0 · 54 · 70 |
| qwen/qwen3.8-27b | risk_holdout | 3 | 44/44 · 44/44 · 0/0 | 0/33 · 0/33 · 0/0 | 0 · 0 · 77 |
| qwen/qwen3.8-27b | risk_set | 3 | 45/45 · 27/27 · 0/0 | 0/25 · 0/16 · 0/0 | 0 · 27 · 70 |

## Тікети, на яких прогони розходяться

| Модель | Набір | # | Різні відповіді |
|---|---|---|---|
| openai/gpt-oss-120b | risk_holdout | 145 | social_engineering, threat_to_others / threat_to_others |
| openai/gpt-oss-120b | risk_holdout | 149 | account_compromised, data_exposure / data_exposure |
| openai/gpt-oss-120b | risk_holdout | 151 | — / unauthorized_charge |
| openai/gpt-oss-120b | risk_holdout | 160 | customer_endangered, expert_fraud / expert_fraud |
| openai/gpt-oss-20b | risk_holdout | 131 | account_compromised, unauthorized_charge / unauthorized_charge |
| openai/gpt-oss-20b | risk_holdout | 146 | account_compromised, customer_endangered / account_compromised, customer_endangered, data_exposure |
| openai/gpt-oss-20b | risk_holdout | 157 | customer_endangered, threat_to_others / threat_to_others |
| openai/gpt-oss-20b | risk_holdout | 170 | account_compromised, crisis, customer_endangered, data_exposure / account_compromised, customer_endangered, data_exposure |
| openai/gpt-oss-20b | risk_holdout | 171 | account_compromised, customer_endangered / account_compromised, customer_endangered, data_exposure |
| openai/gpt-oss-20b | risk_set | 11 | legal_threat / legal_threat, unauthorized_charge |
