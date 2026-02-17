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

// user_id: из initData (при InlineKeyboard) или из user_id в URL (при Reply Keyboard web_app — initData часто пустой)
function getUserId() {
    const fromInitData = tg?.initDataUnsafe?.user?.id;
    if (fromInitData) return fromInitData;
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get('user_id');
    return fromUrl ? parseInt(fromUrl, 10) : null;
}

const monthsShort = ['ЯНВ', 'ФЕВ', 'МАР', 'АПР', 'МАЙ', 'ИЮН',
    'ИЮЛ', 'АВГ', 'СЕН', 'ОКТ', 'НОЯ', 'ДЕК'];

let dayData = {};
let habitTexts = [];
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
    const userId = getUserId();
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
    const userId = getUserId();
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

/** Понедельник = первый день недели. Возвращает понедельник для даты d. */
function getMonday(d) {
    const date = new Date(d);
    const day = date.getDay();
    const diff = date.getDate() - (day === 0 ? 6 : day - 1);
    date.setDate(diff);
    return date;
}

function renderCalendar() {
    const today = new Date();
    today.setHours(0, 0, 0, 0);

    const futureStart = new Date(today);
    futureStart.setDate(today.getDate() + 14);
    const mondayFuture2 = getMonday(futureStart);

    let totalWeeks;
    const dataDates = Object.keys(dayData).map(k => {
        const [y, m, d] = k.split('-').map(Number);
        return new Date(y, m - 1, d);
    });
    if (dataDates.length > 0) {
        const minDataDate = new Date(Math.min(...dataDates.map(d => d.getTime())));
        minDataDate.setHours(0, 0, 0, 0);
        const mondayMin = getMonday(minDataDate);
        const weeksFromMin = Math.ceil((mondayFuture2 - mondayMin) / (7 * 24 * 60 * 60 * 1000));
        totalWeeks = weeksFromMin + 1;
    } else {
        totalWeeks = 3;
    }

    const weeks = [];
    for (let i = 0; i < totalWeeks; i++) {
        const monday = new Date(mondayFuture2);
        monday.setDate(mondayFuture2.getDate() - 7 * i);
        const weekDays = [];
        for (let j = 0; j < 7; j++) {
            const d = new Date(monday);
            d.setDate(monday.getDate() + j);
            const isFuture = d > today;
            const isToday = d.getTime() === today.getTime();
            const status = isFuture ? null : getDayData(d.getFullYear(), d.getMonth(), d.getDate());
            weekDays.push({
                date: d,
                dayNum: d.getDate(),
                isFuture,
                isToday,
                status
            });
        }
        weeks.push({ monday: new Date(monday), days: weekDays });
    }

    const container = document.getElementById('calendar');
    container.innerHTML = '';
    container.className = 'calendar-view';

    const grid = document.createElement('div');
    grid.className = 'calendar-grid';

    weeks.forEach((week, index) => {
        const row = document.createElement('div');
        row.className = 'calendar-week-row';

        const monthLabel = document.createElement('div');
        monthLabel.className = 'month-label';
        const dayWithFirst = week.days.find(d => d.date.getDate() === 1);
        const isEarliestWeek = index === weeks.length - 1;
        const monthForLabel = dayWithFirst
            ? dayWithFirst.date.getMonth()
            : (isEarliestWeek ? week.monday.getMonth() : -1);
        monthLabel.textContent = monthForLabel >= 0 ? monthsShort[monthForLabel] : '';
        const prevMonth = index > 0 ? weeks[index - 1].monday.getMonth() : -1;
        const isNewMonth = monthForLabel >= 0 && (index === 0 || monthForLabel !== prevMonth);
        if (isNewMonth && index > 0) {
            const spacer = document.createElement('div');
            spacer.className = 'month-spacer';
            spacer.setAttribute('aria-hidden', 'true');
            grid.appendChild(spacer);
        }
        row.appendChild(monthLabel);

        const weekContent = document.createElement('div');
        weekContent.className = 'week-content';

        const cellsRow = document.createElement('div');
        cellsRow.className = 'cells-row';
        week.days.forEach((day) => {
            const cell = document.createElement('div');
            cell.className = 'day-cell';
            if (day.isFuture) {
                cell.classList.add('blocked');
                cell.textContent = day.dayNum;
            } else {
                cell.classList.add('status-' + (day.status || 'no-data'));
                if (day.isToday) cell.classList.add('today');
                if (day.isToday) cell.textContent = day.dayNum;
            }
            cellsRow.appendChild(cell);
        });
        weekContent.appendChild(cellsRow);
        row.appendChild(weekContent);

        grid.appendChild(row);
    });

    container.appendChild(grid);
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
