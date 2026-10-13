// ui/js/habits.js


function initHabits() {
    const state = window.AppState.get();
    if (!state) return;

    const container = document.getElementById('habit-tracker-view');
    if (!container) return;

    const habitLog = state.habitLog || [];
    const testResults = state.testResults || [];
    const streakDays = state.streakDays || 0;
    const longestStreak = state.longestStreak || 0;
    const totalQuestionsEver = state.totalQuestionsEver || 0;
    const dailyTargets = state.dailyTargets || { questionsTarget: 50, correctTarget: 40 };

    // 1. Data Aggregation & O(1) Lookups
    const habitMap = new Map();
    habitLog.forEach(h => habitMap.set(h.date, h));

    const today = new Date();
    const todayStr = today.toISOString().split('T')[0];
    const todayCount = habitMap.get(todayStr)?.questionsAnswered || 0;

    const calendarData = [];
    const iterDate = new Date(today);
    iterDate.setDate(iterDate.getDate() - 364);

    for (let i = 0; i <= 364; i++) {
        const dateStr = iterDate.toISOString().split('T')[0];
        const habit = habitMap.get(dateStr);
        calendarData.push({
            date: dateStr,
            count: habit?.questionsAnswered || 0,
            day: iterDate.getDay()
        });
        iterDate.setDate(iterDate.getDate() + 1);
    }

    // Group into weeks for the Heatmap
    const weeks = [];
    let currentWeek = [];
    calendarData.forEach(day => {
        currentWeek.push(day);
        if (day.day === 6) {
            weeks.push(currentWeek);
            currentWeek = [];
        }
    });
    if (currentWeek.length > 0) weeks.push(currentWeek);

    // Slice Stats
    const last7Days = calendarData.slice(-7);
    const wQuestions = last7Days.reduce((acc, d) => acc + d.count, 0);
    const wActive = last7Days.filter(d => d.count > 0).length;

    const last30Days = calendarData.slice(-30);
    const mQuestions = last30Days.reduce((acc, d) => acc + d.count, 0);
    const mActive = last30Days.filter(d => d.count > 0).length;

    const bestDay = habitLog.length > 0
        ? habitLog.reduce((b, h) => h.questionsAnswered > b.questionsAnswered ? h : b, habitLog[0])
        : null;

    // Month Labels
    const mLabels = [];
    const labelDate = new Date(today);
    labelDate.setMonth(labelDate.getMonth() - 11);
    for (let i = 0; i < 12; i++) {
        mLabels.push(labelDate.toLocaleString('default', { month: 'short' }));
        labelDate.setMonth(labelDate.getMonth() + 1);
    }

    // 2. Intelligent Motivation Engine
    const getMotivation = () => {
        const pickRandom = (arr) => arr[Math.floor(Math.random() * arr.length)];
        const hour = today.getHours();

        let greeting = "Ready to learn?";
        let emoji = "☀️";

        if (hour < 12) {
            greeting = pickRandom(["Good morning! Let's get some learning done.", "Rise and shine! Ready for today's goals?"]);
            emoji = pickRandom(["🌅", "☕", "🌤️"]);
        } else if (hour < 18) {
            greeting = pickRandom(["Good afternoon! Keep the momentum going.", "Halfway through the day!"]);
            emoji = pickRandom(["☀️", "😎", "🚀"]);
        } else {
            greeting = pickRandom(["Good evening! Wrap up the day with some learning.", "Late night study session?"]);
            emoji = pickRandom(["🌙", "🦉", "✨"]);
        }

        if (todayCount >= dailyTargets.questionsTarget && dailyTargets.questionsTarget > 0) {
            return { emoji: '🏆', text: "Mission accomplished! You hit all your goals for today.", color: 'text-success-500' };
        }
        if (streakDays >= 30) return { emoji: '👑', text: "Legendary! 30+ days of pure dedication.", color: 'text-warning-500' };
        if (streakDays >= 7) return { emoji: '🔥', text: "You're on fire! A week-long streak!", color: 'text-warning-500' };
        if (todayCount > 0) return { emoji: '📈', text: "Great start! Keep the momentum going.", color: 'text-brand-400' };

        return { emoji, text: greeting, color: 'text-gray-300' };
    };

    const motivation = getMotivation();
    const goalProgress = Math.min((todayCount / (dailyTargets.questionsTarget || 50)) * 100, 100);

    // 3. Intelligent Recommendations
    const recentAccuracy = testResults.slice(-5).reduce((a, r) => a + r.accuracy, 0) / Math.min(testResults.length, 5) || 0;
    let recommendationHtml = '';
    if (wQuestions > 0) {
        if (recentAccuracy < 50) {
            recommendationHtml = `<div class="bg-danger-500/10 border border-danger-500/30 rounded-xl p-4 mb-8">
                <p class="text-sm text-danger-400 flex items-center gap-2 font-medium">💡 Recommendation: Accuracy is dipping. Try switching to 'Learn Mode' and focus on explanations.</p>
            </div>`;
        } else if (state.reviewQueue && state.reviewQueue.filter(r => !r.reviewed).length > 20) {
            recommendationHtml = `<div class="bg-brand-500/10 border border-brand-500/30 rounded-xl p-4 mb-8">
                <p class="text-sm text-brand-400 flex items-center gap-2 font-medium">💡 Recommendation: You have a large backlog of missed questions. Clear your Review Queue today!</p>
            </div>`;
        }
    }

    // 4. Heatmap Generator
    const getIntensity = (count) => {
        if (count === 0) return 'bg-gray-800/40 border border-white/5';
        if (count <= 5) return 'bg-success-700/60 shadow-[0_0_4px_rgba(21,128,61,0.4)]';
        if (count <= 15) return 'bg-success-600/80 shadow-[0_0_6px_rgba(22,163,74,0.6)]';
        if (count <= 30) return 'bg-success-500 shadow-[0_0_8px_rgba(34,197,94,0.8)]';
        return 'bg-success-400 shadow-[0_0_10px_rgba(74,222,128,1)]';
    };

    let heatmapColsHtml = weeks.map((week, wi) => {
        let cellsHtml = '';
        if (wi === 0 && week[0]) {
            for (let p = 0; p < week[0].day; p++) cellsHtml += `<div class="w-3 h-3"></div>`;
        }
        week.forEach(day => {
            cellsHtml += `<div class="w-3 h-3 rounded-[2px] transition-transform hover:scale-125 cursor-pointer ${getIntensity(day.count)}" title="${day.date}: ${day.count} questions"></div>`;
        });
        return `<div class="flex flex-col gap-1">${cellsHtml}</div>`;
    }).join('');

    // 5. Recent Activity List
    const recentActivity = [...habitLog].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 7);
    const recentActivityHtml = recentActivity.length === 0
        ? '<p class="text-sm opacity-60">No activity recorded yet.</p>'
        : recentActivity.map(h => `
            <div class="flex items-center justify-between bg-black/20 border border-white/5 rounded-lg p-3">
                <div>
                    <span class="text-sm font-medium opacity-90">${h.date}</span>
                    <span class="text-xs opacity-60 ml-2 truncate max-w-[120px] inline-block align-bottom">${(h.subjects || []).join(', ')}</span>
                </div>
                <div class="flex items-center gap-4 text-sm font-medium">
                    <span class="text-brand-400">${h.questionsAnswered} Q</span>
                    <span class="text-success-500">${h.correctAnswers} ✓</span>
                </div>
            </div>
        `).join('');

    // 6. Milestones Array
    const milestones = [
        { label: 'First Quiz', target: 1, current: testResults.length, icon: '🌱' },
        { label: '10 Quizzes', target: 10, current: testResults.length, icon: '📚' },
        { label: '100 Questions', target: 100, current: totalQuestionsEver, icon: '💯' },
        { label: '500 Questions', target: 500, current: totalQuestionsEver, icon: '🚀' },
        { label: '7 Day Streak', target: 7, current: longestStreak, icon: '🔥' },
        { label: '30 Day Streak', target: 30, current: longestStreak, icon: '⭐' },
        { label: 'Best Day', target: 1, current: bestDay ? 1 : 0, icon: bestDay ? `${bestDay.questionsAnswered}Q` : '🎯' },
        { label: '1000 Questions', target: 1000, current: totalQuestionsEver, icon: '👑' },
    ];

    const milestonesHtml = milestones.map(m => {
        const achieved = m.current >= m.target;
        return `
        <div class="p-4 rounded-xl border transition-all ${achieved ? 'bg-success-500/10 border-success-500/30' : 'bg-black/20 border-white/5 opacity-60'} text-center flex flex-col justify-center items-center">
            <span class="text-2xl mb-2">${achieved ? m.icon : '🔒'}</span>
            <p class="text-xs font-medium ${achieved ? 'text-success-400' : 'opacity-70'}">${m.label}</p>
            ${!achieved ? `<p class="text-[10px] opacity-50 mt-1">${m.current}/${m.target}</p>` : ''}
        </div>`;
    }).join('');

    // SVG Icons
    const icons = {
        flame: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-warning-500 mb-2"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"></path></svg>`,
        award: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-warning-400 mb-2"><circle cx="12" cy="8" r="7"></circle><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"></polyline></svg>`,
        book: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-brand-400 mb-2"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg>`,
        cal: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-purple-400 mb-2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>`,
        trendUp: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-success-500 mb-2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"></polyline><polyline points="17 6 23 6 23 12"></polyline></svg>`,
        target: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-brand-400 mb-2"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="6"></circle><circle cx="12" cy="12" r="2"></circle></svg>`
    };

    // 7. Render DOM
    container.innerHTML = `
      <div class="max-w-6xl mx-auto p-6 animate-fade-in">
        <h1 class="text-3xl font-bold mb-2">📅 Habit Tracker</h1>
        <p class="opacity-70 mb-8">Track your daily learning consistency and build unbreakable momentum.</p>

        <!-- Motivation Banner -->
        <div class="glass-card card-shade-1 p-6 mb-8 flex items-center gap-5">
            <div class="w-16 h-16 bg-black/20 rounded-full flex items-center justify-center text-4xl shadow-inner border border-white/5">
                ${motivation.emoji}
            </div>
            <div class="flex-1">
                <h2 class="text-2xl font-bold mb-1">
                    ${streakDays > 0 ? `${streakDays} Day Streak! 🔥` : 'Start Your Streak Today! 💪'}
                </h2>
                <p class="${motivation.color} text-sm">${motivation.text}</p>

                <!-- Progress Bar -->
                <div class="mt-4">
                    <div class="flex justify-between items-center mb-1 text-xs opacity-80">
                        <span>Today's Goal: ${todayCount}/${dailyTargets.questionsTarget || 50} questions</span>
                        <span class="font-medium">${goalProgress.toFixed(0)}%</span>
                    </div>
                    <div class="w-full bg-black/30 rounded-full h-2 shadow-inner border border-white/5">
                        <div class="h-2 rounded-full transition-all duration-1000 ${goalProgress >= 100 ? 'bg-success-500 shadow-[0_0_10px_rgba(34,197,94,0.6)]' : 'bg-brand-500 shadow-[0_0_10px_rgba(139,92,246,0.6)]'}" style="width: ${goalProgress}%"></div>
                    </div>
                </div>
            </div>
        </div>

        ${recommendationHtml}

        <!-- Stats Grid -->
        <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4 mb-8">
            <div class="glass-card card-shade-2 p-4 text-center">
                ${icons.flame}
                <p class="text-2xl font-bold">${streakDays}</p>
                <p class="text-xs opacity-70 mt-1">Current Streak</p>
            </div>
            <div class="glass-card card-shade-2 p-4 text-center">
                ${icons.award}
                <p class="text-2xl font-bold">${longestStreak}</p>
                <p class="text-xs opacity-70 mt-1">Best Streak</p>
            </div>
            <div class="glass-card card-shade-2 p-4 text-center">
                ${icons.book}
                <p class="text-2xl font-bold">${wQuestions}</p>
                <p class="text-xs opacity-70 mt-1">This Week</p>
            </div>
            <div class="glass-card card-shade-2 p-4 text-center">
                ${icons.cal}
                <p class="text-2xl font-bold">${wActive}/7</p>
                <p class="text-xs opacity-70 mt-1">Active (Week)</p>
            </div>
            <div class="glass-card card-shade-2 p-4 text-center">
                ${icons.trendUp}
                <p class="text-2xl font-bold">${mQuestions}</p>
                <p class="text-xs opacity-70 mt-1">This Month</p>
            </div>
            <div class="glass-card card-shade-2 p-4 text-center">
                ${icons.target}
                <p class="text-2xl font-bold">${mActive}/30</p>
                <p class="text-xs opacity-70 mt-1">Active (Month)</p>
            </div>
        </div>

        <!-- Heatmap -->
        <div class="glass-card card-shade-3 p-6 mb-8">
            <h3 class="text-lg font-semibold mb-4 opacity-90">📊 Activity Heatmap (Last 365 Days)</h3>

            <div class="flex mb-2 ml-6">
                ${mLabels.map(m => `<span class="text-[10px] opacity-50 flex-1">${m}</span>`).join('')}
            </div>

            <div class="flex gap-1 overflow-x-auto pb-2 scrollbar-thin">
                <div class="flex flex-col gap-1 mr-2 text-[10px] opacity-50 font-mono">
                    <span class="h-3 leading-3">S</span>
                    <span class="h-3 leading-3">M</span>
                    <span class="h-3 leading-3">T</span>
                    <span class="h-3 leading-3">W</span>
                    <span class="h-3 leading-3">T</span>
                    <span class="h-3 leading-3">F</span>
                    <span class="h-3 leading-3">S</span>
                </div>
                ${heatmapColsHtml}
            </div>

            <div class="flex items-center gap-2 mt-4 text-xs opacity-60">
                <span>Less</span>
                <div class="w-3 h-3 rounded-[2px] bg-gray-800/40 border border-white/5"></div>
                <div class="w-3 h-3 rounded-[2px] bg-success-700/60"></div>
                <div class="w-3 h-3 rounded-[2px] bg-success-600/80"></div>
                <div class="w-3 h-3 rounded-[2px] bg-success-500"></div>
                <div class="w-3 h-3 rounded-[2px] bg-success-400"></div>
                <span>More</span>
            </div>
        </div>

        <div class="grid md:grid-cols-2 gap-6 mb-8">
            <!-- Recent Activity -->
            <div class="glass-card card-shade-4 p-6 flex flex-col h-full">
                <h3 class="text-lg font-semibold mb-4 opacity-90">📝 Recent Activity Log</h3>
                <div class="space-y-3 flex-1 overflow-y-auto pr-2 max-h-80">
                    ${recentActivityHtml}
                </div>
            </div>

            <!-- Milestones -->
            <div class="glass-card p-6 flex flex-col h-full">
                <h3 class="text-lg font-semibold mb-4 opacity-90 flex items-center gap-2">🏆 Milestones</h3>
                <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 flex-1">
                    ${milestonesHtml}
                </div>
            </div>
        </div>

      </div>
    `;
}

// Bind to event lifecycle
document.addEventListener('stateUpdated', initHabits);
document.addEventListener('DOMContentLoaded', () => {
    // Also re-render specifically when navigating to the habits tab to ensure layout calculations are correct
    const navBtns = document.querySelectorAll('.nav-btn');
    navBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            if (e.currentTarget.getAttribute('data-target') === 'habit-tracker-view') {
                initHabits();
            }
        });
    });

    if (window.AppState && window.AppState.fetch) window.AppState.fetch().then(initHabits);
    else initHabits();
});
