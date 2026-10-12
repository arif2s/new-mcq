// Dashboard logic
function initDashboard() {
    const state = window.AppState.get();

    // Stats calculation
    const totalTests = state.testResults.length;
    const avgAccuracy = totalTests > 0
        ? Math.round(state.testResults.reduce((acc, r) => acc + r.accuracy, 0) / totalTests)
        : 0;
    const totalTimeMinutes = Math.round(state.testResults.reduce((acc, r) => acc + r.timeUsedSeconds, 0) / 60);

    const dsAccuracy = document.getElementById('ds-accuracy');
    const dsTests = document.getElementById('ds-tests');
    const dsTime = document.getElementById('ds-time');

    if(dsAccuracy) dsAccuracy.textContent = `${avgAccuracy}%`;
    if(dsTests) dsTests.textContent = totalTests;
    if(dsTime) dsTime.textContent = `${totalTimeMinutes}m`;

    // Daily Targets
    const today = new Date().toISOString().split('T')[0];
    const todayStats = { questionsAnswered: 0, correctAnswers: 0, timeSpentSeconds: 0 };

    const todayHabit = state.habitLog.find(h => h.date === today);
    if (todayHabit) {
        todayStats.questionsAnswered = todayHabit.questionsAnswered;
        todayStats.correctAnswers = todayHabit.correctAnswers;
    }

    const targets = state.dailyTargets;

    const qProgress = targets.questionsTarget > 0 ? (todayStats.questionsAnswered / targets.questionsTarget) * 100 : 100;
    const cProgress = targets.correctTarget > 0 ? (todayStats.correctAnswers / targets.correctTarget) * 100 : 100;

    const qProgEl = document.getElementById('ds-q-prog');
    const cProgEl = document.getElementById('ds-c-prog');

    if(qProgEl) {
        qProgEl.style.width = `${Math.min(100, qProgress)}%`;
        document.getElementById('ds-q-text').textContent = `${todayStats.questionsAnswered} / ${targets.questionsTarget}`;
    }
    if(cProgEl) {
        cProgEl.style.width = `${Math.min(100, cProgress)}%`;
        document.getElementById('ds-c-text').textContent = `${todayStats.correctAnswers} / ${targets.correctTarget}`;
    }
}

document.addEventListener('stateUpdated', initDashboard);
document.addEventListener('DOMContentLoaded', async () => {
    await window.AppState.fetch();
    initDashboard();
});
