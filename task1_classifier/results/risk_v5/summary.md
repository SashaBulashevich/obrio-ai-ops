# Зведення прогонів (risk_v5)

Кожен прогін — окремий запуск моделі на всьому наборі. Повних прогонів = без жодного тікета без відповіді.

| Модель | Набір | Прогонів | Знайдено всі ризики (кожен прогін) | Хибні тривоги (кожен прогін) | Без відповіді |
|---|---|---|---|---|---|
| openai/gpt-oss-120b | risk_holdout | 3 | 55/55 · 55/55 · 55/55 | 0/38 · 0/38 · 1/38 | 0 · 0 · 0 |
| openai/gpt-oss-120b | risk_set | 3 | 45/45 · 45/45 · 45/45 | 0/25 · 0/25 · 0/25 | 0 · 0 · 0 |
| openai/gpt-oss-20b | risk_holdout | 3 | 55/55 · 55/55 · 55/55 | 1/38 · 2/38 · 1/38 | 0 · 0 · 0 |
| openai/gpt-oss-20b | risk_set | 3 | 45/45 · 45/45 · 45/45 | 0/25 · 0/25 · 0/25 | 0 · 0 · 0 |
| qwen/qwen3.8-27b | risk_holdout | 3 | 55/55 · 55/55 · 55/55 | 0/38 · 0/38 · 0/38 | 0 · 0 · 0 |
| qwen/qwen3.8-27b | risk_set | 3 | 45/45 · 45/45 · 45/45 | 0/25 · 0/25 · 0/25 | 0 · 0 · 0 |

## Тікети, на яких прогони розходяться

| Модель | Набір | # | Різні відповіді |
|---|---|---|---|
| openai/gpt-oss-120b | risk_holdout | 141 | — / account_change_request |
| openai/gpt-oss-120b | risk_holdout | 145 | social_engineering, threat_to_others / threat_to_others |
| openai/gpt-oss-120b | risk_holdout | 149 | account_compromised, data_exposure / data_exposure |
| openai/gpt-oss-120b | risk_holdout | 155 | crisis / crisis, unauthorized_charge |
| openai/gpt-oss-120b | risk_holdout | 162 | account_compromised / account_compromised, unauthorized_charge |
| openai/gpt-oss-120b | risk_holdout | 172 | customer_endangered / customer_endangered, unauthorized_charge |
| openai/gpt-oss-120b | risk_holdout | 173 | account_compromised, customer_endangered / customer_endangered |
| openai/gpt-oss-120b | risk_holdout | 174 | account_change_request, account_compromised, customer_endangered, data_exposure / account_compromised, customer_endangered, data_exposure |
| openai/gpt-oss-120b | risk_set | 11 | legal_threat / legal_threat, unauthorized_charge |
| openai/gpt-oss-120b | risk_set | 18 | account_compromised / account_compromised, data_exposure |
| openai/gpt-oss-20b | risk_holdout | 170 | account_compromised, crisis, customer_endangered, data_exposure / account_compromised, customer_endangered, data_exposure |
| openai/gpt-oss-20b | risk_holdout | 185 | — / unauthorized_charge |
| openai/gpt-oss-20b | risk_set | 18 | account_compromised / account_compromised, data_exposure |
| openai/gpt-oss-20b | risk_set | 68 | account_compromised, customer_endangered / account_compromised, customer_endangered, data_exposure |
