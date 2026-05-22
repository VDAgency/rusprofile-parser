// Telegram Web App SDK
const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

// Регионы РФ (код → название). Коды подтверждены сабмитом формы
// /search-advanced (см. scripts/diag_filters_final.py).
const REGIONS = {
    "97,77": "Москва",
    "78": "Санкт-Петербург",
    "50": "Московская область",
    "47": "Ленинградская область",
    "23": "Краснодарский край",
    "16": "Республика Татарстан",
    "52": "Нижегородская область",
    "66": "Свердловская область",
    "63": "Самарская область",
    "61": "Ростовская область",
    "74": "Челябинская область",
    "54": "Новосибирская область",
    "02": "Республика Башкортостан",
    "81,59": "Пермский край",
    "36": "Воронежская область",
    "34": "Волгоградская область",
    "38": "Иркутская область",
    "24": "Красноярский край",
    "55": "Омская область",
    "26": "Ставропольский край",
    "31": "Белгородская область",
    "73": "Ульяновская область",
    "33": "Владимирская область",
    "72": "Тюменская область",
    "64": "Саратовская область",
    "58": "Пензенская область",
    "56": "Оренбургская область",
    "71": "Тульская область",
    "62": "Рязанская область",
    "43": "Кировская область",
    "39": "Калининградская область",
    "76": "Ярославская область",
    "40": "Калужская область",
    "69": "Тверская область",
    "46": "Курская область",
    "48": "Липецкая область",
    "37": "Ивановская область",
    "32": "Брянская область",
    "57": "Орловская область",
    "67": "Смоленская область",
    "68": "Тамбовская область",
    "44": "Костромская область",
    "35": "Вологодская область",
    "29": "Архангельская область",
    "51": "Мурманская область",
    "10": "Республика Карелия",
    "11": "Республика Коми",
    "60": "Псковская область",
    "53": "Новгородская область",
    "30": "Астраханская область",
    "01": "Республика Адыгея",
    "08": "Республика Калмыкия",
    "91": "Республика Крым",
    "92": "Севастополь",
    "05": "Республика Дагестан",
    "06": "Республика Ингушетия",
    "07": "Кабардино-Балкарская Республика",
    "09": "Карачаево-Черкесская Республика",
    "15": "Республика Северная Осетия",
    "20": "Чеченская Республика",
    "22": "Алтайский край",
    "04": "Республика Алтай",
    "03": "Республика Бурятия",
    "75,80": "Забайкальский край",
    "70": "Томская область",
    "42": "Кемеровская область",
    "19": "Республика Хакасия",
    "17": "Республика Тыва",
    "14": "Республика Саха (Якутия)",
    "25": "Приморский край",
    "27": "Хабаровский край",
    "28": "Амурская область",
    "41,82": "Камчатский край",
    "49": "Магаданская область",
    "65": "Сахалинская область",
    "79": "Еврейская автономная область",
    "87": "Чукотский автономный округ",
    "86": "Ханты-Мансийский АО",
    "89": "Ямало-Ненецкий АО",
    "83": "Ненецкий автономный округ",
    "12": "Республика Марий Эл",
    "13": "Республика Мордовия",
    "18": "Удмуртская Республика",
    "21": "Чувашская Республика",
    "45": "Курганская область",
    "99": "Байконур",
};

function populateRegions() {
    const select = document.getElementById('region');
    const sorted = Object.entries(REGIONS).sort((a, b) =>
        a[1].localeCompare(b[1], 'ru')
    );
    sorted.forEach(([code, name]) => {
        const option = document.createElement('option');
        option.value = code;
        option.textContent = name;
        select.appendChild(option);
    });
}

function populateYandexRegions() {
    const select = document.getElementById('yandex_region');
    if (!select) return;
    const names = Object.values(REGIONS).sort((a, b) => a.localeCompare(b, 'ru'));
    names.forEach((name) => {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name;
        select.appendChild(option);
    });
}

function setupTabs() {
    const tabs = document.querySelectorAll('.source-tabs .tab');
    const sections = {
        rusprofile: document.getElementById('searchForm'),
        yandex_maps: document.getElementById('yandexForm'),
    };
    tabs.forEach((tab) => {
        tab.addEventListener('click', () => {
            tabs.forEach((t) => t.classList.remove('active'));
            tab.classList.add('active');
            const source = tab.dataset.source;
            Object.entries(sections).forEach(([key, el]) => {
                if (!el) return;
                if (key === source) {
                    el.classList.remove('hidden');
                } else {
                    el.classList.add('hidden');
                }
            });
        });
    });
}

function collectCheckboxGroup(name) {
    return Array.from(
        document.querySelectorAll(`input[type=checkbox][name="${name}"]:checked`)
    ).map(el => el.value);
}

function val(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    const v = (el.value || '').trim();
    return v === '' ? null : v;
}

function checked(id) {
    const el = document.getElementById(id);
    return !!(el && el.checked);
}

// =====================================================================
// ОКВЭД: справочник, поиск, chips, пресеты
// =====================================================================

const OKVED = {
    items: [],
    byCode: new Map(),
    childrenOf: new Map(),     // code -> [child codes]
    presets: [],
    selected: new Set(),       // выбранные коды
};

async function loadOkvedHandbook() {
    const [handbookResp, targetsResp] = await Promise.all([
        fetch('okved.json', { cache: 'no-cache' }),
        fetch('okved_targets.json', { cache: 'no-cache' }),
    ]);
    if (!handbookResp.ok) throw new Error('okved.json не найден');
    if (!targetsResp.ok) throw new Error('okved_targets.json не найден');

    const handbook = await handbookResp.json();
    const targets = await targetsResp.json();

    OKVED.items = handbook.items || [];
    OKVED.byCode = new Map(OKVED.items.map(it => [it.code, it]));

    // Индекс детей: code → массив дочерних кодов.
    OKVED.childrenOf = new Map();
    for (const it of OKVED.items) {
        if (!it.parent) continue;
        if (!OKVED.childrenOf.has(it.parent)) OKVED.childrenOf.set(it.parent, []);
        OKVED.childrenOf.get(it.parent).push(it.code);
    }

    OKVED.presets = targets.presets || [];

    console.log(`ОКВЭД: загружено ${OKVED.items.length} записей, ${OKVED.presets.length} пресетов`);
}

const TOKEN_RE = /[\wа-яёА-ЯЁ\-./]+/gu;

function tokenize(text) {
    if (!text) return [];
    return (text.match(TOKEN_RE) || []).map(t => t.toLowerCase());
}

function commonPrefixLen(a, b) {
    const n = Math.min(a.length, b.length);
    let i = 0;
    while (i < n && a[i] === b[i]) i++;
    return i;
}

function scoreItem(item, queryTokens, rawQuery) {
    if (!queryTokens.length) return 0;

    const name = (item.name || '').toLowerCase();
    const description = (item.description || '').toLowerCase();
    const code = (item.code || '').toLowerCase();
    const keywords = (item.keywords || []).map(k => String(k).toLowerCase());
    const keywordWords = new Set();
    for (const kw of keywords) for (const w of kw.split(/\s+/)) keywordWords.add(w);

    let score = 0;

    const raw = rawQuery.trim().toLowerCase();
    if (raw && raw.length >= 3) {
        if (keywords.some(kw => kw === raw)) score += 12;
        else if (keywords.some(kw => kw.includes(raw))) score += 4;
    }

    for (const token of queryTokens) {
        if (!token || token.length < 2) continue;

        if (code === token) {
            score += 15;
            continue;
        }
        if (code && code.startsWith(token + '.')) score += 4;

        if (keywords.some(kw => kw === token)) {
            score += 10;
        } else if (keywordWords.has(token)) {
            score += 5;
        } else if (token.length >= 4) {
            const threshold = Math.max(4, token.length - 2);
            for (const w of keywordWords) {
                if (w.length < threshold) continue;
                if (commonPrefixLen(token, w) >= threshold) {
                    score += 3;
                    break;
                }
            }
        }

        if (description.includes(token)) score += 2;
        if (name.includes(token)) score += 1;
    }

    if (score <= 0) return 0;

    if (item.is_leaf) score += 2;
    const level = Number(item.level) || 0;
    if (level >= 5) score += 1;
    else if (level === 4) score += 0.5;

    return score;
}

function searchOkved(query, limit = 12) {
    const q = (query || '').trim();
    if (!q) return [];
    const tokens = tokenize(q);
    if (!tokens.length) return [];

    const scored = [];
    for (const item of OKVED.items) {
        // По ТЗ: разрешаем выбирать только уровни 2–6 (классы и ниже).
        if ((item.level || 0) < 2) continue;
        const s = scoreItem(item, tokens, q);
        if (s > 0) scored.push([s, item]);
    }
    scored.sort((a, b) => b[0] - a[0] || (a[1].code || '').localeCompare(b[1].code || ''));
    return scored.slice(0, limit).map(([, it]) => it);
}

function expandChildren(code) {
    const result = [];
    if (!OKVED.byCode.has(code)) return result;
    result.push(code);
    const queue = [code];
    const seen = new Set([code]);
    while (queue.length) {
        const cur = queue.shift();
        const ch = OKVED.childrenOf.get(cur) || [];
        for (const c of ch) {
            if (!seen.has(c)) {
                seen.add(c);
                result.push(c);
                queue.push(c);
            }
        }
    }
    return result;
}

function addOkvedCode(code, withChildren = false) {
    if (!code || !OKVED.byCode.has(code)) return;
    if (withChildren) {
        for (const c of expandChildren(code)) {
            const item = OKVED.byCode.get(c);
            // Пропускаем разделы уровня 1 — они не валидны для поиска.
            if (item && (item.level || 0) >= 2) OKVED.selected.add(c);
        }
    } else {
        OKVED.selected.add(code);
    }
    renderChips();
    rerenderSuggest();
    updateValidation();
}

function removeOkvedCode(code) {
    OKVED.selected.delete(code);
    renderChips();
    rerenderSuggest();
    updateValidation();
}

function renderChips() {
    const container = document.getElementById('okvedChips');
    if (!container) return;
    container.innerHTML = '';
    for (const code of OKVED.selected) {
        const item = OKVED.byCode.get(code);
        if (!item) continue;
        const chip = document.createElement('span');
        chip.className = 'chip';
        chip.title = item.name || '';
        chip.innerHTML = `
            <span class="chip-code">${code}</span>
            <span class="chip-name">${escapeHtml(shortName(item.name))}</span>
            <span class="chip-remove" data-code="${code}">×</span>
        `;
        chip.querySelector('.chip-remove').addEventListener('click', (e) => {
            e.stopPropagation();
            removeOkvedCode(code);
        });
        container.appendChild(chip);
    }
}

function shortName(name) {
    if (!name) return '';
    return name.length > 50 ? name.slice(0, 50) + '…' : name;
}

function escapeHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function renderSuggest(items) {
    const container = document.getElementById('okvedSuggest');
    if (!container) return;
    container.innerHTML = '';
    for (const item of items) {
        const isSelected = OKVED.selected.has(item.code);
        const hasChildren = (OKVED.childrenOf.get(item.code) || []).length > 0;

        const div = document.createElement('div');
        div.className = 'suggest-item' + (isSelected ? ' selected' : '');
        div.innerHTML = `
            <div>
                <span class="suggest-code">${item.code}</span>
                <span class="suggest-name">${escapeHtml(item.name || '')}</span>
            </div>
            ${item.description ? `<div class="suggest-desc">${escapeHtml(item.description)}</div>` : ''}
            ${hasChildren && !isSelected ? `<button type="button" class="suggest-add-children" data-code="${item.code}">+ с подгруппами</button>` : ''}
        `;
        if (!isSelected) {
            div.addEventListener('click', (e) => {
                if (e.target.classList.contains('suggest-add-children')) {
                    e.stopPropagation();
                    addOkvedCode(item.code, true);
                } else {
                    addOkvedCode(item.code, false);
                }
            });
        }
        container.appendChild(div);
    }
}

function rerenderSuggest() {
    const search = document.getElementById('okvedSearch');
    if (!search) return;
    const q = (search.value || '').trim();
    if (!q) {
        document.getElementById('okvedSuggest').innerHTML = '';
        return;
    }
    renderSuggest(searchOkved(q, 12));
}

function setupOkvedSearch() {
    const input = document.getElementById('okvedSearch');
    if (!input) return;
    let timer = null;
    input.addEventListener('input', () => {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => rerenderSuggest(), 150);
    });
}

// ----- Пресеты -----------------------------------------------------------

function applyPreset(preset) {
    if (!preset) return;
    OKVED.selected.clear();
    for (const code of (preset.codes || [])) {
        if (OKVED.byCode.has(code)) OKVED.selected.add(code);
    }
    const rec = preset.recommended_filters || {};

    const setCheck = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.checked = !!value;
    };
    const setVal = (id, value) => {
        const el = document.getElementById(id);
        if (el && value !== undefined && value !== null) el.value = String(value);
    };

    if ('has_phones' in rec) setCheck('has_phones', rec.has_phones);
    if ('has_emails' in rec) setCheck('has_emails', rec.has_emails);
    if ('has_sites' in rec) setCheck('has_sites', rec.has_sites);
    if ('finance_has_actual_year_data' in rec) setCheck('finance_has_actual_year_data', rec.finance_has_actual_year_data);
    if ('not_defendant' in rec) setCheck('not_defendant', rec.not_defendant);
    if ('finance_revenue_from' in rec) setVal('finance_revenue_from', rec.finance_revenue_from);
    if ('finance_revenue_to' in rec) setVal('finance_revenue_to', rec.finance_revenue_to);

    // МСП — это группа чекбоксов с name="msp"
    if (Array.isArray(rec.msp)) {
        const wanted = new Set(rec.msp);
        document.querySelectorAll('input[type=checkbox][name="msp"]').forEach(el => {
            el.checked = wanted.has(el.value);
        });
    }

    renderChips();
    rerenderSuggest();
    updateValidation();
    document.getElementById('presetsPanel').classList.add('hidden');

    if (typeof tg.HapticFeedback !== 'undefined' && tg.HapticFeedback.notificationOccurred) {
        tg.HapticFeedback.notificationOccurred('success');
    }
}

function renderPresetsPanel() {
    const panel = document.getElementById('presetsPanel');
    if (!panel) return;
    panel.innerHTML = '';
    for (const preset of OKVED.presets) {
        const div = document.createElement('div');
        div.className = 'preset-item';
        div.innerHTML = `
            <div class="preset-title">${escapeHtml(preset.title || preset.id)}</div>
            <div class="preset-desc">${escapeHtml(preset.description || '')}</div>
            ${preset.notes ? `<div class="preset-notes">${escapeHtml(preset.notes)}</div>` : ''}
        `;
        div.addEventListener('click', () => applyPreset(preset));
        panel.appendChild(div);
    }
}

function setupPresetsButton() {
    const btn = document.getElementById('presetsBtn');
    const panel = document.getElementById('presetsPanel');
    if (!btn || !panel) return;
    btn.addEventListener('click', () => panel.classList.toggle('hidden'));
}

// ----- Валидация ---------------------------------------------------------

function hasOtherSignificantFilters(data) {
    if (data.query) return true;
    if (data.region && data.region.length) return true;
    for (const k of [
        'finance_revenue_from', 'finance_revenue_to',
        'finance_profit_from', 'finance_profit_to',
        'sshr_from', 'sshr_to',
        'capital_from', 'capital_to',
    ]) {
        if (data[k]) return true;
    }
    if (data.msp && data.msp.length) return true;
    if (data.okopf && data.okopf.length) return true;
    if (data.has_phones || data.has_sites || data.has_emails) return true;
    if (data.finance_has_actual_year_data || data.not_defendant) return true;
    return false;
}

function updateValidation() {
    const group = document.getElementById('okvedGroup');
    const errorEl = document.getElementById('okvedError');
    if (!group || !errorEl) return;
    const data = getFormData();
    const hasOkved = OKVED.selected.size > 0;
    const hasOther = hasOtherSignificantFilters(data);
    const ok = hasOkved || hasOther;
    if (!ok) {
        group.classList.add('has-error');
        errorEl.classList.remove('hidden');
    } else {
        group.classList.remove('has-error');
        errorEl.classList.add('hidden');
    }
    return ok;
}

// =====================================================================
// Сабмит
// =====================================================================

function clampMaxNew(raw) {
    const n = parseInt(raw, 10);
    if (!n || n < 1) return 100;
    if (n > 300) return 300;
    return n;
}

function getFormData() {
    const region = val('region');
    return {
        query: val('query'),
        region: region ? [region] : [],
        okved: Array.from(OKVED.selected),
        okved_strict: !checked('okved_loose'),
        status: collectCheckboxGroup('status'),
        okopf: collectCheckboxGroup('okopf'),
        msp: collectCheckboxGroup('msp'),
        finance_revenue_from: val('finance_revenue_from'),
        finance_revenue_to: val('finance_revenue_to'),
        finance_profit_from: val('finance_profit_from'),
        finance_profit_to: val('finance_profit_to'),
        sshr_from: val('sshr_from'),
        sshr_to: val('sshr_to'),
        capital_from: val('capital_from'),
        capital_to: val('capital_to'),
        has_phones: checked('has_phones'),
        has_sites: checked('has_sites'),
        has_emails: checked('has_emails'),
        finance_has_actual_year_data: checked('finance_has_actual_year_data'),
        not_defendant: checked('not_defendant'),
        max_new: clampMaxNew(val('max_new')),
    };
}

// Telegram Web НЕ закрывает Mini App автоматически после sendData
// (на Desktop/Mobile закрывает) — старая сессия с тем же initData остаётся
// открытой, и второй submit зависает. Явный tg.close() решает проблему:
// при следующем открытии Telegram выдаст новый initData.
function _submitAndClose(payload) {
    tg.sendData(JSON.stringify(payload));
    try { tg.close(); } catch (_) { /* старые версии WebApp API */ }
}

function submitForm(e) {
    e.preventDefault();
    if (!updateValidation()) {
        tg.showAlert('Выберите ОКВЭД из справочника или добавьте другие фильтры.');
        return;
    }
    const data = getFormData();
    data.source = 'rusprofile';
    // Этап 2 v3 — ИИ-квалификация и кросс-обогащение.
    const aiProfile = val('rusprofile_ai_profile');
    if (aiProfile) data.ai_profile_id = parseInt(aiProfile, 10);
    data.enable_cross_enrichment = checked('rusprofile_cross_enrich');
    _submitAndClose(data);
}

function submitYandexForm(e) {
    e.preventDefault();
    const region = val('yandex_region');
    const category = val('yandex_category');
    const maxNew = clampMaxNew(val('yandex_max_new'));

    if (!region) {
        tg.showAlert('Выберите регион.');
        return;
    }
    if (!category) {
        tg.showAlert('Укажите вид деятельности (например: стоматологии, кафе).');
        return;
    }

    const payload = {
        source: 'yandex_maps',
        region: region,
        category: category,
        max_new: maxNew,
    };
    const aiProfile = val('yandex_ai_profile');
    if (aiProfile) payload.ai_profile_id = parseInt(aiProfile, 10);
    payload.enable_cross_enrichment = checked('yandex_cross_enrich');

    _submitAndClose(payload);
}

// =====================================================================
// init
// =====================================================================

// =====================================================================
// Bottom-nav: переключение «Парсинг» / «История»
// =====================================================================

function setupBottomNav() {
    const buttons = document.querySelectorAll('.bottom-nav .nav-btn');
    buttons.forEach((btn) => {
        btn.addEventListener('click', () => {
            const target = btn.dataset.page;
            buttons.forEach((b) => b.classList.toggle('active', b === btn));
            document.querySelectorAll('.page').forEach((p) => {
                p.classList.toggle('hidden', p.id !== `page-${target}`);
            });
            if (target === 'history') {
                loadHistory();
            } else if (target === 'profiles') {
                loadProfilesList();
            } else if (target === 'cabinet') {
                loadCabinet();
            }
        });
    });
}

function switchToPage(targetPage) {
    /** Программное переключение страницы (для кнопок «Открыть кабинет»
     *  внутри баннеров). */
    const btn = document.querySelector(
        `.bottom-nav .nav-btn[data-page="${targetPage}"]`
    );
    if (btn) btn.click();
}

// =====================================================================
// История запусков
// =====================================================================

let historyLoaded = false;

function getUnsafeUid() {
    // 1) Стандартный путь — Telegram передал user через initDataUnsafe.
    if (tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.id) {
        return String(tg.initDataUnsafe.user.id);
    }
    // 2) Обходной путь: бот подложил user_id в URL Mini App
    //    (когда has_main_web_app=false и Telegram не передаёт user).
    try {
        const urlUid = new URLSearchParams(window.location.search).get('uid');
        if (urlUid && /^\d+$/.test(urlUid)) {
            return urlUid;
        }
    } catch (_) { /* ignore */ }
    // 3) start_param из Telegram (если запущен через t.me/<bot>?startapp=<uid>).
    if (tg.initDataUnsafe && tg.initDataUnsafe.start_param
        && /^\d+$/.test(tg.initDataUnsafe.start_param)) {
        return tg.initDataUnsafe.start_param;
    }
    return null;
}

// Передаём ВСЁ через query-параметры, без кастомных заголовков —
// иначе Telegram WebView на части устройств блокирует запрос (CORS
// preflight, плюс некоторые сборки режут кастомные заголовки до
// отправки). Простой GET с query → запрос точно уходит.
function apiUrl(path) {
    const sep = path.includes('?') ? '&' : '?';
    const params = [];
    if (tg.initData) {
        params.push('init_data=' + encodeURIComponent(tg.initData));
    }
    const uid = getUnsafeUid();
    if (uid) {
        params.push('uid=' + encodeURIComponent(uid));
    }
    if (!params.length) return path;
    return path + sep + params.join('&');
}

// Без кастомных заголовков. Только Accept и максимально стандартный
// fetch — никаких triggers для CORS preflight.
function apiFetchOptions(extra) {
    return Object.assign({
        cache: 'no-store',
        credentials: 'omit',
    }, extra || {});
}

// Одноразовая диагностика при первом 401 — пользователь увидит alert
// со всеми ключевыми параметрами (user.id, длина initData, версия
// Telegram), чтобы можно было прислать скрин в поддержку.
let diagnosticsShown = false;
function showAuthDiagnostics() {
    if (diagnosticsShown) return;
    diagnosticsShown = true;
    const initLen = (tg.initData || '').length;
    const uid = getUnsafeUid();
    const userObj = tg.initDataUnsafe && tg.initDataUnsafe.user
        ? JSON.stringify(tg.initDataUnsafe.user) : '(нет)';
    const platform = tg.platform || '(нет)';
    const version = tg.version || '(нет)';
    const lines = [
        'Диагностика авторизации:',
        `• tg.initData длина: ${initLen}`,
        `• tg.initDataUnsafe.user: ${userObj}`,
        `• Получен ли uid: ${uid || 'НЕТ'}`,
        `• Платформа: ${platform}`,
        `• Версия Telegram WebApp: ${version}`,
    ];
    tg.showAlert(lines.join('\n'));
}

async function loadHistory(force = false) {
    const statusEl = document.getElementById('history-status');
    const listEl = document.getElementById('history-list');
    if (!statusEl || !listEl) return;

    if (historyLoaded && !force) return;

    statusEl.textContent = 'Загрузка…';
    listEl.innerHTML = '';

    try {
        const resp = await fetch(apiUrl('/api/history?limit=50'), apiFetchOptions());
        if (resp.status === 401) {
            const initLen = (tg.initData || '').length;
            const uid = getUnsafeUid();
            statusEl.innerHTML = (
                'Не удалось проверить авторизацию Telegram.<br>' +
                `<small>initData length: ${initLen}, user.id: ${uid || '—'}</small><br>` +
                '<small>Нажмите кнопку «Подробная диагностика» и пришлите скриншот.</small>' +
                '<br><br><button type="button" class="btn-mini" id="diag-btn">Подробная диагностика</button>'
            );
            const diagBtn = document.getElementById('diag-btn');
            if (diagBtn) diagBtn.addEventListener('click', () => {
                diagnosticsShown = false; showAuthDiagnostics();
            });
            return;
        }
        if (!resp.ok) {
            statusEl.textContent = 'Ошибка загрузки истории.';
            return;
        }
        const data = await resp.json();
        const runs = data.runs || [];
        if (!runs.length) {
            statusEl.textContent = 'Запусков пока нет. Запустите парсинг — они появятся здесь.';
            return;
        }
        statusEl.textContent = `Найдено запусков: ${runs.length}`;
        listEl.innerHTML = runs.map(renderRunCard).join('');
        attachRunActions();
        historyLoaded = true;
    } catch (err) {
        console.error(err);
        statusEl.textContent = 'Сеть недоступна или сервер не отвечает.';
    }
}

function renderRunCard(run) {
    const date = run.finished_at || run.started_at || '';
    const dateStr = date ? new Date(date).toLocaleString('ru-RU') : '';
    const sourceLabel = run.source === 'yandex_maps' ? 'Яндекс.Карты' : 'Rusprofile';
    const stats = run.status === 'error'
        ? `<span class="run-status-error">Ошибка: ${escapeHtml(run.theme_filters?.error || 'не выполнен')}</span>`
        : `Новых: <b>${run.total_new}</b> · пропущено: ${run.total_skipped}`;

    const sheetBtn = run.sheet_url
        ? `<a class="btn-mini" href="${escapeHtml(run.sheet_url)}" target="_blank" rel="noopener">Открыть таблицу</a>`
        : '';

    // Этап 2 v3 — блок ИИ-метрик, если квалификация выполнялась.
    let aiBlock = '';
    if (run.ai_qualify_stats) {
        const s = run.ai_qualify_stats.by_status || {};
        const tokens = run.ai_qualify_stats.tokens_used_total || 0;
        const cost = run.ai_qualify_stats.cost_rub_estimate || 0;
        const xMatches = run.ai_qualify_stats.cross_enrichment_matches || {};
        const xTotal = (xMatches.yandex_found || 0) + (xMatches.rusprofile_found || 0)
                       + (xMatches.both || 0);
        aiBlock = `
            <div class="run-ai">
                🔥 ${s.hot || 0} · ❄ ${s.cold || 0} · ⏭ ${s.skip || 0} · ❓ ${s.unknown || 0}
                ${s.quota_exceeded ? `· 🚫 ${s.quota_exceeded}` : ''}
                ${tokens ? `<br><small>Токенов: ${tokens.toLocaleString('ru-RU')} (~${cost.toFixed(2)} ₽)</small>` : ''}
                ${xTotal ? `<br><small>Кросс-обогащение: ${xTotal} матчей</small>` : ''}
            </div>
        `;
    }
    // Кнопка квалификации — для всех, у кого включён ИИ (PRO / legacy AI / dev).
    const qualifyBtn = aiAvailable
        ? `<button type="button" class="btn-mini" data-action="qualify">
               ${run.ai_qualify_stats ? 'Перезапустить ИИ' : 'Запустить ИИ'}
           </button>`
        : '';

    return `
        <div class="run-card" data-run-id="${run.id}">
            <div class="run-head">
                <span class="run-source">${escapeHtml(sourceLabel)}</span>
                <span class="run-date">${escapeHtml(dateStr)}</span>
            </div>
            <div class="run-title">${escapeHtml(run.theme_title || '(без заголовка)')}</div>
            <div class="run-stats">${stats}</div>
            ${aiBlock}
            <div class="run-actions">
                ${sheetBtn}
                <button type="button" class="btn-mini" data-action="repush">Перезалить в Sheets</button>
                <button type="button" class="btn-mini primary" data-action="xlsx">Скачать Excel</button>
                ${qualifyBtn}
            </div>
        </div>
    `;
}

function attachRunActions() {
    document.querySelectorAll('.run-card').forEach((card) => {
        const runId = card.dataset.runId;
        card.querySelectorAll('[data-action]').forEach((btn) => {
            btn.addEventListener('click', () => onRunAction(runId, btn.dataset.action, btn));
        });
    });
}

async function onRunAction(runId, action, btn) {
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = '…';
    try {
        if (action === 'repush') {
            const resp = await fetch(
                apiUrl(`/api/runs/${runId}/repush`),
                apiFetchOptions({ method: 'POST' }),
            );
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                tg.showAlert(data.error || 'Не удалось перезалить.');
                return;
            }
            tg.showAlert(`Готово. Перезаписано ${data.exported} компаний в Sheets.`);
        } else if (action === 'xlsx') {
            // Telegram WebView не умеет скачивать binary через <a download>.
            // Просим сервер отправить файл напрямую сообщением в чат —
            // пользователь получит документ через бота и Telegram сам
            // даст стандартные кнопки «Открыть / Сохранить».
            const resp = await fetch(
                apiUrl(`/api/runs/${runId}/send-xlsx`),
                apiFetchOptions({ method: 'POST' }),
            );
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                tg.showAlert(data.error || 'Не удалось отправить Excel.');
                return;
            }
            tg.showAlert(
                'Excel-файл отправлен сообщением в чат с ботом — '
                + 'откройте чат, чтобы скачать.'
            );
            // Закрыть Mini App, чтобы пользователь сразу увидел файл
            // в чате. Если закрытие нежелательно (он хочет ещё что-то
            // сделать в истории), можно убрать.
            // tg.close();
        } else if (action === 'qualify') {
            // Этап 2 v3 — запуск ИИ-квалификации.
            // Спрашиваем профиль если есть несколько.
            let profileId = '';
            if (cachedProfiles.length === 0) {
                tg.showAlert('Сначала создайте ИИ-профиль в разделе «ИИ-профили».');
                return;
            } else if (cachedProfiles.length === 1) {
                profileId = cachedProfiles[0].id;
            } else {
                const choice = prompt(
                    'Выберите профиль (введите номер):\n'
                    + cachedProfiles.map((p, i) => `${i + 1}. ${p.name}`).join('\n')
                );
                const idx = parseInt(choice, 10) - 1;
                if (isNaN(idx) || idx < 0 || idx >= cachedProfiles.length) return;
                profileId = cachedProfiles[idx].id;
            }
            const resp = await fetch(
                apiUrl(`/api/runs/${runId}/qualify`),
                {
                    ...apiFetchOptions(),
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        profile_id: profileId,
                        force: true,
                        enable_cross_enrichment: true,
                    }),
                },
            );
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                tg.showAlert(data.error || data.message || 'Не удалось запустить.');
                return;
            }
            tg.showAlert(
                'Квалификация запущена в фоне. По завершении придёт сообщение в чат.'
            );
        }
    } catch (err) {
        console.error(err);
        tg.showAlert('Сеть недоступна.');
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
}

// =====================================================================
// Этап 2 v3 — Тариф, профили, ИИ-квалификация
// =====================================================================

let currentTariff = 'simple';
let aiAvailable = false;
let isDeveloper = false;
let cachedProfiles = [];

// Человеко-читаемые названия тарифов для плашки.
const TARIFF_LABELS = {
    simple: 'Simple',
    ai: 'AI',
    trial: 'Trial',
    trial_expired: 'Trial (истёк)',
    basic: 'Basic',
    pro: 'Pro',
};

async function loadTariff() {
    try {
        const resp = await fetch(apiUrl('/api/tariff'), apiFetchOptions());
        if (!resp.ok) return;
        const data = await resp.json();
        currentTariff = data.tariff_plan || 'simple';
        aiAvailable = Boolean(data.is_ai_available);
        isDeveloper = Boolean(data.is_developer);
        renderTariffBadge(data);
        applyTariffVisibility();
        if (aiAvailable) {
            await loadProfilesIntoSelects();
        }
    } catch (err) {
        console.error('Не удалось загрузить тариф:', err);
    }
}

function renderTariffBadge(t) {
    const el = document.getElementById('tariffBadge');
    if (!el) return;
    el.classList.remove('hidden');

    const label = TARIFF_LABELS[t.tariff_plan] || t.tariff_plan;
    const devMark = t.is_developer ? ' · dev' : '';

    if (t.is_ai_available) {
        const used = t.usage_current?.companies_processed || 0;
        const quota = t.quotas?.companies_monthly || 0;
        const pct = t.usage_current?.companies_percent || 0;
        const days = t.usage_period?.days_remaining ?? '—';
        // 0 = без лимита (dev / legacy без квоты).
        const usageStr = quota > 0
            ? `${used} / ${quota} (${pct}%)`
            : `${used} компаний · без лимита`;
        const bar = quota > 0
            ? `<div class="tariff-bar"><div class="tariff-bar-fill" style="width:${pct}%"></div></div>
               <div class="tariff-meta">Сброс через ${days} дн.</div>`
            : '';
        el.innerHTML = `
            <div class="tariff-row">
                <strong>Тариф: ${label}${devMark}</strong>
                <span class="tariff-pct">${usageStr}</span>
            </div>
            ${bar}
        `;
    } else {
        // Tariff без ИИ (Simple / Trial / Basic) — поясняем что доступно.
        const hint = t.tariff_plan === 'trial' || t.tariff_plan === 'basic'
            ? 'ИИ-квалификация — на тарифе Pro'
            : 'ИИ-квалификация не подключена';
        el.innerHTML = `
            <div class="tariff-row">
                <strong>Тариф: ${label}${devMark}</strong>
                <span class="tariff-pct">${hint}</span>
            </div>
        `;
    }
}

function applyTariffVisibility() {
    document.querySelectorAll('.ai-only').forEach((el) => {
        el.classList.toggle('hidden', !aiAvailable);
    });
}

async function loadProfilesIntoSelects() {
    try {
        const resp = await fetch(apiUrl('/api/profiles'), apiFetchOptions());
        if (!resp.ok) return;
        const data = await resp.json();
        cachedProfiles = data.profiles || [];
        ['rusprofile_ai_profile', 'yandex_ai_profile'].forEach((id) => {
            const sel = document.getElementById(id);
            if (!sel) return;
            const cur = sel.value;
            sel.innerHTML = '<option value="">Не использовать</option>'
                + cachedProfiles.map((p) =>
                    `<option value="${p.id}">${escapeHtml(p.name)}</option>`
                ).join('');
            if (cur) sel.value = cur;
        });
    } catch (err) {
        console.error('Не удалось загрузить профили:', err);
    }
}

// ─── Раздел «ИИ-профили»: список + двухшаговый UX ─────────────────────

let editingProfile = { brief: '', name: '', extracted: null };

async function loadProfilesList() {
    if (!aiAvailable) {
        document.getElementById('profiles-status').textContent =
            'Раздел доступен на тарифе Pro (или legacy AI).';
        return;
    }
    const status = document.getElementById('profiles-status');
    const list = document.getElementById('profiles-list');
    status.textContent = 'Загрузка…';
    list.innerHTML = '';
    try {
        const resp = await fetch(apiUrl('/api/profiles'), apiFetchOptions());
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        cachedProfiles = data.profiles || [];
        if (cachedProfiles.length === 0) {
            status.textContent = 'Профилей пока нет. Создайте первый.';
            return;
        }
        status.textContent = '';
        list.innerHTML = cachedProfiles.map((p) => `
            <div class="profile-card">
                <div class="profile-card-name">${escapeHtml(p.name)}</div>
                <div class="profile-card-meta">
                    Позитивных ключей: ${p.keywords_positive.length},
                    негативных: ${p.keywords_negative.length}
                </div>
                <div class="profile-card-meta">
                    ${escapeHtml((p.icp_description || '').slice(0, 120))}…
                </div>
                <div class="profile-card-actions">
                    <button type="button" class="btn-secondary" onclick="deleteProfile(${p.id})">
                        Удалить
                    </button>
                </div>
            </div>
        `).join('');
    } catch (err) {
        status.textContent = 'Ошибка загрузки: ' + err.message;
    }
}

window.deleteProfile = async function(id) {
    if (!confirm('Удалить профиль?')) return;
    try {
        const resp = await fetch(
            apiUrl(`/api/profiles/${id}`),
            { ...apiFetchOptions(), method: 'DELETE' },
        );
        if (resp.ok) {
            await loadProfilesList();
            await loadProfilesIntoSelects();
        } else {
            tg.showAlert('Не удалось удалить.');
        }
    } catch (err) {
        tg.showAlert('Сеть недоступна.');
    }
};

function setupProfileForms() {
    document.getElementById('profileNewBtn')?.addEventListener('click', () => {
        editingProfile = { brief: '', name: '', extracted: null };
        document.getElementById('profile_name').value = '';
        document.getElementById('profile_brief').value = '';
        document.getElementById('profileStep1').classList.remove('hidden');
        document.getElementById('profileStep2').classList.add('hidden');
        document.getElementById('profiles-list').classList.add('hidden');
        document.getElementById('profileNewBtn').classList.add('hidden');
    });
    document.getElementById('profileCancelBtn')?.addEventListener('click', resetProfileUI);
    document.getElementById('profileBackBtn')?.addEventListener('click', () => {
        document.getElementById('profileStep2').classList.add('hidden');
        document.getElementById('profileStep1').classList.remove('hidden');
    });
    document.getElementById('profileExtractBtn')?.addEventListener('click', extractProfile);
    document.getElementById('profileSaveBtn')?.addEventListener('click', saveProfile);

    // Добавление ключевых слов через Enter
    document.getElementById('profilePosAdd')?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            const v = e.target.value.trim();
            if (v) {
                editingProfile.extracted.keywords_positive.push(v);
                renderProfileChips();
                e.target.value = '';
            }
        }
    });
    document.getElementById('profileNegAdd')?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            const v = e.target.value.trim();
            if (v) {
                editingProfile.extracted.keywords_negative.push(v);
                renderProfileChips();
                e.target.value = '';
            }
        }
    });
}

function resetProfileUI() {
    document.getElementById('profileStep1').classList.add('hidden');
    document.getElementById('profileStep2').classList.add('hidden');
    document.getElementById('profiles-list').classList.remove('hidden');
    document.getElementById('profileNewBtn').classList.remove('hidden');
    loadProfilesList();
}

async function extractProfile() {
    const name = val('profile_name').trim();
    const brief = val('profile_brief').trim();
    if (!brief) {
        tg.showAlert('Опишите бриф (минимум — кого ищете).');
        return;
    }
    editingProfile.brief = brief;
    editingProfile.name = name || 'Без названия';

    const btn = document.getElementById('profileExtractBtn');
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'ИИ извлекает…';
    try {
        const resp = await fetch(apiUrl('/api/profiles/extract'), {
            ...apiFetchOptions(),
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ brief, name }),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            tg.showAlert(err.message || 'Ошибка извлечения профиля.');
            return;
        }
        const data = await resp.json();
        editingProfile.extracted = {
            icp_description: data.extracted.icp_description,
            semantic_criteria: data.extracted.semantic_criteria,
            keywords_positive: data.extracted.keywords_positive.slice(),
            keywords_negative: data.extracted.keywords_negative.slice(),
        };
        renderProfileStep2();
        document.getElementById('profileStep1').classList.add('hidden');
        document.getElementById('profileStep2').classList.remove('hidden');
    } catch (err) {
        tg.showAlert('Сеть недоступна.');
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
}

function renderProfileStep2() {
    const ex = editingProfile.extracted;
    document.getElementById('profileIcp').textContent = ex.icp_description;
    document.getElementById('profileCriteria').innerHTML =
        ex.semantic_criteria.map((c) => `<li>${escapeHtml(c)}</li>`).join('');
    renderProfileChips();
}

function renderProfileChips() {
    const ex = editingProfile.extracted;
    const renderChips = (containerId, arr, removeFn) => {
        const cont = document.getElementById(containerId);
        cont.innerHTML = arr.map((kw, i) => `
            <span class="profile-chip">
                ${escapeHtml(kw)}
                <button type="button" data-idx="${i}" class="chip-x">×</button>
            </span>
        `).join('');
        cont.querySelectorAll('.chip-x').forEach((b) => {
            b.addEventListener('click', () => {
                arr.splice(parseInt(b.dataset.idx, 10), 1);
                renderProfileChips();
            });
        });
    };
    renderChips('profilePosKws', ex.keywords_positive);
    renderChips('profileNegKws', ex.keywords_negative);
}

async function saveProfile() {
    const ex = editingProfile.extracted;
    const btn = document.getElementById('profileSaveBtn');
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = 'Сохраняю…';
    try {
        const resp = await fetch(apiUrl('/api/profiles'), {
            ...apiFetchOptions(),
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: editingProfile.name,
                brief: editingProfile.brief,
                icp_description: ex.icp_description,
                semantic_criteria: ex.semantic_criteria,
                keywords_positive: ex.keywords_positive,
                keywords_negative: ex.keywords_negative,
            }),
        });
        if (!resp.ok) {
            tg.showAlert('Не удалось сохранить профиль.');
            return;
        }
        await loadProfilesIntoSelects();
        resetProfileUI();
    } catch (err) {
        tg.showAlert('Сеть недоступна.');
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
}

// =====================================================================
// Этап 3 — Кабинет / биллинг (stub)
// =====================================================================

let cachedTariff = null;  // последний ответ от GET /api/tariff
let cachedPlans = null;   // последний ответ от GET /api/billing/plans

async function loadCabinet() {
    /** Подгружает /api/tariff + /api/billing/plans + /api/billing/subscription,
     *  рисует страницу Кабинета. */
    const status = document.getElementById('cabinet-status');
    const tariffEl = document.getElementById('cabinet-tariff');
    const plansEl = document.getElementById('cabinet-plans');
    const histEl = document.getElementById('cabinet-history');
    status.textContent = 'Загрузка…';
    tariffEl.classList.add('hidden');
    plansEl.classList.add('hidden');
    histEl.classList.add('hidden');

    try {
        const [tariffResp, plansResp, subResp] = await Promise.all([
            fetch(apiUrl('/api/tariff'), apiFetchOptions()),
            fetch(apiUrl('/api/billing/plans'), apiFetchOptions()),
            fetch(apiUrl('/api/billing/subscription'), apiFetchOptions()),
        ]);
        if (!tariffResp.ok) throw new Error('tariff ' + tariffResp.status);
        cachedTariff = await tariffResp.json();
        cachedPlans = plansResp.ok ? await plansResp.json() : { plans: [] };
        const sub = subResp.ok ? await subResp.json() : { active: null, history: [] };

        renderCabinetTariff(cachedTariff, sub);
        renderCabinetPlans(cachedPlans, cachedTariff);
        renderCabinetHistory(sub);

        status.textContent = '';
        tariffEl.classList.remove('hidden');
        plansEl.classList.remove('hidden');
        histEl.classList.remove('hidden');

        // Обновляем баннер блокировки на странице «Парсинг».
        updateParseBlockedBanner(cachedTariff);
    } catch (err) {
        console.error('loadCabinet failed:', err);
        status.textContent = 'Не удалось загрузить данные кабинета. Попробуйте позже.';
    }
}

function renderCabinetTariff(t, sub) {
    const el = document.getElementById('cabinet-tariff');
    const titleMap = {
        trial: 'Trial (бесплатно)',
        trial_expired: 'Trial закончился',
        basic: 'Basic',
        pro: 'Pro',
        simple: 'Basic (бессрочно)',  // legacy
        ai: 'Pro (бессрочно)',        // legacy
    };
    const title = titleMap[t.tariff_plan] || t.tariff_plan;

    let body = `<div class="cabinet-tariff-title">${escapeHtml(title)}</div>`;

    // Состояние Trial
    if (t.trial && t.trial.is_trial) {
        const daysLeft = t.trial.days_left ?? '—';
        const parsesLeft = t.trial.parses_left ?? '—';
        body += `
            <div class="cabinet-meter">
                ⏰ Осталось <b>${daysLeft} дн.</b> / <b>${parsesLeft} парсингов</b>
            </div>
            <div class="cabinet-hint">
                После окончания доступа парсинг будет приостановлен. История и
                выгрузки в Sheets/Excel останутся доступны.
            </div>
        `;
    }
    // Активная подписка
    else if (sub && sub.active) {
        const expires = sub.active.expires_at
            ? new Date(sub.active.expires_at).toLocaleDateString('ru-RU')
            : '—';
        const renewLine = sub.active.auto_renew
            ? `✓ Автопродление включено`
            : `⊗ Автопродление отключено`;
        body += `
            <div class="cabinet-meter">
                Действует до: <b>${expires}</b>
            </div>
            <div class="cabinet-hint">${renewLine}</div>
        `;
        if (sub.active.auto_renew) {
            body += `
                <button type="button" class="btn-secondary"
                        onclick="cancelSubscriptionFlow()">
                    Отменить автопродление
                </button>
            `;
        }
    }
    // Trial expired или blocked
    else if (t.is_blocked || t.tariff_plan === 'trial_expired') {
        body += `
            <div class="cabinet-meter blocked">
                ❌ ${escapeHtml(t.parsing_blocked_message || 'Доступ приостановлен')}
            </div>
        `;
    }
    // Legacy (simple/ai)
    else if (t.tariff_plan === 'simple' || t.tariff_plan === 'ai') {
        body += `
            <div class="cabinet-hint">
                Бессрочный доступ — спасибо за раннюю поддержку проекта!
            </div>
        `;
    }

    // Использование за период
    if (t.usage_current) {
        const parses = t.usage_current.parses_used_period || 0;
        let usageHtml = `<div class="cabinet-usage-row">📊 Парсингов за период: <b>${parses}</b></div>`;
        if (t.is_ai_available && (t.quotas?.companies_monthly || 0) > 0) {
            const used = t.usage_current.companies_processed || 0;
            const quota = t.quotas.companies_monthly;
            const pct = t.usage_current.companies_percent || 0;
            usageHtml += `
                <div class="cabinet-usage-row">
                    🤖 ИИ-проверок: <b>${used}</b> / ${quota} (${pct}%)
                </div>
                <div class="cabinet-bar">
                    <div class="cabinet-bar-fill" style="width:${pct}%"></div>
                </div>
            `;
        }
        body += `<div class="cabinet-usage">${usageHtml}</div>`;
    }

    el.innerHTML = body;
}

function renderCabinetPlans(plansData, currentTariff) {
    const list = document.getElementById('cabinet-plans-list');
    if (!plansData || !plansData.plans || plansData.plans.length === 0) {
        list.innerHTML = '<div class="cabinet-hint">Тарифы не настроены.</div>';
        return;
    }
    const billingConfigured = plansData.billing_configured;
    list.innerHTML = plansData.plans.map((p) => {
        const isCurrent =
            currentTariff &&
            (currentTariff.tariff_plan === p.id ||
                (p.id === 'basic' && currentTariff.tariff_plan === 'simple') ||
                (p.id === 'pro' && currentTariff.tariff_plan === 'ai'));
        const priceLine = p.price_rub > 0
            ? `${p.price_rub} ₽ / ${p.period_days} дн.`
            : `Цена будет настроена`;
        const btnLabel = !billingConfigured
            ? 'Оплатить (в разработке)'
            : 'Оплатить';
        const btnClass = isCurrent
            ? 'btn-secondary'
            : 'btn-primary';
        return `
            <div class="cabinet-plan ${isCurrent ? 'current' : ''}">
                <div class="cabinet-plan-head">
                    <span class="cabinet-plan-name">${escapeHtml(p.name)}</span>
                    <span class="cabinet-plan-price">${escapeHtml(priceLine)}</span>
                </div>
                <div class="cabinet-plan-descr">${escapeHtml(p.description)}</div>
                ${isCurrent
                    ? '<div class="cabinet-plan-current">Ваш текущий тариф</div>'
                    : `<button type="button" class="${btnClass}" onclick="payTariff('${p.id}')">${btnLabel}</button>`
                }
            </div>
        `;
    }).join('');
}

function renderCabinetHistory(sub) {
    const list = document.getElementById('cabinet-history-list');
    if (!sub || !sub.history || sub.history.length === 0) {
        list.innerHTML = '<div class="cabinet-hint">История подписок пуста.</div>';
        return;
    }
    list.innerHTML = sub.history.map((s) => {
        const created = s.starts_at
            ? new Date(s.starts_at).toLocaleDateString('ru-RU')
            : '—';
        const expires = s.expires_at
            ? new Date(s.expires_at).toLocaleDateString('ru-RU')
            : '—';
        const statusMap = {
            active: '✓ Активна',
            past_due: '⚠ Не оплачена',
            cancelled: '⊗ Отменена',
            expired: '⏱ Истекла',
        };
        return `
            <div class="cabinet-history-row">
                <span class="cabinet-history-tariff">${escapeHtml(s.tariff_plan.toUpperCase())}</span>
                <span class="cabinet-history-period">${created} → ${expires}</span>
                <span class="cabinet-history-status">${statusMap[s.status] || s.status}</span>
            </div>
        `;
    }).join('');
}

function updateParseBlockedBanner(t) {
    /** Обновляет баннер на странице «Парсинг» в соответствии с
     *  is_parsing_available. */
    const banner = document.getElementById('parseBlockedBanner');
    if (!banner) return;
    if (!t || t.is_parsing_available !== false) {
        banner.classList.add('hidden');
        return;
    }
    const text = document.getElementById('parseBlockedText');
    if (text) text.textContent = t.parsing_blocked_message
        || 'Тариф не позволяет запустить парсинг.';
    banner.classList.remove('hidden');
}

window.payTariff = async function(tariff) {
    /** Создаёт «платёж» (stub) и открывает confirmation_url. */
    try {
        const resp = await fetch(apiUrl('/api/billing/create-payment'), {
            ...apiFetchOptions(),
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tariff }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            tg.showAlert(data.error || data.message || 'Не удалось создать платёж.');
            return;
        }
        const url = data.confirmation_url;
        if (!url) {
            tg.showAlert('Платёжный URL не получен.');
            return;
        }
        // Stub-страница лежит в /app/payment_stub.html
        // tg.openLink открывает во внешнем браузере, что для Telegram Web
        // ведёт к открытию в новой вкладке. Для stub-страницы достаточно.
        if (typeof tg.openLink === 'function') {
            tg.openLink(url);
        } else {
            window.open(url, '_blank');
        }
    } catch (err) {
        console.error('payTariff failed:', err);
        tg.showAlert('Сеть недоступна.');
    }
};

window.cancelSubscriptionFlow = async function() {
    if (!confirm('Отменить автопродление? Доступ сохранится до даты окончания.')) {
        return;
    }
    try {
        const resp = await fetch(
            apiUrl('/api/billing/cancel-subscription'),
            { ...apiFetchOptions(), method: 'POST' }
        );
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            tg.showAlert(data.error || 'Не удалось отменить.');
            return;
        }
        tg.showAlert('Автопродление отключено. Доступ сохраняется до ' +
            new Date(data.access_until).toLocaleDateString('ru-RU') + '.');
        await loadCabinet();
    } catch (err) {
        tg.showAlert('Сеть недоступна.');
    }
};

// Кнопка «Открыть кабинет» в баннере блокировки.
function setupParseBlockedBanner() {
    const btn = document.getElementById('parseBlockedGoToCabinet');
    if (btn) {
        btn.addEventListener('click', () => switchToPage('cabinet'));
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    populateRegions();
    populateYandexRegions();
    setupTabs();
    setupBottomNav();
    setupProfileForms();
    setupParseBlockedBanner();

    document.getElementById('searchForm').addEventListener('submit', submitForm);
    const yandexForm = document.getElementById('yandexForm');
    if (yandexForm) yandexForm.addEventListener('submit', submitYandexForm);

    document.body.style.backgroundColor = tg.themeParams.bg_color || '#ffffff';

    try {
        await loadOkvedHandbook();
        setupOkvedSearch();
        renderPresetsPanel();
        setupPresetsButton();
    } catch (err) {
        console.error('Не удалось загрузить справочник ОКВЭД:', err);
        const errorEl = document.getElementById('okvedError');
        if (errorEl) {
            errorEl.textContent = 'Не удалось загрузить справочник ОКВЭД.';
            errorEl.classList.remove('hidden');
        }
    }

    // Любая правка фильтров перезапускает валидацию.
    document.getElementById('searchForm').addEventListener('input', () => updateValidation());
    document.getElementById('searchForm').addEventListener('change', () => updateValidation());

    // Этап 2 v3 — загружаем тариф и профили.
    await loadTariff();

    // Этап 3 — баннер блокировки парсинга. cachedTariff заполнится
    // в loadCabinet, но для случая когда пользователь не открыл
    // Кабинет, сделаем отдельный лёгкий запрос здесь же.
    try {
        const r = await fetch(apiUrl('/api/tariff'), apiFetchOptions());
        if (r.ok) {
            const t = await r.json();
            updateParseBlockedBanner(t);
        }
    } catch (_) { /* ignore */ }
});
