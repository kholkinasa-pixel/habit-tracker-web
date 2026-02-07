// Инициализация Telegram Web App
const tg = window.Telegram?.WebApp;
if (tg) {
    tg.ready();
    tg.expand();
}

const API_BASE = "https://keaton-drys-gerda.ngrok-free.dev";

let currentDate = new Date();
const months = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
                'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

// Данные календаря из API: { "2025-02-05": "good", "2025-02-06": "minimum", ... }
// Значения: "no-data" | "minimum" | "good"
let dayData = {};
let habitText = '';

function getDayData(year, month, day) {
    const key = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    return dayData[key] || 'no-data';
}

function hideLoadError() {
    const el = document.getElementById('load-error');
    if (el) el.style.display = 'none';
}
function showLoadError(msg) {
    let el = document.getElementById('load-error');
    if (!el) {
        el = document.createElement('div');
        el.id = 'load-error';
        el.style.cssText = 'margin-top:12px;padding:10px;background:rgba(200,0,0,0.15);border-radius:8px;font-size:13px;';
        document.querySelector('.header')?.parentElement?.insertBefore(el, document.getElementById('calendar'));
    }
    el.textContent = msg;
    el.style.display = 'block';
}

async function loadCalendarData() {
    const userId = tg?.initDataUnsafe?.user?.id;
    if (!userId) {
        console.warn('Telegram user id не найден, календарь пустой');
        dayData = {};
        habitText = '';
        showLoadError('Не удалось определить пользователя (откройте из Telegram).');
        renderCalendar();
        return;
    }
    if (!API_BASE) {
        dayData = {};
        habitText = '';
        showLoadError('Не задан адрес API. Укажите BACKEND_PUBLIC_URL в config.py и обновите календарь на GitHub.');
        renderCalendar();
        return;
    }
    hideLoadError();
    const calendarUrl = `${API_BASE}/api/users/${userId}/calendar`;
    const habitUrl = `${API_BASE}/api/users/${userId}/habit`;
    try {
        const [calRes, habitRes] = await Promise.all([
            fetch(calendarUrl, {
                method: 'GET',
                mode: 'cors',
                headers: {
                    'Accept': 'application/json',
                    'ngrok-skip-browser-warning': 'true'
                }
            }),
            fetch(habitUrl, {
                method: 'GET',
                mode: 'cors',
                headers: {
                    'Accept': 'application/json',
                    'ngrok-skip-browser-warning': 'true'
                }
            }).catch(() => null)
        ]);
        const res = calRes;
        const contentType = res.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) {
            const text = await res.text();
            const preview = text.slice(0, 80).replace(/\s+/g, ' ');
            showLoadError('Сервер вернул не JSON (код ' + res.status + '). Проверьте, что бот запущен и ngrok активен. ' + (preview.length ? 'Ответ: ' + preview + '…' : ''));
            dayData = {};
            habitText = '';
            renderCalendar();
            return;
        }
        if (!res.ok) {
            showLoadError('Ошибка ' + res.status + ': ' + res.statusText);
            dayData = {};
            habitText = '';
            renderCalendar();
            return;
        }
        dayData = await res.json();
        if (habitRes && habitRes.ok) {
            const habitData = await habitRes.json();
            habitText = habitData.habit_text || '';
        } else {
            habitText = '';
        }
    } catch (e) {
        console.error('Ошибка загрузки календаря:', e);
        const msg = e.message || String(e);
        const isNetwork = msg.includes('Failed to fetch') || msg.includes('NetworkError') || msg.includes('Load failed');
        showLoadError(isNetwork
            ? 'Нет связи с сервером. Запущен ли бот? Работает ли ngrok? URL: ' + API_BASE
            : 'Ошибка: ' + msg);
        dayData = {};
        habitText = '';
    }
    renderCalendar();
}

function renderCalendar() {
    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();

    const habitEl = document.getElementById('habit-title');
    if (habitEl) habitEl.textContent = habitText ? `📝 ${habitText}` : '';

    document.getElementById('month-title').textContent = `${months[month]} ${year}`;

    const firstDay = new Date(year, month, 1);
    const lastDay = new Date(year, month + 1, 0);
    const startPadding = firstDay.getDay() === 0 ? 6 : firstDay.getDay() - 1; // Пн первый

    const grid = document.getElementById('calendar');
    grid.innerHTML = '';

    // Заголовки дней недели
    ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].forEach(d => {
        const el = document.createElement('div');
        el.className = 'weekday';
        el.textContent = d;
        grid.appendChild(el);
    });

    // Пустые ячейки до 1-го числа
    for (let i = 0; i < startPadding; i++) {
        const el = document.createElement('div');
        el.className = 'day empty';
        grid.appendChild(el);
    }

    // Дни месяца
    for (let d = 1; d <= lastDay.getDate(); d++) {
        const el = document.createElement('div');
        el.className = 'day ' + getDayData(year, month, d);
        el.textContent = d;
        grid.appendChild(el);
    }

    // Пустые ячейки в конце
    const totalCells = startPadding + lastDay.getDate();
    const remainder = totalCells % 7;
    if (remainder > 0) {
        for (let i = 0; i < 7 - remainder; i++) {
            const el = document.createElement('div');
            el.className = 'day empty';
            grid.appendChild(el);
        }
    }
}

document.getElementById('prev').onclick = () => {
    currentDate.setMonth(currentDate.getMonth() - 1);
    renderCalendar();
};
document.getElementById('next').onclick = () => {
    currentDate.setMonth(currentDate.getMonth() + 1);
    renderCalendar();
};

// Загружаем данные из БД через API и рисуем календарь
loadCalendarData();
