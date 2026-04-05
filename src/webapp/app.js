// Telegram Web App SDK
const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

// Регионы РФ
const REGIONS = {
    "77": "Москва",
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
    "59": "Пермский край",
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
    "75": "Забайкальский край",
    "70": "Томская область",
    "42": "Кемеровская область",
    "19": "Республика Хакасия",
    "17": "Республика Тыва",
    "14": "Республика Саха (Якутия)",
    "25": "Приморский край",
    "27": "Хабаровский край",
    "28": "Амурская область",
    "41": "Камчатский край",
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
    "45": "Курганская область"
};

// Заполняем выпадающий список регионов
function populateRegions() {
    const select = document.getElementById('region');
    const sorted = Object.entries(REGIONS).sort((a, b) => a[1].localeCompare(b[1], 'ru'));
    sorted.forEach(([code, name]) => {
        const option = document.createElement('option');
        option.value = code;
        option.textContent = name;
        select.appendChild(option);
    });
}

// Собираем данные формы
function getFormData() {
    return {
        region: document.getElementById('region').value || null,
        okved: document.getElementById('okved').value || null,
        revenue_from: document.getElementById('revenue_from').value || null,
        revenue_to: document.getElementById('revenue_to').value || null,
        org_type: document.getElementById('org_type').value || null,
        business_size: document.getElementById('business_size').value || null,
        has_phone: document.getElementById('has_phone').checked,
        has_site: document.getElementById('has_site').checked,
        has_email: document.getElementById('has_email').checked
    };
}

// Отправка данных в Telegram-бот
function submitForm(e) {
    e.preventDefault();

    const data = getFormData();

    // Проверяем что хоть один фильтр выбран
    const hasFilter = data.region || data.okved || data.revenue_from ||
                      data.revenue_to || data.org_type || data.business_size ||
                      data.has_phone || data.has_site || data.has_email;

    if (!hasFilter) {
        tg.showAlert('Выберите хотя бы один фильтр для поиска');
        return;
    }

    // Отправляем данные боту
    tg.sendData(JSON.stringify(data));
}

// Инициализация
document.addEventListener('DOMContentLoaded', () => {
    populateRegions();
    document.getElementById('searchForm').addEventListener('submit', submitForm);

    // Тема Telegram
    document.body.style.backgroundColor = tg.themeParams.bg_color || '#ffffff';
});
