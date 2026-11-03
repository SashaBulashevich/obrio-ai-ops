# Зведення прогонів (risk_v4)

Кожен прогін — окремий запуск моделі на всьому наборі. Повних прогонів = без жодного тікета без відповіді.

| Модель | Набір | Прогонів | Знайдено всі ризики (кожен прогін) | Хибні тривоги (кожен прогін) | Без відповіді |
|---|---|---|---|---|---|
| openai/gpt-oss-120b | risk_holdout | 3 | 44/44 · 44/44 · 44/44 | 1/33 · 2/33 · 1/33 | 0 · 0 · 0 |
| openai/gpt-oss-120b | risk_set | 3 | 45/45 · 45/45 · 45/45 | 0/25 · 0/25 · 1/25 | 0 · 0 · 0 |
| openai/gpt-oss-20b | risk_holdout | 3 | 43/44 · 43/44 · 43/44 | 1/33 · 1/33 · 1/33 | 0 · 0 · 0 |
| openai/gpt-oss-20b | risk_set | 3 | 44/45 · 44/45 · 44/45 | 0/25 · 0/25 · 0/25 | 0 · 0 · 0 |
| qwen/qwen3.8-27b | risk_holdout | 3 | 44/44 · 44/44 · 44/44 | 0/33 · 0/33 · 0/33 | 0 · 0 · 0 |
| qwen/qwen3.8-27b | risk_set | 3 | 45/45 · 45/45 · 45/45 | 0/25 · 0/25 · 0/25 | 0 · 0 · 0 |

## Тікети, на яких прогони розходяться

| Модель | Набір | # | Різні відповіді |
|---|---|---|---|
| openai/gpt-oss-120b | risk_holdout | 145 | social_engineering, threat_to_others / threat_to_others |
| openai/gpt-oss-120b | risk_holdout | 149 | account_compromised, data_exposure / data_exposure |
| openai/gpt-oss-120b | risk_holdout | 151 | — / unauthorized_charge |
| openai/gpt-oss-120b | risk_holdout | 160 | customer_endangered, expert_fraud / expert_fraud |
| openai/gpt-oss-120b | risk_holdout | 173 | account_change_request, account_compromised, customer_endangered / customer_endangered |
| openai/gpt-oss-120b | risk_holdout | 174 | account_change_request, account_compromised, customer_endangered / account_compromised, customer_endangered, data_exposure |
| openai/gpt-oss-120b | risk_set | 18 | account_compromised / account_compromised, data_exposure |
| openai/gpt-oss-120b | risk_set | 55 | — / data_exposure |
| openai/gpt-oss-20b | risk_holdout | 108 | account_compromised, social_engineering / social_engineering |
| openai/gpt-oss-20b | risk_holdout | 131 | account_compromised, unauthorized_charge / unauthorized_charge |
| openai/gpt-oss-20b | risk_holdout | 143 | expert_fraud, threat_to_others / expert_fraud, threat_to_others, unauthorized_charge |
| openai/gpt-oss-20b | risk_holdout | 146 | account_compromised, customer_endangered / account_compromised, customer_endangered, data_exposure |
| openai/gpt-oss-20b | risk_holdout | 157 | customer_endangered, threat_to_others / threat_to_others |
| openai/gpt-oss-20b | risk_holdout | 170 | account_compromised, crisis, customer_endangered, data_exposure / account_compromised, customer_endangered, data_exposure |
| openai/gpt-oss-20b | risk_holdout | 171 | account_compromised, customer_endangered / account_compromised, customer_endangered, data_exposure |
| openai/gpt-oss-20b | risk_set | 11 | legal_threat / legal_threat, unauthorized_charge |
| openai/gpt-oss-20b | risk_set | 41 | crisis / crisis, unauthorized_charge |
