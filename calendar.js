// Инициализация Telegram Web App
const tg = window.Telegram?.WebApp;
if (tg) {
    tg.ready();
    tg.expand();
}

// API URL: api_url в query, window.API_BASE_URL, или same-origin (когда Mini App на том же домене что и API)
function getApiBase() {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get('api_url');
    if (fromUrl) return fromUrl.replace(/\/$/, '');
    if (window.API_BASE_URL) return String(window.API_BASE_URL).replace(/\/$/, '');
    // Same-origin — Mini App раздаётся с Railway вместе с API, fetch без CORS
    if (typeof window !== 'undefined' && window.location?.origin) {
        return window.location.origin;
    }
    return 'https://habit-tracker-web-production-f65e.up.railway.app';
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
const monthsFull = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

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
            headers: { 'Accept': 'application/json' }
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
            headers: { 'Accept': 'application/json' }
        });
        const contentType = res.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) {
            const text = await res.text();
            const preview = text.slice(0, 80).replace(/\s+/g, ' ');
            showLoadError('Сервер вернул не JSON. ' + (preview.length ? preview + '…' : ''));
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
        const raw = await res.json();
        dayData = (typeof raw === 'object' && raw !== null) ? raw : {};
    } catch (e) {
        console.error('Ошибка загрузки календаря:', e);
        const msg = e.message || String(e);
        const isNetwork = msg.includes('Failed to fetch') || msg.includes('NetworkError') || msg.includes('Load failed');
        showLoadError(isNetwork ? 'Нет связи с сервером. Проверьте подключение к интернету.' : 'Ошибка: ' + msg);
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

/** Активный день — любой день с логом (minimum или good). */
function isActiveDay(status) {
    return status === 'minimum' || status === 'good';
}

function toDateKey(d) {
    const y = d.getFullYear(), m = d.getMonth(), day = d.getDate();
    return `${y}-${String(m + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

/** Расчёт серий: { current, longest }. Текущая — подряд до сегодня включительно. */
function computeStreaks() {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const todayKey = toDateKey(today);

    const activeDates = Object.keys(dayData).filter(k => isActiveDay(dayData[k]));
    if (activeDates.length === 0) return { current: 0, longest: 0 };

    activeDates.sort();

    if (dayData[todayKey] === 'no-data') {
        let longest = 1;
        let run = 1;
        for (let i = 1; i < activeDates.length; i++) {
            const prev = new Date(activeDates[i - 1]);
            const curr = new Date(activeDates[i]);
            const diffDays = Math.round((curr - prev) / (24 * 60 * 60 * 1000));
            if (diffDays === 1) {
                run++;
                longest = Math.max(longest, run);
            } else {
                run = 1;
            }
        }
        return { current: 0, longest };
    }

    let current = 0;
    let startDate = new Date(today);
    if (!isActiveDay(dayData[todayKey])) {
        startDate.setDate(startDate.getDate() - 1);
    }
    let d = new Date(startDate);
    while (true) {
        const k = toDateKey(d);
        if (isActiveDay(dayData[k])) {
            current++;
            d.setDate(d.getDate() - 1);
        } else break;
    }

    let longest = 1;
    let run = 1;
    for (let i = 1; i < activeDates.length; i++) {
        const prev = new Date(activeDates[i - 1]);
        const curr = new Date(activeDates[i]);
        const diffDays = Math.round((curr - prev) / (24 * 60 * 60 * 1000));
        if (diffDays === 1) {
            run++;
            longest = Math.max(longest, run);
        } else {
            run = 1;
        }
    }

    return { current, longest };
}

function renderStreaks() {
    const el = document.getElementById('streak-stats');
    if (!el) return;
    const { current, longest } = computeStreaks();
    el.innerHTML = '';
    const curSpan = document.createElement('span');
    curSpan.className = 'streak-current';
    curSpan.textContent = `Текущая серия: ${current} ${current === 1 ? 'день' : current < 5 ? 'дня' : 'дней'}${current > 0 ? ' 🔥' : ''}`;
    el.appendChild(curSpan);
    const longSpan = document.createElement('span');
    longSpan.className = 'streak-longest';
    longSpan.textContent = `Самая длинная серия: ${longest} ${longest === 1 ? 'день' : longest < 5 ? 'дня' : 'дней'}`;
    el.appendChild(longSpan);
}

/** Статистика за месяц: { activeDays, totalDays }. Только для прошедших месяцев. */
function getMonthlyStats(year, month) {
    const totalDays = new Date(year, month + 1, 0).getDate();
    let activeDays = 0;
    for (let d = 1; d <= totalDays; d++) {
        const status = getDayData(year, month, d);
        if (isActiveDay(status)) activeDays++;
    }
    return { activeDays, totalDays };
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

    const dataKeys = Object.keys(dayData || {});
    const dataDates = dataKeys.map(k => {
        const [y, m, d] = k.split('-').map(Number);
        return new Date(y, m - 1, d);
    });
    const hasLogs = dataDates.length > 0;
    const displayStartDate = hasLogs
        ? (() => { const d = new Date(Math.min(...dataDates.map(x => x.getTime()))); d.setHours(0, 0, 0, 0); return d; })()
        : today;
    const startMonday = getMonday(displayStartDate);
    const totalWeeks = Math.ceil((mondayFuture2.getTime() - startMonday.getTime()) / (7 * 24 * 60 * 60 * 1000)) + 1;

    const weeks = [];
    for (let i = 0; i < totalWeeks; i++) {
        const monday = new Date(mondayFuture2);
        monday.setDate(mondayFuture2.getDate() - 7 * i);
        const isStartWeek = monday.getTime() === startMonday.getTime();
        const weekDays = [];
        for (let j = 0; j < 7; j++) {
            const d = new Date(monday);
            d.setDate(monday.getDate() + j);
            if (isStartWeek && d < displayStartDate) {
                weekDays.push(null);
                continue;
            }
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

    function firstNonNullDay(days) {
        return days.find(d => d !== null);
    }
    function lastNonNullDay(days) {
        for (let i = days.length - 1; i >= 0; i--) if (days[i] !== null) return days[i];
        return null;
    }
    const displayRows = [];
    for (let i = 0; i < weeks.length; i++) {
        const week = weeks[i];
        const firstDay = firstNonNullDay(week.days);
        const lastDay = lastNonNullDay(week.days);
        const firstMonth = firstDay ? firstDay.date.getMonth() : 0;
        const lastMonth = lastDay ? lastDay.date.getMonth() : 0;
        if (firstMonth === lastMonth) {
            displayRows.push({ month: firstMonth, days: week.days });
        } else {
            let splitIndex = -1;
            for (let j = 1; j < 7; j++) {
                const curr = week.days[j];
                const prev = week.days[j - 1];
                if (curr !== null && prev !== null && curr.date.getMonth() !== prev.date.getMonth()) {
                    splitIndex = j;
                    break;
                }
            }
            if (splitIndex > 0) {
                const laterPart = [];
                for (let j = 0; j < 7; j++) {
                    laterPart.push(j >= splitIndex ? week.days[j] : null);
                }
                const earlierPart = [];
                for (let j = 0; j < 7; j++) {
                    earlierPart.push(j < splitIndex ? week.days[j] : null);
                }
                const laterMonth = week.days[splitIndex] ? week.days[splitIndex].date.getMonth() : firstMonth;
                const earlierMonth = firstDay ? firstDay.date.getMonth() : firstMonth;
                displayRows.push({ month: laterMonth, days: laterPart });
                displayRows.push({ month: earlierMonth, days: earlierPart });
            } else {
                displayRows.push({ month: firstMonth, days: week.days });
            }
        }
    }

    const container = document.getElementById('calendar');
    if (!container) return;
    container.innerHTML = '';
    container.className = 'calendar-view';

    const grid = document.createElement('div');
    grid.className = 'calendar-grid';

    displayRows.forEach((rowData, index) => {
        const prevMonth = index > 0 ? displayRows[index - 1].month : -1;
        const isNewMonth = index > 0 && rowData.month !== prevMonth;
        if (isNewMonth) {
            const spacer = document.createElement('div');
            spacer.className = 'month-spacer';
            spacer.setAttribute('aria-hidden', 'true');
            grid.appendChild(spacer);
        }

        const row = document.createElement('div');
        row.className = 'calendar-week-row';

        const firstNonNullIndex = rowData.days.findIndex(d => d !== null);
        const isLastRowOfMonth = index === displayRows.length - 1 || displayRows[index + 1].month !== rowData.month;

        const weekContent = document.createElement('div');
        weekContent.className = 'week-content';

        const cellsRow = document.createElement('div');
        cellsRow.className = 'cells-row';
        rowData.days.forEach((day) => {
            const cell = document.createElement('div');
            cell.className = 'day-cell';
            if (day === null) {
                cell.classList.add('empty');
            } else if (day.isFuture) {
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

        const monthLabelWrap = document.createElement('div');
        monthLabelWrap.className = 'month-label-wrap';
        if (isLastRowOfMonth) {
            if (firstNonNullIndex >= 0) {
                row.dataset.monthOffset = String(firstNonNullIndex);
            }
            const firstDay = rowData.days.find(d => d !== null);
            const rowYear = firstDay ? firstDay.date.getFullYear() : today.getFullYear();
            const rowMonth = rowData.month;
            const firstDayOfMonth = firstDay ? firstDay.date.getDate() : 1;
            const isFullMonth = firstDayOfMonth === 1;

            const monthLabel = document.createElement('div');
            monthLabel.className = 'month-label month-label-vertical' + (isFullMonth ? '' : ' short');
            monthLabel.textContent = isFullMonth ? monthsFull[rowMonth] : monthsShort[rowMonth];
            monthLabelWrap.appendChild(monthLabel);

            if (isFullMonth) {
                const { activeDays, totalDays } = getMonthlyStats(rowYear, rowMonth);
                const monthProgress = document.createElement('div');
                monthProgress.className = 'month-progress month-label-vertical';
                monthProgress.textContent = `Выполнено ${activeDays}/${totalDays}`;
                monthLabelWrap.appendChild(monthProgress);
            }
        }
        row.appendChild(monthLabelWrap);

        grid.appendChild(row);
    });

    container.appendChild(grid);

    // Выравнивание метки месяца по первому дню: смещение в px по высоте ячейки
    requestAnimationFrame(() => {
        const firstRow = grid.querySelector('.calendar-week-row');
        const sampleCell = firstRow?.querySelector('.cells-row .day-cell:not(.empty)');
        const cellHeight = sampleCell ? sampleCell.offsetHeight : 40;
        const gap = 4;
        grid.querySelectorAll('.calendar-week-row').forEach((r) => {
            const offset = parseInt(r.dataset.monthOffset || '0', 10);
            r.style.setProperty('--month-offset-px', `${(cellHeight + gap) * offset}px`);
        });
    });

    renderStreaks();
}

function setupListeners() {
    const btn = document.getElementById('habit-title-btn');
    const dd = document.getElementById('habit-dropdown');
    if (btn) btn.addEventListener('click', (e) => { e.stopPropagation(); toggleDropdown(); });
    if (dd) dd.addEventListener('click', (e) => e.stopPropagation());
    document.addEventListener('click', () => closeDropdown());
}

async function init() {
    try {
        setupListeners();
        habitTexts = await loadHabits();
        if (habitTexts.length) {
            selectedHabitId = habitTexts[0].id;
        }
        renderHabitSwitcher();
        await loadCalendarData(selectedHabitId);
    } catch (err) {
        console.error('init error:', err);
        showLoadError('Ошибка загрузки: ' + (err && err.message ? err.message : String(err)));
        renderCalendar();
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
