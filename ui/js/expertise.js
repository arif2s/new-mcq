// ui/js/expertise.js

const LEVEL_CONFIG = {
    beginner: { color: 'text-danger-500', bg: 'bg-danger-500/10', border: 'border-danger-500/30', emoji: '🌱' },
    intermediate: { color: 'text-warning-500', bg: 'bg-warning-500/10', border: 'border-warning-500/30', emoji: '📗' },
    advanced: { color: 'text-brand-400', bg: 'bg-brand-500/10', border: 'border-brand-500/30', emoji: '⭐' },
    expert: { color: 'text-success-500', bg: 'bg-success-500/10', border: 'border-success-500/30', emoji: '👑' }
};


function initExpertiseMap() {
    const state = window.AppState.get();
    const topicExpertise = state.topicExpertise || {};
    const subjects = state.subjects || [];

    const groups = {};
    const counts = { beginner: 0, intermediate: 0, advanced: 0, expert: 0 };
    const recs = [];

    const expertiseVals = Object.values(topicExpertise);
    const totalTopics = expertiseVals.length;

    expertiseVals.forEach(exp => {
        if (!groups[exp.subject]) groups[exp.subject] = [];
        groups[exp.subject].push(exp);

        if (counts[exp.level] !== undefined) {
            counts[exp.level]++;
        }

        if (exp.level === 'beginner' || exp.level === 'intermediate') {
            recs.push(exp);
        }
    });

    for (const subject in groups) {
        groups[subject].sort((a, b) => b.accuracy - a.accuracy);
    }

    recs.sort((a, b) => a.accuracy - b.accuracy);
    const topRecs = recs.slice(0, 5);

    const emptyContainer = document.getElementById('expertise-empty');
    const contentContainer = document.getElementById('expertise-content');

    if (totalTopics === 0) {
        if (emptyContainer) emptyContainer.classList.remove('hidden');
        if (contentContainer) contentContainer.classList.add('hidden');
        return;
    }

    if (emptyContainer) emptyContainer.classList.add('hidden');
    if (contentContainer) contentContainer.classList.remove('hidden');

    // 1. Render Distribution Grid
    const distGrid = document.getElementById('expertise-distribution-grid');
    if (distGrid) {
        distGrid.innerHTML = Object.entries(counts).map(([level, count]) => {
            const config = LEVEL_CONFIG[level];
            const pct = totalTopics > 0 ? (count / totalTopics) * 100 : 0;
            return `
                <div class="${config.bg} ${config.border} border rounded-xl p-4 text-center">
                    <span class="text-2xl">${config.emoji}</span>
                    <p class="text-2xl font-bold mt-1 ${config.color}">${count}</p>
                    <p class="text-xs text-muted capitalize">${window.escapeHTML(level)}</p>
                    <p class="text-xs text-muted">${pct.toFixed(0)}%</p>
                </div>
            `;
        }).join('');
    }

    // 2. Render Distribution Bar
    const distBar = document.getElementById('expertise-distribution-bar');
    if (distBar) {
        distBar.innerHTML = `
            <div class="bg-danger-500 transition-all" style="width: ${(counts.beginner / totalTopics) * 100}%"></div>
            <div class="bg-warning-500 transition-all" style="width: ${(counts.intermediate / totalTopics) * 100}%"></div>
            <div class="bg-brand-500 transition-all" style="width: ${(counts.advanced / totalTopics) * 100}%"></div>
            <div class="bg-success-500 transition-all" style="width: ${(counts.expert / totalTopics) * 100}%"></div>
        `;
    }

    // 3. Render Recommendations
    const recsContainer = document.getElementById('expertise-recommendations');
    if (recsContainer) {
        if (topRecs.length === 0) {
            recsContainer.innerHTML = '<p class="text-muted text-sm">All topics are at advanced level or above! 🎉</p>';
        } else {
            recsContainer.innerHTML = topRecs.map(exp => `
                <div class="flex items-center justify-between glass-panel rounded-lg p-3">
                  <div>
                    <span class="text-sm font-medium">${window.escapeHTML(exp.topic)}</span>
                    <span class="text-xs text-muted ml-2">(${window.escapeHTML(exp.subject)})</span>
                  </div>
                  <div class="flex items-center gap-2">
                    <span class="text-xs px-2 py-0.5 rounded-full ${LEVEL_CONFIG[exp.level].bg} ${LEVEL_CONFIG[exp.level].color}">
                      ${exp.accuracy.toFixed(0)}%
                    </span>
                    <span class="text-xs text-muted">→ Practice more</span>
                  </div>
                </div>
            `).join('');
        }
    }

    // 4. Render Subjects
    const subjectsContainer = document.getElementById('expertise-subjects-container');
    if (subjectsContainer) {
        subjectsContainer.innerHTML = Object.entries(groups).map(([subject, topics]) => {
            const subjectInfo = subjects.find(s => s.name === subject);
            const avgAccuracy = topics.reduce((acc, t) => acc + t.accuracy, 0) / topics.length;
            const avgClass = avgAccuracy >= 70 ? 'text-success-500' : avgAccuracy >= 50 ? 'text-warning-500' : 'text-danger-500';

            const topicsHtml = topics.map(exp => {
                const config = LEVEL_CONFIG[exp.level] || LEVEL_CONFIG.beginner;
                const accBg = exp.accuracy >= 70 ? 'bg-success-500' : exp.accuracy >= 50 ? 'bg-warning-500' : 'bg-danger-500';

                let trendIcon = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-muted"><line x1="5" y1="12" x2="19" y2="12"></line></svg>';
                if (exp.trend === 'improving') {
                    trendIcon = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-success-500"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"></polyline><polyline points="17 6 23 6 23 12"></polyline></svg>';
                } else if (exp.trend === 'declining') {
                    trendIcon = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-danger-500"><polyline points="23 18 13.5 8.5 8.5 13.5 1 6"></polyline><polyline points="17 18 23 18 23 12"></polyline></svg>';
                }

                return `
                    <div class="${config.bg} ${config.border} border rounded-lg p-4 transition-all hover:scale-[1.02]">
                        <div class="flex items-start justify-between mb-2">
                          <div>
                            <p class="text-sm font-medium">${window.escapeHTML(exp.topic)}</p>
                            <p class="text-xs capitalize ${config.color}">
                              ${config.emoji} ${window.escapeHTML(exp.level)}
                            </p>
                          </div>
                          <div class="flex items-center gap-1">
                            ${trendIcon}
                          </div>
                        </div>

                        <div class="flex items-center gap-2 mb-2">
                          <div class="flex-1 glass-panel rounded-full h-2">
                            <div class="h-2 rounded-full ${accBg}" style="width: ${exp.accuracy}%"></div>
                          </div>
                          <span class="text-xs text-muted w-10 text-right">${exp.accuracy.toFixed(0)}%</span>
                        </div>

                        <div class="flex justify-between text-xs text-muted">
                          <span>${exp.attempted}/${exp.totalQuestions} attempted</span>
                          <span>${exp.correct} correct</span>
                        </div>
                    </div>
                `;
            }).join('');

            return `
                <div class="glass-card rounded-xl p-6 mb-6">
                    <div class="flex items-center justify-between mb-4">
                      <h3 class="text-lg font-semibold">📚 ${window.escapeHTML(subject)}</h3>
                      <div class="flex items-center gap-3 text-sm">
                        <span class="text-muted">${subjectInfo?.questions?.length || 0} total questions</span>
                        <span class="font-medium ${avgClass}">Avg: ${avgAccuracy.toFixed(1)}%</span>
                      </div>
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                        ${topicsHtml}
                    </div>
                </div>
            `;
        }).join('');
    }
}

document.addEventListener('stateUpdated', initExpertiseMap);
document.addEventListener('DOMContentLoaded', () => {
    // Navigation listener specifically for Expertise tab triggers map re-render
    const navBtns = document.querySelectorAll('.nav-btn');
    navBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            if (e.target.getAttribute('data-target') === 'expertise-view') {
                initExpertiseMap();
            }
        });
    });
});
