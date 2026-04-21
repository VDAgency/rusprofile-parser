// Telegram Web App SDK
const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

// Регионы РФ (код → название).
// Коды подтверждены сабмитом формы /search-advanced (см.
// scripts/diag_filters_final.py → logs/diag_filters_final.log).
// Большинство совпадают с ОКАТО, но для ряда субъектов Rusprofile
// использует *составной* код «старая,новая классификация».
const REGIONS = {
    "97,77": "Москва",              // составной: 97 (ТиНАО) + 77
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
    "81,59": "Пермский край",       // составной: 81 (Коми-Пермяцкий АО) + 59
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
    "75,80": "Забайкальский край",  // составной: 75 + 80 (Агинский Бурятский АО)
    "70": "Томская область",
    "42": "Кемеровская область",
    "19": "Республика Хакасия",
    "17": "Республика Тыва",
    "14": "Республика Саха (Якутия)",
    "25": "Приморский край",
    "27": "Хабаровский край",
    "28": "Амурская область",
    "41,82": "Камчатский край",     // составной: 41 + 82 (Корякский АО)
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

// Значения всех чекбоксов с одним именем собираем в массив.
function collectCheckboxGroup(name) {
    return Array.from(
        document.querySelectorAll(`input[type=checkbox][name="${name}"]:checked`)
    ).map(el => el.value);
}

// Вспомогалка: строка → null, если пусто.
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

// ОКВЭД — пользователь может ввести несколько кодов через запятую.
function parseOkved(raw) {
    if (!raw) return [];
    return raw.split(',')
        .map(s => s.trim())
        .filter(s => s.length > 0);
}

function getFormData() {
    const region = val('region');
    return {
        query: val('query'),
        region: region ? [region] : [],
        okved: parseOkved(val('okved')),
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
    };
}

function hasAnyFilter(data) {
    if (data.query) return true;
    for (const k of ['region', 'okved', 'okopf', 'msp']) {
        if (data[k] && data[k].length) return true;
    }
    for (const k of [
        'finance_revenue_from', 'finance_revenue_to',
        'finance_profit_from', 'finance_profit_to',
        'sshr_from', 'sshr_to',
        'capital_from', 'capital_to',
    ]) {
        if (data[k]) return true;
    }
    for (const k of [
        'has_phones', 'has_sites', 'has_emails',
        'finance_has_actual_year_data', 'not_defendant',
    ]) {
        if (data[k]) return true;
    }
    return false;
}

function submitForm(e) {
    e.preventDefault();
    const data = getFormData();

    if (!hasAnyFilter(data)) {
        tg.showAlert('Выберите хотя бы один фильтр или введите текст запроса.');
        return;
    }
    tg.sendData(JSON.stringify(data));
}

document.addEventListener('DOMContentLoaded', () => {
    populateRegions();
    document.getElementById('searchForm').addEventListener('submit', submitForm);
    document.body.style.backgroundColor = tg.themeParams.bg_color || '#ffffff';
});
