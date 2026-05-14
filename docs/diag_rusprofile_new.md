# Diag: новая механика Rusprofile (разведка 2026-05-14)

## Итог разведки

Rusprofile полностью заменил JSON-API `/ajax_auth.php?action=search_advanced`
на новый endpoint. Форма поиска и авторизация также изменились.

---

## 1. Поиск

### Endpoint

```
POST https://www.rusprofile.ru/ajax/search/advanced?cacheKey=<random>
Content-Type: application/json
X-Csrf-Token: <cookie __Host-csrf-token>
```

### Тело запроса

Rusprofile больше не принимает фильтры через URL-параметры — они игнорируются Vue.
Фильтры передаются **только** в теле POST-запроса:

```json
{
  "action": "search_advanced",
  "query": "",
  "state_1": true,
  "state_2": false,
  "state_3": false,
  "state_4": false,
  "state_5": false,
  "okved": ["46.49.3"],
  "okved_strict": true,
  "region": ["63"],
  "okopf": ["12165", "12300"],
  "page": "1"
}
```

Изменения по сравнению со старым `ajax_auth.php`:
- `state-N: true` → `state_N: true/false` (все 5 флагов обязательны в body)
- `page` — всё так же строкой `"1"`, `"2"`, ...

### Ответ

```json
{
  "success": true,
  "code": 0,
  "message": "OK",
  "data": {
    "items": [...],
    "query_display": "...",
    "ul_count": 123,
    "ip_count": 0,
    "fl_count": 0,
    "total_count": 123,
    "pagination": {...}
  }
}
```

Изменения по сравнению со старым ответом:
- `result[]` → `data.items[]`
- `total_count` теперь в `data.total_count` (не в корне)
- Добавлено поле `data.pagination`

### Поля items (подтверждены)

```
ref_type, name, raw_name, link, ogrn, raw_ogrn, inn, region, address,
inactive, status_extended, ceo_name, ceo_type, snippet_string, snippet_type,
main_okved_id, okved_descr, authorized_capital, finance_revenue,
finance_revenue_diff_type, finance_revenue_diff_value, finance_revenue_diff_percent,
reg_date, okpo, url, aci_id
```

### Механика в парсере

Vue автоматически делает POST при загрузке страницы `/search-advanced`.
Мы перехватываем его через `page.route("**/ajax/search/advanced**")` и
заменяем body через `route.continue_(post_data=new_body)`.

```python
# Старый роут:
await page.route("**/ajax_auth.php*", handle_route)

# Новый роут:
await page.route("**/ajax/search/advanced**", handle_route)
```

### Пагинация

`page: "N"` в теле POST. Каждая страница — новый `goto` + `requestSubmit`.
Размер страницы: **50** компаний.

---

## 2. Авторизация

### Старый способ (сломан)

Двухэтапная UI-форма через Vue-модалку не работает в headless — модалка
использует invisible reCAPTCHA и не монтируется без реального браузера.

### Новый способ (работает)

Прямой POST к `/auth.php?action=login`:

```
POST https://www.rusprofile.ru/auth.php?action=login
Content-Type: multipart/form-data   (через FormData)
X-Csrf-Token: <cookie __Host-csrf-token>
```

Body (FormData):
```
login=EMAIL
password=PASSWORD
```

Ответ при успехе:
```json
{"fields": {"hasPaidSubscription": true}, "success": true, "code": 0, "message": "OK"}
```

Браузер получает Set-Cookie с сессией автоматически.

---

## 3. Изменения в коде

| Файл | Что изменилось |
|------|----------------|
| `src/rusprofile/parser.py` | route: `**/ajax_auth.php*` → `**/ajax/search/advanced**`; ответ: `j.get("result")` → `j.get("data",{}).get("items")`; total: `j.get("total_count")` → `data.get("total_count")`; selector: `#state-1` → `#filter-form` |
| `src/rusprofile/filters.py` | `body[f"state-{code}"] = True` → все 5 флагов `state_N: bool` |
| `src/rusprofile/auth.py` | `_login` полностью переписан на прямой POST к `/auth.php?action=login` через `page.evaluate(fetch(...))` |

---

## 4. Что НЕ изменилось

- Структура карточек `list-element` в DOM (для `_parse_company_card`)
- Детальная страница `/id/N` (для `enrich_company_details`)
- `build_search_url` в `filters.py` (используется только для UI-display)
- Все поля `SearchFilters`
- Контракт `parse_search_results` / `Company`
