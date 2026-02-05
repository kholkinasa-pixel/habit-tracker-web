// Инициализация Telegram Web App
const tg = window.Telegram?.WebApp;
if (tg) {
    tg.ready();
    tg.expand();
}

let currentDate = new Date();
const months = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
                'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

// Пример данных: { "2025-02-05": "good", "2025-02-06": "minimum", ... }
// Значения: "no-data" | "minimum" | "good"
function getDayData(year, month, day) {
    const key = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    return dayData[key] || 'no-data';
}

// Замените на реальные данные из API/БД
let dayData = {
    '2025-02-01': 'good',
    '2025-02-02': 'minimum',
    '2025-02-05': 'good',
};

function renderCalendar() {
    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();

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

renderCalendar();
