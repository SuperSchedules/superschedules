let current = new Date();
current.setDate(1);

const monthEl = document.getElementById('current-month');
const calendarEl = document.getElementById('calendar');

function renderCalendar() {
    monthEl.textContent = current.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
    calendarEl.innerHTML = '';

    const grid = document.createElement('div');
    grid.className = 'grid';

    const firstDay = current.getDay();
    for (let i = 0; i < firstDay; i++) {
        grid.appendChild(document.createElement('div'));
    }

    const daysInMonth = new Date(current.getFullYear(), current.getMonth() + 1, 0).getDate();
    for (let d = 1; d <= daysInMonth; d++) {
        const cell = document.createElement('div');
        cell.textContent = d;
        grid.appendChild(cell);
    }

    calendarEl.appendChild(grid);
}

document.getElementById('prev-month').addEventListener('click', () => {
    current.setMonth(current.getMonth() - 1);
    renderCalendar();
});

document.getElementById('next-month').addEventListener('click', () => {
    current.setMonth(current.getMonth() + 1);
    renderCalendar();
});

renderCalendar();
