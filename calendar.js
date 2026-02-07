// Инициализация Telegram Web App
const tg = window.Telegram?.WebApp;
if (tg) {
    tg.ready();
    tg.expand();
}

// API URL: из api_url в query, или window.API_BASE_URL, или fallback
function getApiBase() {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get('api_url');
    if (fromUrl) return fromUrl.replace(/\/$/, '');
    if (window.API_BASE_URL) return window.API_BASE_URL.replace(/\/$/, '');
    return 'https://keaton-drys-gerda.ngrok-free.dev';
}
const API_BASE = getApiBase();

const monthsShort = ['Янв', 'Фев', 'Мар', 'Апр', 'Май', 'Июн',
    'Июл', 'Авг', 'Сен', 'Окт', 'Ноя', 'Дек'];

let currentYear = new Date().getFullYear();
let dayData = {};
let habitTexts = []; // [{ id, text }, ...]
let selectedHabitId = null;

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
        document.querySelector('.habit-switcher')?.parentElement?.insertBefore(el, document.getElementById('calendar'));
    }
    el.textContent = msg;
    el.style.display = 'block';
}

function closeDropdown() {
    const dd = document.getElementById('habit-dropdown');
    if (dd) dd.classList.remove('open');
}

function openDropdown() {
    const dd = document.getElementById('habit-dropdown');
    if (dd) dd.classList.add('open');
}

function toggleDropdown() {
    const dd = document.getElementById('habit-dropdown');
    if (dd) dd.classList.toggle('open');
}

async function loadHabits() {
    const userId = tg?.initDataUnsafe?.user?.id;
    if (!userId) return [];
    const habitUrl = `${API_BASE}/api/users/${userId}/habit`;
    try {
        const res = await fetch(habitUrl, {
            method: 'GET',
            mode: 'cors',
            headers: {
                'Accept': 'application/json',
                'ngrok-skip-browser-warning': 'true'
            }
        });
        if (!res.ok) return [];
        const data = await res.json();
        return data.habits || [];
    } catch (e) {
        console.warn('loadHabits error:', e);
        return [];
    }
}

async function loadCalendarData(habitId) {
    const userId = tg?.initDataUnsafe?.user?.id;
    if (!userId) {
        dayData = {};
        showLoadError('Не удалось определить пользователя (откройте из Telegram).');
        renderCalendar();
        return;
    }
    if (!API_BASE) {
        dayData = {};
        showLoadError('Не задан адрес API.');
        renderCalendar();
        return;
    }
    hideLoadError();
    let calendarUrl = `${API_BASE}/api/users/${userId}/calendar`;
    if (habitId != null) {
        calendarUrl += `?habit_id=${habitId}`;
    }
    try {
        const res = await fetch(calendarUrl, {
            method: 'GET',
            mode: 'cors',
            headers: {
                'Accept': 'application/json',
                'ngrok-skip-browser-warning': 'true'
            }
        });
        const contentType = res.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) {
            const text = await res.text();
            const preview = text.slice(0, 80).replace(/\s+/g, ' ');
            showLoadError('Сервер вернул не JSON. Проверьте, что бот запущен и ngrok активен. ' + (preview.length ? preview + '…' : ''));
            dayData = {};
            renderCalendar();
            return;
        }
        if (!res.ok) {
            showLoadError('Ошибка ' + res.status + ': ' + res.statusText);
            dayData = {};
            renderCalendar();
            return;
        }
        dayData = await res.json();
    } catch (e) {
        console.error('Ошибка загрузки календаря:', e);
        const msg = e.message || String(e);
        const isNetwork = msg.includes('Failed to fetch') || msg.includes('NetworkError') || msg.includes('Load failed');
        showLoadError(isNetwork ? 'Нет связи с сервером. Запущен ли бот? Работает ли ngrok?' : 'Ошибка: ' + msg);
        dayData = {};
    }
    renderCalendar();
}

function renderHabitSwitcher() {
    const btn = document.getElementById('habit-title-btn');
    const dd = document.getElementById('habit-dropdown');
    const textEl = document.getElementById('habit-title-text');

    if (!habitTexts.length) {
        if (textEl) textEl.textContent = 'Нет привычек';
        if (dd) dd.innerHTML = '';
        return;
    }

    const selected = habitTexts.find(h => h.id === selectedHabitId) || habitTexts[0];
    selectedHabitId = selected.id;
    if (textEl) textEl.textContent = '📝 ' + selected.text;

    dd.innerHTML = '';
    habitTexts.forEach(h => {
        const item = document.createElement('div');
        item.className = 'habit-dropdown-item' + (h.id === selectedHabitId ? ' selected' : '');
        item.textContent = h.text;
        item.dataset.habitId = h.id;
        item.addEventListener('click', () => {
            selectedHabitId = h.id;
            closeDropdown();
            loadCalendarData(selectedHabitId);
            renderHabitSwitcher();
        });
        dd.appendChild(item);
    });
}

function renderMonthBlock(year, month) {
    const block = document.createElement('div');
    block.className = 'month-block';

    const title = document.createElement('div');
    title.className = 'month-block-title';
    title.textContent = monthsShort[month];
    block.appendChild(title);

    const grid = document.createElement('div');
    grid.className = 'month-grid';

    ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].forEach(d => {
        const el = document.createElement('div');
        el.className = 'weekday';
        el.textContent = d;
        grid.appendChild(el);
    });

    const firstDay = new Date(year, month, 1);
    const lastDay = new Date(year, month + 1, 0);
    const startPadding = firstDay.getDay() === 0 ? 6 : firstDay.getDay() - 1;

    for (let i = 0; i < startPadding; i++) {
        const el = document.createElement('div');
        el.className = 'day empty';
        grid.appendChild(el);
    }
    for (let d = 1; d <= lastDay.getDate(); d++) {
        const el = document.createElement('div');
        el.className = 'day ' + getDayData(year, month, d);
        el.textContent = d;
        grid.appendChild(el);
    }
    const totalCells = startPadding + lastDay.getDate();
    const remainder = totalCells % 7;
    if (remainder > 0) {
        for (let i = 0; i < 7 - remainder; i++) {
            const el = document.createElement('div');
            el.className = 'day empty';
            grid.appendChild(el);
        }
    }

    block.appendChild(grid);
    return block;
}

function renderCalendar() {
    document.getElementById('year-title').textContent = currentYear;

    const container = document.getElementById('calendar');
    container.innerHTML = '';
    for (let m = 0; m < 12; m++) {
        container.appendChild(renderMonthBlock(currentYear, m));
    }
}

document.getElementById('habit-title-btn').addEventListener('click', (e) => {
    e.stopPropagation();
    toggleDropdown();
});

document.addEventListener('click', () => closeDropdown());

document.getElementById('habit-dropdown').addEventListener('click', (e) => e.stopPropagation());

async function init() {
    habitTexts = await loadHabits();
    if (habitTexts.length) {
        selectedHabitId = habitTexts[0].id;
    }
    renderHabitSwitcher();
    await loadCalendarData(selectedHabitId);
}

init();
