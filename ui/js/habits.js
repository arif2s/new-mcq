function initHabits() {
    const state = window.AppState.get();
    const habitMap = new Map();
    state.habitLog.forEach(h => habitMap.set(h.date, h));

    const today = new Date();
    const calendarData = [];

    // Generate 365 days of data
    const iterDate = new Date(today);
    iterDate.setDate(iterDate.getDate() - 364);

    for (let i = 0; i <= 364; i++) {
        const dateStr = iterDate.toISOString().split('T')[0];
        const h = habitMap.get(dateStr);
        calendarData.push({
            date: dateStr,
            count: h ? h.questionsAnswered : 0,
            dayOfWeek: iterDate.getDay()
        });
        iterDate.setDate(iterDate.getDate() + 1);
    }

    // Calculate Stats
    const last7Days = calendarData.slice(-7);
    const wQuestions = last7Days.reduce((acc, d) => acc + d.count, 0);
    const last30Days = calendarData.slice(-30);
    const mQuestions = last30Days.reduce((acc, d) => acc + d.count, 0);

    const hActiveEl = document.getElementById('ht-active-streak');
    const hLongEl = document.getElementById('ht-longest-streak');
    const hWkQ = document.getElementById('ht-week-q');
    const hMoQ = document.getElementById('ht-month-q');

    if(hActiveEl) hActiveEl.textContent = `${state.streakDays || 0} Days`;
    if(hLongEl) hLongEl.textContent = `${state.longestStreak || 0} Days`;
    if(hWkQ) hWkQ.textContent = wQuestions;
    if(hMoQ) hMoQ.textContent = mQuestions;

    // Render Calendar
    const calContainer = document.getElementById('ht-calendar');
    if(calContainer) {
        calContainer.innerHTML = '';
        const grid = document.createElement('div');
        grid.style.display = 'grid';
        grid.style.gridTemplateColumns = 'repeat(53, 1fr)';
        grid.style.gridTemplateRows = 'repeat(7, 1fr)';
        grid.style.gap = '4px';

        let currentCol = 0;
        calendarData.forEach((day, idx) => {
            const cell = document.createElement('div');
            cell.className = 'w-3 h-3 rounded-sm';
            cell.title = `${day.date}: ${day.count} questions`;

            // Map count to color intensity
            if (day.count === 0) cell.classList.add('bg-gray-800');
            else if (day.count < 10) cell.classList.add('bg-brand-900');
            else if (day.count < 30) cell.classList.add('bg-brand-700');
            else if (day.count < 60) cell.classList.add('bg-brand-500');
            else cell.classList.add('bg-brand-300');

            // Grid placement (col = week, row = day of week)
            cell.style.gridColumn = currentCol + 1;
            cell.style.gridRow = day.dayOfWeek + 1;

            if (day.dayOfWeek === 6) currentCol++;

            grid.appendChild(cell);
        });
        calContainer.appendChild(grid);
    }
}

document.addEventListener('stateUpdated', initHabits);
document.addEventListener('DOMContentLoaded', initHabits);
