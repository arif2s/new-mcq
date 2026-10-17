// ui/js/quiz.js

let quizConfig = {
    subject: 'all',
    topic: 'all',
    keyword: '',
    count: 10,
    timeLimit: 0,
    timeout: 5,
    mode: 'learn', // 'test', 'learn', 'target'
    spacedRepetition: true
};

let currentQuiz = null;
let currentIndex = 0;
let quizAnswers = {};
let timers = { overall: null, question: null, autoAdvance: null };
let times = { start: null, questionStart: null };
let isPaused = false;
let scores = { correct: 0, wrong: 0 };

// State for the Topic Verification Popup
let activeVerificationSession = null;

// --- Utility: Derive Card Heading from Filter / Search ---
function deriveCardHeading(cfg) {
    if (cfg.keyword && cfg.keyword.trim()) {
        return `Search: ${cfg.keyword.trim()}`;
    }
    if (cfg.topic && cfg.topic !== 'all') {
        return cfg.topic;
    }
    if (cfg.subject && cfg.subject !== 'all') {
        return cfg.subject;
    }
    return 'Mixed Clinical Set';
}

function showToast(message) {
    let toast = document.getElementById('quiz-toast-banner');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'quiz-toast-banner';
        toast.className = 'fixed bottom-6 right-6 z-50 glass-card px-5 py-3 rounded-xl border border-brand-500/40 shadow-lg text-sm font-medium transition-all';
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.style.display = 'block';
    toast.style.opacity = '1';
    clearTimeout(toast._hideTimer);
    toast._hideTimer = setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => { toast.style.display = 'none'; }, 300);
    }, 3200);
}
window.showToast = showToast;

// =========================================================
// 1. QUIZ CONFIGURATION & SETUP VIEW
// =========================================================

async function initQuizSetup() {
    const container = document.getElementById('quiz-view');
    if (!container) return;

    let subjects = [], topics = [];
    try {
        const [subRes, topRes] = await Promise.all([
            fetch('/api/mcq/subjects'),
            fetch('/api/mcq/topics')
        ]);
        if (subRes.ok) subjects = await subRes.json();
        if (topRes.ok) topics = await topRes.json();
    } catch (e) {
        console.error("Failed to load quiz filters:", e);
    }

    container.innerHTML = `
        <div class="w-full p-6 animate-fade-in">
            <div class="flex items-center justify-between mb-8">
                <div>
                    <h1 class="text-3xl font-bold mb-2 flex items-center gap-3">⚙️ Quiz Configuration &amp; Set Curator</h1>
                    <p class="subtitle">Curate question sets by CSV subject, topic, or custom keyword search.</p>
                </div>
                <button id="toggle-inspector-btn" class="secondary-btn px-4 py-2 flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="15" y1="3" x2="15" y2="21"></line></svg>
                    Toggle Inspector
                </button>
            </div>

            <div class="grid lg:grid-cols-2 gap-6 mb-8">
                <!-- 1. Source Filters -->
                <div class="glass-card card-shade-1 p-6">
                    <h3 class="text-lg font-semibold mb-4 border-b border-gray-700/50 pb-2 flex items-center gap-2">📂 1. Curate Topics &amp; Filters</h3>

                    <label class="block text-xs text-muted mb-1">CSV Subject</label>
                    <select id="cfg-subject" class="w-full glass-select rounded-lg px-3 py-2.5 text-sm mb-4">
                        <option value="all">All Subjects</option>
                        ${subjects.map(s => `<option value="${window.escapeHTML(s.subject)}" ${quizConfig.subject === s.subject ? 'selected' : ''}>${window.escapeHTML(s.subject)} (${s.count})</option>`).join('')}
                    </select>

                    <label class="block text-xs text-muted mb-1">CSV Topic</label>
                    <select id="cfg-topic" class="w-full glass-select rounded-lg px-3 py-2.5 text-sm mb-4">
                        <option value="all">All Topics</option>
                        ${topics.map(t => `<option value="${window.escapeHTML(t.topic)}" ${quizConfig.topic === t.topic ? 'selected' : ''}>${window.escapeHTML(t.topic)} (${t.count})</option>`).join('')}
                    </select>

                    <label class="block text-xs text-muted mb-1">Custom Keyword Search (FTS5)</label>
                    <input type="text" id="cfg-keyword" value="${window.escapeHTML(quizConfig.keyword)}" placeholder="e.g., cardiac amyloidosis, vagus nerve, Vicryl..." class="w-full glass-input rounded-lg px-3 py-2.5 text-sm">
                </div>

                <!-- 2. Session Rules -->
                <div class="glass-card card-shade-2 p-6">
                    <h3 class="text-lg font-semibold mb-4 border-b border-gray-700/50 pb-2 flex items-center gap-2">🕹️ 2. Session Rules</h3>

                    <label class="block text-xs text-muted mb-1">Study Mode</label>
                    <div class="flex gap-2 mb-4">
                        <button id="mode-test" class="flex-1 py-2 rounded-lg text-sm font-medium border border-gray-700/50 transition-colors ${quizConfig.mode === 'test' ? 'primary-btn' : 'secondary-btn'}">📝 Test</button>
                        <button id="mode-learn" class="flex-1 py-2 rounded-lg text-sm font-medium border border-gray-700/50 transition-colors ${quizConfig.mode === 'learn' ? 'primary-btn' : 'secondary-btn'}">📖 Learn</button>
                        <button id="mode-target" class="flex-1 py-2 rounded-lg text-sm font-medium border border-gray-700/50 transition-colors ${quizConfig.mode === 'target' ? 'primary-btn' : 'secondary-btn'}">🎯 Target</button>
                    </div>

                    <div class="flex items-center justify-between mb-1">
                        <label class="text-xs text-muted">Question Count / Target</label>
                        <span id="cfg-count-val" class="text-xs font-bold text-brand-400">${quizConfig.count}</span>
                    </div>
                    <input type="range" id="cfg-count" min="1" max="100" value="${quizConfig.count}" class="w-full mb-4 accent-brand-500">

                    <div class="flex items-center justify-between mb-1">
                        <label class="text-xs text-muted">Total Time Limit (Mins)</label>
                        <span id="cfg-time-val" class="text-xs font-bold text-brand-400">${quizConfig.timeLimit === 0 ? 'Unlimited' : quizConfig.timeLimit}</span>
                    </div>
                    <input type="range" id="cfg-time" min="0" max="120" step="5" value="${quizConfig.timeLimit}" class="w-full mb-4 accent-brand-500">

                    <label class="flex items-center gap-2 cursor-pointer group mt-2">
                        <input type="checkbox" id="cfg-sr" ${quizConfig.spacedRepetition ? 'checked' : ''} class="w-4 h-4 rounded glass-input text-brand-500 accent-brand-500">
                        <span class="text-sm opacity-80 group-hover:opacity-100 transition-opacity">🧠 Prioritize Due Spaced Repetition (FSRS v4.5) Cards</span>
                    </label>
                </div>
            </div>

            <!-- 3. Launch Action Bar -->
            <div class="glass-card card-shade-3 p-6 text-center">
                <h2 class="text-3xl font-black text-brand-400 mb-1" id="setup-match-count">...</h2>
                <p class="text-muted text-sm mb-6">Matching Questions Available</p>

                <div class="flex flex-col sm:flex-row items-center justify-center gap-4">
                    <button id="btn-start-quiz-now" class="secondary-btn px-8 py-4 font-bold rounded-xl transition-all hover:scale-105 disabled:opacity-40 w-full sm:w-auto text-base flex items-center justify-center gap-2">
                        ⚡ Start Quiz Now
                    </button>
                    <button id="btn-confirm-launch" class="primary-btn px-10 py-4 font-bold rounded-xl transition-all hover:scale-105 disabled:opacity-40 w-full sm:w-auto text-base flex items-center justify-center gap-2">
                        🔍 Confirm &amp; Launch (Review Topics)
                    </button>
                </div>
                <p class="text-xs text-muted mt-3">
                    <strong>Start Quiz Now</strong> begins immediately and adds the set to Knowledge Hub (Circle ①) without generating references yet.
                    <strong>Confirm &amp; Launch</strong> lets you verify extracted medical topics to generate references (Circle ②) and LM Studio notes (Circle ③).
                </p>
            </div>
        </div>
    `;

    const updateMatchCount = async () => {
        try {
            const res = await fetch('/api/mcq/search_count', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    subjects: quizConfig.subject === 'all' ? null : [quizConfig.subject],
                    topics: quizConfig.topic === 'all' ? null : [quizConfig.topic],
                    keyword: quizConfig.keyword || null,
                    count: 0
                })
            });
            const data = await res.json();
            const count = data.count || 0;
            const matchEl = document.getElementById('setup-match-count');
            if (matchEl) matchEl.textContent = count;

            const startNowBtn = document.getElementById('btn-start-quiz-now');
            const confirmBtn = document.getElementById('btn-confirm-launch');
            if (startNowBtn) startNowBtn.disabled = count === 0;
            if (confirmBtn) confirmBtn.disabled = count === 0;

            const countSlider = document.getElementById('cfg-count');
            if (countSlider) {
                if (quizConfig.mode !== 'target') {
                    countSlider.max = Math.max(count, 1);
                    if (parseInt(countSlider.value) > count && count > 0) {
                        countSlider.value = count;
                        quizConfig.count = count;
                        document.getElementById('cfg-count-val').textContent = count;
                    }
                } else {
                    countSlider.max = 100;
                }
            }
        } catch (e) {
            console.error(e);
        }
    };

    const bindInput = (id, key, isInt = false, callback = null) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener(el.tagName === 'INPUT' && el.type === 'range' ? 'input' : 'change', (e) => {
            quizConfig[key] = isInt ? parseInt(e.target.value) : e.target.value;
            if (callback) callback(e);
        });
    };

    bindInput('cfg-subject', 'subject', false, updateMatchCount);
    bindInput('cfg-topic', 'topic', false, updateMatchCount);

    let debounce;
    document.getElementById('cfg-keyword')?.addEventListener('input', (e) => {
        quizConfig.keyword = e.target.value;
        clearTimeout(debounce);
        debounce = setTimeout(updateMatchCount, 280);
    });

    bindInput('cfg-count', 'count', true, (e) => {
        document.getElementById('cfg-count-val').textContent = e.target.value;
    });
    bindInput('cfg-time', 'timeLimit', true, (e) => {
        document.getElementById('cfg-time-val').textContent = e.target.value === '0' ? 'Unlimited' : e.target.value;
    });

    document.getElementById('cfg-sr')?.addEventListener('change', (e) => {
        quizConfig.spacedRepetition = e.target.checked;
    });

    ['test', 'learn', 'target'].forEach(m => {
        document.getElementById(`mode-${m}`)?.addEventListener('click', () => {
            quizConfig.mode = m;
            initQuizSetup();
        });
    });

    document.getElementById('btn-start-quiz-now')?.addEventListener('click', () => handleLaunchCuratedSet(false));
    document.getElementById('btn-confirm-launch')?.addEventListener('click', () => handleLaunchCuratedSet(true));
    document.getElementById('toggle-inspector-btn')?.addEventListener('click', toggleRightSidebar);

    updateMatchCount();
}

async function fetchCuratedQuestions() {
    const payload = {
        subjects: quizConfig.subject === 'all' ? null : [quizConfig.subject],
        topics: quizConfig.topic === 'all' ? null : [quizConfig.topic],
        keyword: quizConfig.keyword || null,
        count: quizConfig.mode === 'target' ? 100 : quizConfig.count,
        spaced_repetition: quizConfig.spacedRepetition
    };

    const res = await fetch('/api/mcq/generate_set', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.status === 'success' && Array.isArray(data.questions) && data.questions.length > 0) {
        return data.questions;
    }
    return [];
}

async function handleLaunchCuratedSet(openTopicReviewPopup = false) {
    const startNowBtn = document.getElementById('btn-start-quiz-now');
    const confirmBtn = document.getElementById('btn-confirm-launch');
    if (startNowBtn) startNowBtn.disabled = true;
    if (confirmBtn) confirmBtn.disabled = true;

    try {
        const questions = await fetchCuratedQuestions();
        if (!questions.length) {
            alert("No questions found for the selected criteria.");
            return;
        }

        const sessionId = `sess_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`;
        const customTitle = deriveCardHeading(quizConfig);
        const subjectName = quizConfig.subject !== 'all'
            ? quizConfig.subject
            : (questions[0].subject_name || customTitle);

        if (!openTopicReviewPopup) {
            // "Start Quiz Now": Create session in Hub with Circle 1 active (topics_reviewed = false)
            currentQuiz = {
                sessionId,
                customTitle,
                subjectName,
                topicsReviewed: false,
                topicList: [],
                questions,
                config: { ...quizConfig }
            };
            await persistSessionToBackend({
                sessionId,
                subjectName,
                customTitle,
                questions,
                answers: [],
                topicsReviewed: false,
                topicList: [],
                isCompleted: false,
                timeUsedSeconds: 0
            });
            beginQuizRunner();
        } else {
            // "Confirm & Launch": Extract medical topics and open the Topic Verification Popup
            await openTopicVerificationModal({
                sessionId,
                subjectName,
                customTitle,
                questions,
                config: { ...quizConfig },
                existingTopicList: null,
                isExistingHubSession: false
            });
        }
    } catch (e) {
        console.error("Error launching quiz set:", e);
        alert("Failed to prepare question set.");
    } finally {
        if (startNowBtn) startNowBtn.disabled = false;
        if (confirmBtn) confirmBtn.disabled = false;
    }
}

// Global helper so Knowledge Hub Inspector's "Create Question Set" on a weak topic works in 1 click
window.startCustomTopicQuiz = async function(topicName) {
    quizConfig.subject = 'all';
    quizConfig.topic = 'all';
    quizConfig.keyword = topicName;
    quizConfig.mode = 'learn';
    quizConfig.count = 15;

    const navBtn = document.querySelector('.nav-btn[data-target="quiz-view"]');
    if (navBtn) navBtn.click();
    await initQuizSetup();
    showToast(`Curated question set for "${topicName}"`);
};

// =========================================================
// 2. TOPIC VERIFICATION POPUP (Black vs Grey, Green Outline, Double-Click Ignore, Auto-Save Outside Click)
// =========================================================

async function extractSessionTopicsFromBackend(questions) {
    try {
        const res = await fetch('/api/queue/extract_topics', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                questions: questions.map(q => ({
                    question: q.question || q.question_text || '',
                    topic_name: q.topic_name || q.topic || 'General',
                    option_a: q.option_a || q.opt_a || '',
                    option_b: q.option_b || q.opt_b || '',
                    option_c: q.option_c || q.opt_c || '',
                    option_d: q.option_d || q.opt_d || '',
                    correct_answer: q.correct_answer || 'A',
                    explanation: q.explanation || ''
                }))
            })
        });
        if (res.ok) {
            const data = await res.json();
            if (Array.isArray(data.questions)) {
                data.questions.forEach((qRes, idx) => {
                    if (questions[idx]) {
                        questions[idx].topics = qRes.topics || [];
                        questions[idx].topic_details = qRes.topic_details || [];
                    }
                });
            }
            if (Array.isArray(data.session_topics) && data.session_topics.length > 0) {
                return data.session_topics;
            }
            // Consolidate from per-question details or topics array
            const topicMap = new Map();
            (data.questions || []).forEach(qItem => {
                const details = qItem.topic_details || (qItem.topics || []).map(t => ({
                    name: t,
                    verified: t !== 'General Medical Concept',
                    selected: t !== 'General Medical Concept'
                }));
                details.forEach(d => {
                    if (!d.name || d.name === 'General Medical Concept') return;
                    const key = d.name.toLowerCase();
                    if (!topicMap.has(key)) {
                        topicMap.set(key, {
                            name: d.name,
                            verified: Boolean(d.verified),
                            selected: d.selected !== undefined ? Boolean(d.selected) : Boolean(d.verified),
                            confidence: d.verified ? 'high' : 'low'
                        });
                    } else if (d.verified) {
                        const ex = topicMap.get(key);
                        ex.verified = true;
                        ex.selected = true;
                    }
                });
            });
            return Array.from(topicMap.values());
        }
    } catch (e) {
        console.warn("Batch topic extraction fallback:", e);
    }
    return [];
}

window.openTopicVerificationModal = async function(sessionContext) {
    const modal = document.getElementById('topic-editor-modal');
    if (!modal) return;

    activeVerificationSession = {
        sessionId: sessionContext.sessionId,
        subjectName: sessionContext.subjectName || 'Mixed Session',
        customTitle: sessionContext.customTitle || sessionContext.subjectName || 'Study Set',
        questions: sessionContext.questions || [],
        config: sessionContext.config || { ...quizConfig },
        isExistingHubSession: Boolean(sessionContext.isExistingHubSession),
        topicList: [],
        existingNotesSet: new Set()
    };

    modal.style.display = 'flex';
    const contentBox = document.getElementById('topic-editor-questions');
    if (contentBox) {
        contentBox.innerHTML = `
            <div class="text-center py-10 text-muted animate-pulse">
                Extracting and canonicalizing medical entities via medSpaCy &amp; ZIM dictionary...
            </div>
        `;
    }

    // Use saved draft topic list if reopening an unconfirmed Hub card; otherwise extract fresh
    let topicList = [];
    if (Array.isArray(sessionContext.existingTopicList) && sessionContext.existingTopicList.length > 0) {
        topicList = sessionContext.existingTopicList.map(item => (
            typeof item === 'string'
                ? { name: item, verified: true, selected: true }
                : { name: item.name, verified: Boolean(item.verified), selected: Boolean(item.selected) }
        ));
    } else {
        topicList = await extractSessionTopicsFromBackend(activeVerificationSession.questions);
    }

    // Check which topics already have synthesized notes in SQLite
    try {
        const statusRes = await fetch('/api/mcq/topics_status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ topics: topicList.map(t => t.name) })
        });
        if (statusRes.ok) {
            const stData = await statusRes.json();
            (stData.existing_topics || []).forEach(t => activeVerificationSession.existingNotesSet.add(t.toLowerCase()));
            const ignoredLower = new Set((stData.ignored_topics || []).map(t => t.toLowerCase()));
            topicList = topicList.filter(t => !ignoredLower.has(t.name.toLowerCase()));
        }
    } catch (_) {}

    activeVerificationSession.topicList = topicList;
    renderTopicVerificationModalDOM();
};

function renderTopicVerificationModalDOM() {
    const modal = document.getElementById('topic-editor-modal');
    if (!modal || !activeVerificationSession) return;

    const session = activeVerificationSession;
    const selectedCount = session.topicList.filter(t => t.selected).length;

    const modalCard = modal.querySelector('.glass-modal');
    if (!modalCard) return;

    modalCard.innerHTML = `
        <div class="p-5 border-b border-gray-700 flex justify-between items-center glass-panel rounded-t-24">
            <div>
                <h2 class="text-xl font-bold flex items-center gap-2">
                    🩺 Verify Medical Topics — <span class="text-brand-400">${window.escapeHTML(session.customTitle)}</span>
                </h2>
                <p class="text-xs text-muted mt-1">
                    <strong>Single-click</strong> to select/deselect (green outline &amp; underline).
                    <strong>Double-click</strong> to permanently ignore false-positive terms.
                    Click outside this popup at any time to auto-save your draft to Knowledge Hub.
                </p>
            </div>
            <button id="close-topic-editor-btn" class="text-muted hover:text-brand-400 transition-colors bg-transparent border-none cursor-pointer p-1" title="Save Draft to Hub &amp; Close">
                ✕
            </button>
        </div>

        <div class="p-6 overflow-y-auto flex-1 space-y-5" id="topic-editor-content">
            <!-- Legend & Quick Bulk Controls -->
            <div class="flex flex-wrap items-center justify-between gap-3 glass-panel p-3 rounded-xl border border-gray-700/50 text-xs">
                <div class="flex flex-wrap items-center gap-4">
                    <span class="flex items-center gap-1.5">
                        <span class="w-3 h-3 rounded-full bg-success-500 inline-block"></span>
                        <strong>Green Outline + Underline:</strong> Selected for Notes (${selectedCount} / ${session.topicList.length})
                    </span>
                    <span class="flex items-center gap-1.5">
                        <span class="font-bold">Normal Text:</span> Verified Medical Topic (ZIM/Morphology)
                    </span>
                    <span class="flex items-center gap-1.5 opacity-60">
                        <span>Grey Text:</span> Doubtful Term (Regex/medSpaCy only)
                    </span>
                </div>
                <div class="flex gap-2">
                    <button type="button" id="tp-select-verified-btn" class="secondary-btn px-2.5 py-1 text-xs">Select Verified Only</button>
                    <button type="button" id="tp-select-all-btn" class="secondary-btn px-2.5 py-1 text-xs">Select All</button>
                    <button type="button" id="tp-deselect-all-btn" class="secondary-btn px-2.5 py-1 text-xs">Clear Selection</button>
                </div>
            </div>

            <!-- Topic Chips Container -->
            <div id="topic-editor-questions" class="flex flex-wrap gap-2.5 min-h-[120px] p-4 glass-panel rounded-2xl border border-gray-700/50">
                ${session.topicList.length === 0
                    ? '<p class="text-sm text-muted m-auto">No medical topics extracted. Add a custom topic below.</p>'
                    : session.topicList.map((t, idx) => {
                        const hasNote = session.existingNotesSet.has(t.name.toLowerCase());
                        // Normal/Black text for verified vs Grey muted text for doubtful
                        const textClass = t.verified ? 'font-semibold text-gray-100' : 'opacity-60 text-muted font-normal';
                        // Green outline and green underline when selected
                        const selectedClass = t.selected
                            ? 'border-2 border-success-500 underline decoration-success-500 decoration-2 bg-success-500/15 shadow-sm'
                            : 'border border-gray-700 glass-panel';

                        return `
                            <div class="topic-verify-chip inline-flex items-center gap-2 px-3.5 py-2 rounded-xl text-sm cursor-pointer select-none transition-all ${textClass}${selectedClass}"
                                 data-idx="${idx}"
                                 title="Single-click: Toggle selection | Double-click: Add '${window.escapeHTML(t.name)}' to permanent Ignore List">
                                <span class="chip-label">${window.escapeHTML(t.name)}</span>${hasNote ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-success-500/20 text-success-300 no-underline" title="Notes already synthesized">✓ Cached</span>' : ''}
                                <button type="button" class="chip-edit-btn text-xs opacity-60 hover:opacity-100 bg-transparent border-none cursor-pointer ml-1 no-underline" data-idx="${idx}" title="Edit topic name">✎</button>
                            </div>
                        `;
                    }).join('')}
            </div>

            <!-- Add Custom Topic Bar -->
            <div class="pt-3 border-t border-gray-700/50 flex flex-col sm:flex-row gap-3 items-stretch sm:items-center">
                <span class="text-sm font-medium whitespace-nowrap">➕ Add or Correct Topic:</span>
                <div class="flex flex-1 gap-2">
                    <input type="text" id="custom-topic-input" class="glass-input flex-1 py-2 px-3 text-sm" placeholder="e.g., Cardiac amyloidosis, Acute appendicitis...">
                    <button type="button" id="add-custom-topic-btn" class="secondary-btn py-2 px-4 text-sm whitespace-nowrap">
                        + Add Topic
                    </button>
                </div>
            </div>
        </div>

        <div class="p-4 border-t border-gray-700 flex flex-wrap justify-between items-center gap-3 glass-panel rounded-b-24">
            <span class="text-xs text-muted">
                Clicking outside this modal saves your partial review to Knowledge Hub (Circle ①).
            </span>
            <div class="flex flex-wrap gap-3">
                <button type="button" id="confirm-add-hub-btn" class="secondary-btn px-5 py-2.5 text-sm font-semibold flex items-center gap-2 cursor-pointer">
                    📥 Confirm &amp; Add to Hub
                </button>
                <button type="button" id="confirm-start-quiz-btn" class="primary-btn px-6 py-2.5 text-sm font-bold flex items-center gap-2 cursor-pointer">
                    ▶️ Confirm &amp; Start Quiz
                </button>
            </div>
        </div>
    `;

    // Wire single-click (toggle green outline/underline) vs double-click (permanent ignore list)
    modalCard.querySelectorAll('.topic-verify-chip').forEach(chipEl => {
        let clickTimer = null;
        const idx = parseInt(chipEl.dataset.idx, 10);

        chipEl.addEventListener('click', (e) => {
            if (e.target.closest('.chip-edit-btn') || e.target.tagName === 'INPUT') return;
            if (clickTimer) clearTimeout(clickTimer);
            clickTimer = setTimeout(() => {
                const item = session.topicList[idx];
                if (item) {
                    item.selected = !item.selected;
                    renderTopicVerificationModalDOM();
                }
            }, 210);
        });

        chipEl.addEventListener('dblclick', async (e) => {
            if (e.target.closest('.chip-edit-btn') || e.target.tagName === 'INPUT') return;
            if (clickTimer) clearTimeout(clickTimer);
            const item = session.topicList[idx];
            if (!item) return;

            const removedName = item.name;
            session.topicList.splice(idx, 1);
            renderTopicVerificationModalDOM();

            try {
                await fetch('/api/queue/ignore_topic', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ topic: removedName })
                });
                showToast(`Added "${removedName}" to permanent Ignore List.`);
            } catch (err) {
                console.error("Failed to ignore topic:", err);
            }
        });
    });

    // Wire inline Edit (✎) button
    modalCard.querySelectorAll('.chip-edit-btn').forEach(editBtn => {
        editBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const idx = parseInt(editBtn.dataset.idx, 10);
            const item = session.topicList[idx];
            if (!item) return;

            const newName = prompt("Edit medical topic name:", item.name);
            if (newName && newName.trim()) {
                item.name = newName.trim();
                item.verified = true;
                item.selected = true;
                renderTopicVerificationModalDOM();
            }
        });
    });

    // Bulk selection buttons
    document.getElementById('tp-select-verified-btn')?.addEventListener('click', () => {
        session.topicList.forEach(t => { t.selected = Boolean(t.verified); });
        renderTopicVerificationModalDOM();
    });
    document.getElementById('tp-select-all-btn')?.addEventListener('click', () => {
        session.topicList.forEach(t => { t.selected = true; });
        renderTopicVerificationModalDOM();
    });
    document.getElementById('tp-deselect-all-btn')?.addEventListener('click', () => {
        session.topicList.forEach(t => { t.selected = false; });
        renderTopicVerificationModalDOM();
    });

    // Add custom topic
    const addCustomTopic = () => {
        const input = document.getElementById('custom-topic-input');
        const val = (input?.value || '').trim();
        if (!val) return;
        const existing = session.topicList.find(t => t.name.toLowerCase() === val.toLowerCase());
        if (existing) {
            existing.selected = true;
            existing.verified = true;
        } else {
            session.topicList.unshift({ name: val, verified: true, selected: true, confidence: 'high' });
        }
        input.value = '';
        renderTopicVerificationModalDOM();
    };

    document.getElementById('add-custom-topic-btn')?.addEventListener('click', addCustomTopic);
    document.getElementById('custom-topic-input')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            addCustomTopic();
        }
    });

    // Close button -> Auto-save draft to Hub
    document.getElementById('close-topic-editor-btn')?.addEventListener('click', () => {
        finalizeTopicModalAction('save_draft');
    });

    // Confirm & Add to Hub
    document.getElementById('confirm-add-hub-btn')?.addEventListener('click', () => {
        finalizeTopicModalAction('confirm_hub');
    });

    // Confirm & Start Quiz
    document.getElementById('confirm-start-quiz-btn')?.addEventListener('click', () => {
        finalizeTopicModalAction('confirm_start');
    });

    // Backdrop Click (Click outside popup) -> Auto-saves draft to Knowledge Hub!
    modal.onclick = (e) => {
        if (e.target === modal) {
            finalizeTopicModalAction('save_draft');
        }
    };
}

async function finalizeTopicModalAction(actionMode) {
    const modal = document.getElementById('topic-editor-modal');
    if (modal) modal.style.display = 'none';
    if (!activeVerificationSession) return;

    const session = activeVerificationSession;
    activeVerificationSession = null;

    const isConfirmed = (actionMode === 'confirm_hub' || actionMode === 'confirm_start');

    if (session.isExistingHubSession) {
        // Updating topics on an existing Knowledge Hub card
        try {
            await fetch(`/api/sessions/${encodeURIComponent(session.sessionId)}/update_topics`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    confirm: isConfirmed,
                    topics_reviewed: isConfirmed,
                    topic_list: session.topicList
                })
            });
        } catch (e) {
            console.error("Failed to update session topics:", e);
        }

        if (actionMode === 'confirm_start') {
            currentQuiz = {
                sessionId: session.sessionId,
                customTitle: session.customTitle,
                subjectName: session.subjectName,
                topicsReviewed: true,
                topicList: session.topicList,
                questions: session.questions,
                config: session.config || { ...quizConfig }
            };
            const navBtn = document.querySelector('.nav-btn[data-target="quiz-view"]');
            if (navBtn) navBtn.click();
            beginQuizRunner();
        } else {
            showToast(isConfirmed
                ? "Topics confirmed! Generating references & notes in background."
                : "Draft topic selections saved to Knowledge Hub.");
            if (typeof window.loadSessions === 'function') window.loadSessions();
        }
        return;
    }

    // New session created from Quiz Configuration
    await persistSessionToBackend({
        sessionId: session.sessionId,
        subjectName: session.subjectName,
        customTitle: session.customTitle,
        questions: session.questions,
        answers: [],
        topicsReviewed: isConfirmed,
        topicList: session.topicList,
        isCompleted: false,
        timeUsedSeconds: 0
    });

    if (actionMode === 'confirm_start') {
        currentQuiz = {
            sessionId: session.sessionId,
            customTitle: session.customTitle,
            subjectName: session.subjectName,
            topicsReviewed: true,
            topicList: session.topicList,
            questions: session.questions,
            config: session.config || { ...quizConfig }
        };
        beginQuizRunner();
    } else if (actionMode === 'confirm_hub') {
        showToast("Set added to Knowledge Hub! Generating references & notes.");
        const hubNav = document.querySelector('.nav-btn[data-target="knowledge-hub-view"]');
        if (hubNav) hubNav.click();
        if (typeof window.loadSessions === 'function') window.loadSessions();
    } else {
        // Click outside popup -> saved draft to Hub
        showToast("Topic draft saved to Knowledge Hub (Circle ①). You can refine it anytime from the Hub.");
        if (typeof window.loadSessions === 'function') window.loadSessions();
    }
}

async function persistSessionToBackend({
    sessionId,
    subjectName,
    customTitle,
    questions,
    answers,
    topicsReviewed,
    topicList,
    isCompleted,
    timeUsedSeconds
}) {
    const totalQ = questions.length;
    const correctCount = answers.filter(a => a.isCorrect).length;
    const wrongCount = answers.filter(a => !a.isCorrect && !a.timedOut).length;
    const unansweredCount = Math.max(0, totalQ - answers.length);
    const accuracy = answers.length > 0 ? (correctCount / answers.length) * 100 : 0;

    const payload = {
        id: sessionId,
        subjectName: subjectName || 'Mixed Topics',
        customTitle: customTitle || subjectName || 'Study Set',
        date: new Date().toISOString().split('T')[0],
        timestamp: Date.now(),
        lastAttempted: answers.length > 0 ? Date.now() : 0,
        totalQuestions: totalQ,
        correctAnswers: correctCount,
        wrongAnswers: wrongCount,
        unanswered: unansweredCount,
        score: correctCount,
        maxScore: totalQ,
        accuracy,
        timeLimitSeconds: (quizConfig.timeLimit || 0) * 60,
        timeUsedSeconds: timeUsedSeconds || 0,
        mode: quizConfig.mode || 'learn',
        isCompleted: Boolean(isCompleted),
        topicsReviewed: Boolean(topicsReviewed),
        topicList: topicList || [],
        questions,
        answers
    };

    try {
        await fetch('/api/sessions/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
    } catch (e) {
        console.error("Failed to save session to backend:", e);
    }
    return payload;
}

// =========================================================
// 3. ACTIVE QUIZ RUNNER & RIGHT SIDEBAR INSPECTOR
// =========================================================

window.launchPreparedQuiz = function(preparedQuizData) {
    currentQuiz = {
        sessionId: preparedQuizData.sessionId || `sess_${Date.now()}`,
        customTitle: preparedQuizData.customTitle || preparedQuizData.config?.subjectName || 'Retake Session',
        subjectName: preparedQuizData.config?.subjectName || 'Mixed Session',
        topicsReviewed: Boolean(preparedQuizData.topicsReviewed),
        topicList: preparedQuizData.topicList || [],
        questions: preparedQuizData.questions || [],
        config: { ...quizConfig, ...(preparedQuizData.config || {}) }
    };
    const navBtn = document.querySelector('.nav-btn[data-target="quiz-view"]');
    if (navBtn) navBtn.click();
    beginQuizRunner();
};

function beginQuizRunner() {
    currentIndex = 0;
    quizAnswers = {};
    scores = { correct: 0, wrong: 0 };
    isPaused = false;
    times.start = Date.now();

    clearInterval(timers.overall);
    if (quizConfig.timeLimit > 0) {
        let timeLeft = quizConfig.timeLimit * 60;
        timers.overall = setInterval(() => {
            if (isPaused) return;
            timeLeft--;
            if (timeLeft <= 0) finishQuiz();
            else {
                const el = document.getElementById('active-timer');
                if (el) el.textContent = `${Math.floor(timeLeft / 60).toString().padStart(2, '0')}:${(timeLeft % 60).toString().padStart(2, '0')}`;
            }
        }, 1000);
    }

    renderQuestion();
}

function renderQuestion() {
    const container = document.getElementById('quiz-view');
    if (!container || !currentQuiz || !currentQuiz.questions.length) return;

    const q = currentQuiz.questions[currentIndex];
    const isAnswered = !!quizAnswers[q.id];

    if (!isAnswered) {
        times.questionStart = Date.now();
        resetQuestionTimeout();
    }
    updateInspectorForQuestion(q);

    const isTarget = quizConfig.mode === 'target';
    const isLearn = quizConfig.mode === 'learn';

    const headerText = isTarget
        ? `Target: ${scores.correct} / ${quizConfig.count} Correct`
        : `Question ${currentIndex + 1} of ${currentQuiz.questions.length}`;

    const progressPct = isTarget
        ? (scores.correct / quizConfig.count) * 100
        : (Object.keys(quizAnswers).length / currentQuiz.questions.length) * 100;

    container.innerHTML = `
        <div class="w-full p-6 animate-fade-in flex flex-col h-full">
            <div class="flex items-center justify-between mb-6 flex-wrap gap-4">
                <div class="flex items-center gap-4">
                    <div class="glass-panel border border-gray-700/50 rounded-xl px-5 py-2.5 shadow-inner">
                        <span class="text-brand-400 font-black text-lg tracking-wide">${headerText}</span>
                    </div>
                    <div class="flex items-center gap-4 text-sm font-bold glass-panel px-4 py-2.5 rounded-xl border border-gray-700/50">
                        <span class="text-success-500 flex items-center gap-1.5">✓ ${scores.correct}</span>
                        <span class="opacity-30">|</span>
                        <span class="text-danger-500 flex items-center gap-1.5">✗ ${scores.wrong}</span>
                    </div>
                </div>
                <div class="flex items-center gap-3">
                    <button id="toggle-inspector-active" class="secondary-btn px-3 py-2 text-xs flex items-center gap-1.5" title="Show/Hide Reference Inspector">
                        📚 References
                    </button>
                    ${quizConfig.timeLimit > 0 ? `<div id="active-timer" class="px-4 py-2 glass-panel border border-gray-700 rounded-xl font-mono text-lg font-bold text-brand-400 shadow-inner">--:--</div>` : ''}
                </div>
            </div>

            <div class="w-full glass-panel rounded-full h-2 mb-6 shadow-inner border border-gray-700/50 overflow-hidden">
                <div class="h-2 rounded-full transition-all duration-500 bg-gradient-to-r from-brand-500 to-purple-500" style="width: ${Math.min(progressPct, 100)}%"></div>
            </div>

            <div class="glass-card card-shade-1 p-8 mb-6 relative overflow-hidden flex-shrink-0">
                <div class="mb-4 flex items-center justify-between gap-2">
                    <span class="px-3 py-1 bg-brand-500/20 text-brand-300 rounded-full text-xs font-bold uppercase tracking-wider border border-brand-500/30">
                        ${window.escapeHTML(q.csv_topic_name || q.subject_name || currentQuiz.subjectName || currentQuiz.customTitle || 'General')}
                    </span>
                    <span class="px-3 py-1 glass-panel text-muted rounded-full text-xs font-bold uppercase tracking-wider border border-gray-700/50">
                        Mode: ${quizConfig.mode}
                    </span>
                </div>
                <h2 class="text-xl font-semibold leading-relaxed mb-6">${window.escapeHTML(q.question)}</h2>

                <div class="space-y-3" id="options-container"></div>
            </div>

            <div id="feedback-container" class="flex-1 overflow-y-auto pr-2 mb-4 space-y-4"></div>

            <div class="flex justify-between items-center mt-auto pt-4 border-t border-gray-700/50">
                <button id="btn-prev" class="secondary-btn px-6 py-3 rounded-xl disabled:opacity-30 font-medium" ${currentIndex === 0 ? 'disabled' : ''}>
                    ← Previous
                </button>
                <button id="btn-next" class="primary-btn px-8 py-3 rounded-xl transition-all shadow-lg font-bold disabled:opacity-30" ${!isAnswered && !isTarget ? 'disabled' : ''}>
                    ${currentIndex >= currentQuiz.questions.length - 1 && !isTarget ? 'Finish Quiz 🏁' : 'Next Question →'}
                </button>
            </div>
        </div>
    `;

    // Render Options A-D
    const optsDiv = document.getElementById('options-container');
    ['A', 'B', 'C', 'D'].forEach(opt => {
        const text = q[`option_${opt.toLowerCase()}`] || q[`opt_${opt.toLowerCase()}`];
        if (!text) return;

        const btn = document.createElement('button');
        let classes = 'w-full text-left px-5 py-4 rounded-xl border transition-all flex items-center justify-between font-medium ';

        if (!isAnswered) {
            classes += 'glass-card border-gray-700/50 hover:border-brand-500 cursor-pointer';
            btn.onclick = () => handleSelectOption(q, opt);
        } else {
            classes += 'cursor-default ';
            const ans = quizAnswers[q.id];
            const isCorrectOpt = opt === (q.correct_answer || 'A').toUpperCase();
            const isSelected = ans.selected === opt;

            if (ans.timedOut) classes += 'glass-panel border-gray-700/50 opacity-50';
            else if (isCorrectOpt) classes += 'bg-success-500/20 border-success-500/50 text-success-300';
            else if (isSelected && !ans.isCorrect) classes += 'bg-danger-500/20 border-danger-500/50 text-danger-300';
            else classes += 'glass-panel border-gray-700/50 opacity-50';
        }

        btn.className = classes;
        btn.innerHTML = `
            <div class="flex items-center gap-4">
                <span class="w-8 h-8 rounded-lg flex items-center justify-center text-sm font-bold glass-panel">
                    ${opt}
                </span>
                <span class="leading-snug">${window.escapeHTML(text)}</span>
            </div>
            ${isAnswered && !quizAnswers[q.id].timedOut && opt === (q.correct_answer || 'A').toUpperCase() ? '<span class="text-success-500 font-bold text-lg">✓</span>' : ''}
            ${isAnswered && !quizAnswers[q.id].timedOut && quizAnswers[q.id].selected === opt && opt !== (q.correct_answer || 'A').toUpperCase() ? '<span class="text-danger-500 font-bold text-lg">✗</span>' : ''}
        `;
        optsDiv.appendChild(btn);
    });

    // Learn Mode Feedback: Show CSV Explanation + LLM Detailed Explanation (if LLM enabled)
    if (isAnswered && isLearn && !quizAnswers[q.id].timedOut) {
        const fbContainer = document.getElementById('feedback-container');
        const llmEnabled = window.AppState ? window.AppState.get().llmEnabled !== false : true;
        let fbHtml = '';

        if (q.explanation && q.explanation !== 'No explanation provided.') {
            fbHtml += `
                <div class="animate-fade-in glass-panel border border-gray-700/50 rounded-2xl p-5">
                    <h4 class="font-bold text-brand-300 text-xs uppercase tracking-wider mb-2">📄 CSV Baseline Explanation</h4>
                    <div class="prose prose-sm max-w-none">${window.renderMarkdownAndMath(q.explanation)}</div>
                </div>
            `;
        }

        if (llmEnabled && q.llm_explanation) {
            fbHtml += `
                <div class="animate-fade-in bg-brand-600/10 border border-brand-500/30 rounded-2xl p-5">
                    <h4 class="font-bold text-brand-400 text-xs uppercase tracking-wider mb-2">🤖 LM Studio Detailed Clinical Breakdown</h4>
                    <div class="prose prose-sm max-w-none">${window.renderMarkdownAndMath(q.llm_explanation)}</div>
                </div>
            `;
        } else if (!fbHtml) {
            fbHtml = `
                <div class="animate-fade-in glass-panel border border-gray-700/50 rounded-2xl p-5 text-sm text-muted">
                    Check the Right Sidebar Inspector for verified textbook, Obsidian, and ZIM references on this question.
                </div>
            `;
        }

        if (fbContainer) fbContainer.innerHTML = fbHtml;
    }

    document.getElementById('btn-prev').onclick = () => {
        if (currentIndex > 0) {
            currentIndex--;
            renderQuestion();
        }
    };

    const nextBtn = document.getElementById('btn-next');
    if (isTarget) {
        nextBtn.textContent = 'Skip';
        nextBtn.onclick = () => handleSelectOption(q, null, true);
        if (isAnswered) nextBtn.style.display = 'none';
    } else {
        nextBtn.onclick = () => {
            if (currentIndex < currentQuiz.questions.length - 1) {
                currentIndex++;
                renderQuestion();
            } else {
                finishQuiz();
            }
        };
    }

    document.getElementById('toggle-inspector-active')?.addEventListener('click', toggleRightSidebar);
}

function handleSelectOption(q, option, isTimeout = false) {
    clearTimeout(timers.autoAdvance);
    clearInterval(timers.question);

    const correctOpt = (q.correct_answer || 'A').toUpperCase();
    const isCorrect = !isTimeout && option === correctOpt;
    const timeSpentMs = Date.now() - times.questionStart;

    quizAnswers[q.id] = {
        selected: option,
        isCorrect,
        timeSpentMs,
        timedOut: isTimeout,
        timestamp: Date.now()
    };

    if (isCorrect) scores.correct++;
    else scores.wrong++;

    // FSRS v4.5 Spaced Repetition Scoring (supports string IDs like "q_0_...")
    if (quizConfig.spacedRepetition && !isTimeout) {
        const timeSec = timeSpentMs / 1000;
        let rating = 3; // Good
        if (!isCorrect) rating = 1; // Again
        else if (timeSec > 30) rating = 2; // Hard
        else if (timeSec < 8) rating = 4; // Easy

        fetch('/api/mcq/score', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mcq_id: String(q.id), rating })
        }).catch(console.error);
    }

    renderQuestion();

    if (quizConfig.mode === 'target' && scores.correct >= quizConfig.count) {
        timers.autoAdvance = setTimeout(finishQuiz, 1200);
        return;
    }

    if (quizConfig.mode !== 'learn') {
        timers.autoAdvance = setTimeout(() => {
            if (currentIndex < currentQuiz.questions.length - 1) {
                currentIndex++;
                renderQuestion();
            } else {
                finishQuiz();
            }
        }, 1100);
    }
}

function resetQuestionTimeout() {
    clearInterval(timers.question);
    if (quizConfig.timeout <= 0) return;

    timers.question = setInterval(() => {
        if (isPaused) return;
        const elapsed = Math.floor((Date.now() - times.questionStart) / 1000);
        if (elapsed >= (quizConfig.timeout * 60)) triggerBreakModal();
    }, 1000);
}

function triggerBreakModal() {
    isPaused = true;
    clearInterval(timers.question);
    const container = document.getElementById('quiz-view');
    if (!container) return;

    container.innerHTML = `
        <div class="max-w-2xl mx-auto p-6 flex items-center h-full">
            <div class="glass-card card-shade-3 w-full text-center p-10 animate-fade-in">
                <h2 class="text-3xl font-black mb-4">☕ Need a Break?</h2>
                <p class="text-muted mb-8 text-lg">No answer recorded for ${quizConfig.timeout} minutes. Your progress is safe.</p>
                <div class="flex flex-col gap-3 max-w-sm mx-auto">
                    <button id="break-resume" class="primary-btn w-full py-4 font-bold rounded-xl">▶️ Resume Session</button>
                    <button id="break-skip" class="secondary-btn w-full py-3 rounded-xl">⏭️ Skip Question</button>
                    <button id="break-end" class="w-full py-2 text-muted hover:text-brand-400 transition-colors mt-2 bg-transparent border-none cursor-pointer">End Session &amp; Save Results</button>
                </div>
            </div>
        </div>
    `;

    document.getElementById('break-resume').onclick = () => {
        isPaused = false;
        times.questionStart = Date.now();
        renderQuestion();
    };
    document.getElementById('break-skip').onclick = () => {
        isPaused = false;
        handleSelectOption(currentQuiz.questions[currentIndex], null, true);
    };
    document.getElementById('break-end').onclick = finishQuiz;
}

// =========================================================
// 4. RESULTS & RIGHT SIDEBAR CLINICAL INSPECTOR
// =========================================================

async function finishQuiz() {
    clearInterval(timers.overall);
    clearInterval(timers.question);
    clearTimeout(timers.autoAdvance);

    if (!currentQuiz) return;

    const answeredQuestions = [];
    const answersList = [];

    currentQuiz.questions.forEach(q => {
        const ans = quizAnswers[q.id];
        if (ans) {
            answeredQuestions.push(q);
            answersList.push({
                questionId: String(q.id),
                selected: ans.selected || "",
                isCorrect: Boolean(ans.isCorrect),
                timedOut: Boolean(ans.timedOut),
                timeSpentMs: Number(ans.timeSpentMs || 0),
                timestamp: Number(ans.timestamp || Date.now())
            });
        }
    });

    const timeUsed = Math.floor((Date.now() - times.start) / 1000);

    const savedPayload = await persistSessionToBackend({
        sessionId: currentQuiz.sessionId || crypto.randomUUID(),
        subjectName: currentQuiz.subjectName,
        customTitle: currentQuiz.customTitle,
        questions: currentQuiz.questions,
        answers: answersList,
        topicsReviewed: currentQuiz.topicsReviewed,
        topicList: currentQuiz.topicList,
        isCompleted: true,
        timeUsedSeconds: timeUsed
    });

    if (window.AppState && window.AppState.recordQuizCompletion) {
        window.AppState.recordQuizCompletion(savedPayload, answeredQuestions, quizAnswers);
    }

    renderResults(savedPayload, answeredQuestions);
}

function renderResults(result, answeredQs) {
    const container = document.getElementById('quiz-view');
    if (!container) return;

    const wrongQs = answeredQs.filter(q => {
        const a = quizAnswers[q.id];
        return a && !a.isCorrect && !a.timedOut;
    });
    const llmEnabled = window.AppState ? window.AppState.get().llmEnabled !== false : true;

    container.innerHTML = `
        <div class="w-full p-6 animate-fade-in">
            <div class="text-center mb-10">
                <h1 class="text-4xl font-black mb-2">${result.accuracy >= 70 ? '🎉 Excellent Work!' : '💪 Keep Practicing!'}</h1>
                <p class="subtitle text-lg">Session completed for <span class="text-brand-400 font-bold">${window.escapeHTML(result.customTitle || result.subjectName)}</span></p>
            </div>

            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-10">
                <div class="glass-card card-shade-1 text-center p-6">
                    <p class="text-3xl font-black text-brand-400 mb-1">${result.accuracy.toFixed(1)}%</p>
                    <p class="text-xs text-muted uppercase">Accuracy</p>
                </div>
                <div class="glass-card card-shade-2 text-center p-6">
                    <p class="text-3xl font-black text-success-500 mb-1">${result.correctAnswers}</p>
                    <p class="text-xs text-muted uppercase">Correct</p>
                </div>
                <div class="glass-card card-shade-3 text-center p-6">
                    <p class="text-3xl font-black text-danger-500 mb-1">${result.wrongAnswers}</p>
                    <p class="text-xs text-muted uppercase">Wrong</p>
                </div>
                <div class="glass-card card-shade-4 text-center p-6">
                    <p class="text-3xl font-black mb-1">${Math.floor(result.timeUsedSeconds / 60)}m ${result.timeUsedSeconds % 60}s</p>
                    <p class="text-xs text-muted uppercase">Time Taken</p>
                </div>
            </div>

            <div class="flex flex-wrap gap-4 justify-center mb-10 pb-10 border-b border-gray-700/50">
                <button id="res-hub" class="secondary-btn px-6 py-3 rounded-xl font-medium">📚 Open Knowledge Hub</button>
                <button id="res-retake" class="secondary-btn px-6 py-3 rounded-xl font-medium">🔄 Retake Set</button>
                ${wrongQs.length > 0 ? `<button id="res-wrong" class="primary-btn px-6 py-3 rounded-xl font-bold">🧠 Practice Wrong (${wrongQs.length})</button>` : ''}
                <button id="res-setup" class="primary-btn px-6 py-3 rounded-xl font-bold">⚙️ New Configuration</button>
            </div>

            <h3 class="text-2xl font-bold mb-6">Detailed Question Breakdown</h3>
            <div class="space-y-6">
                ${answeredQs.map((q, i) => {
                    const a = quizAnswers[q.id];
                    const bgBorder = a.timedOut
                        ? 'border-gray-700/50 glass-panel'
                        : a.isCorrect
                            ? 'border-success-500/30 glass-card'
                            : 'border-danger-500/30 glass-card';

                    return `
                    <div class="rounded-2xl border ${bgBorder} p-6">
                        <div class="flex items-start justify-between mb-4">
                            <h4 class="font-semibold text-lg leading-snug flex-1 pr-4">
                                <span class="text-muted font-mono text-sm mr-2">Q${i + 1}.</span>${window.escapeHTML(q.question)}
                            </h4>
                            <span class="text-xs font-mono glass-panel px-2.5 py-1 rounded">⏱️ ${(a.timeSpentMs / 1000).toFixed(1)}s</span>
                        </div>

                        ${!a.timedOut ? `
                        <div class="flex flex-wrap gap-4 mb-4">
                            <div class="text-sm glass-panel px-3 py-1.5 rounded-lg">
                                <span class="text-muted">Your Answer:</span>
                                <span class="font-bold ml-1 ${a.isCorrect ? 'text-success-400' : 'text-danger-400'}">${a.selected}</span>
                            </div>
                            ${!a.isCorrect ? `
                            <div class="text-sm bg-success-500/10 px-3 py-1.5 rounded-lg border border-success-500/20">
                                <span class="text-muted">Correct Answer:</span>
                                <span class="font-bold ml-1 text-success-400">${q.correct_answer}</span>
                            </div>` : ''}
                        </div>

                        ${q.explanation ? `
                        <div class="glass-panel rounded-xl p-4 border border-gray-700/50 mb-3">
                            <p class="text-xs font-bold text-brand-300 uppercase mb-2">📄 CSV Explanation</p>
                            <div class="prose prose-sm max-w-none">${window.renderMarkdownAndMath(q.explanation)}</div>
                        </div>` : ''}

                        ${llmEnabled && q.llm_explanation ? `
                        <div class="bg-brand-600/10 rounded-xl p-4 border border-brand-500/30">
                            <p class="text-xs font-bold text-brand-400 uppercase mb-2">🤖 LM Studio Detailed Breakdown</p>
                            <div class="prose prose-sm max-w-none">${window.renderMarkdownAndMath(q.llm_explanation)}</div>
                        </div>` : ''}
                        ` : '<p class="text-sm text-muted italic">Skipped due to timeout.</p>'}
                    </div>`;
                }).join('')}
            </div>
        </div>
    `;

    document.getElementById('res-hub')?.addEventListener('click', () => {
        document.querySelector('.nav-btn[data-target="knowledge-hub-view"]')?.click();
    });
    document.getElementById('res-setup')?.addEventListener('click', () => {
        currentQuiz = null;
        initQuizSetup();
    });
    document.getElementById('res-retake')?.addEventListener('click', () => {
        currentQuiz.questions = answeredQs;
        beginQuizRunner();
    });
    document.getElementById('res-wrong')?.addEventListener('click', () => {
        quizConfig.mode = 'learn';
        currentQuiz.questions = wrongQs;
        beginQuizRunner();
    });

    if (window.closeInspector) window.closeInspector();
}

function toggleRightSidebar() {
    const sidebar = document.getElementById('right-sidebar');
    if (!sidebar) return;
    if (sidebar.classList.contains('collapsed')) {
        window.openInspector('Clinical Inspector', document.getElementById('inspector-content')?.innerHTML || '');
    } else {
        window.closeInspector();
    }
}

function renderReferenceBadgesHTML(refs) {
    if (!refs || typeof refs !== 'object') return '';
    let itemsHtml = '';

    (refs.pdf || []).forEach(pdf => {
        itemsHtml += `
            <a href="#" class="citation-pdf w-full p-3 rounded-xl flex items-center justify-between gap-2 mb-2 text-left no-underline"
               data-filepath="${window.escapeHTML(pdf.file_path || '')}"
               data-page="${pdf.page || 1}">
                <div class="min-w-0 flex-1">
                    <p class="font-semibold text-xs truncate">📕 ${window.escapeHTML(pdf.title || 'PDF Textbook')}</p>
                    <p class="text-[10px] opacity-80 truncate">${window.escapeHTML(pdf.heading || '')} • Page ${pdf.page || 1}</p>
                </div>
                <span class="text-[10px] font-mono px-2 py-0.5 rounded glass-panel shrink-0">SumatraPDF ↗</span>
            </a>
        `;
    });

    (refs.obsidian || []).forEach(obs => {
        itemsHtml += `
            <a href="#" class="citation-obsidian w-full p-3 rounded-xl flex items-center justify-between gap-2 mb-2 text-left no-underline"
               data-filepath="${window.escapeHTML(obs.file_path || '')}"
               data-file="${window.escapeHTML(obs.title || '')}">
                <div class="min-w-0 flex-1">
                    <p class="font-semibold text-xs truncate">🟣 ${window.escapeHTML(obs.title || 'Obsidian Note')}</p>
                    <p class="text-[10px] opacity-80 truncate">${window.escapeHTML(obs.heading || 'Vault Note')}</p>
                </div>
                <span class="text-[10px] font-mono px-2 py-0.5 rounded glass-panel shrink-0">View Note</span>
            </a>
        `;
    });

    (refs.zim || []).forEach(zim => {
        const artTitle = zim.article_id || zim.title || '';
        itemsHtml += `
            <a href="#" class="citation-zim w-full p-3 rounded-xl flex items-center justify-between gap-2 mb-2 text-left no-underline"
               data-article-id="${window.escapeHTML(artTitle)}">
                <div class="min-w-0 flex-1">
                    <p class="font-semibold text-xs truncate">🌐 ${window.escapeHTML(zim.title || 'ZIM Article')}</p>
                    <p class="text-[10px] opacity-80 truncate">Offline ZIM Encyclopedia</p>
                </div>
                <span class="text-[10px] font-mono px-2 py-0.5 rounded glass-panel shrink-0">Read HTML</span>
            </a>
        `;
    });

    if (!itemsHtml) return '';
    return `
        <div class="mb-5">
            <h4 class="font-bold text-xs uppercase tracking-wider text-muted mb-2.5">🔗 Verified Local References</h4>
            <div class="flex flex-col">${itemsHtml}</div>
        </div>
    `;
}

async function updateInspectorForQuestion(questionObj) {
    if (!questionObj) return;

    // Collect topics specific ONLY to this active MCQ
    const topicsSet = new Set();

    if (Array.isArray(questionObj.topic_details) && questionObj.topic_details.length > 0) {
        questionObj.topic_details.forEach(td => {
            if (td?.name && td.name !== 'General Medical Concept') {
                topicsSet.add(td.name);
            }
        });
    } else if (Array.isArray(questionObj.topics) && questionObj.topics.length > 0) {
        questionObj.topics.forEach(t => {
            const name = typeof t === 'string' ? t : t?.name;
            if (name && name !== 'General Medical Concept') {
                topicsSet.add(name);
            }
        });
    }

    // Extract topics on the fly for this question if not already extracted or if only 1 topic
    if (topicsSet.size <= 1) {
        try {
            const extRes = await fetch('/api/queue/extract_topics', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    questions: [{
                        id: String(questionObj.id || ''),
                        question: questionObj.question || questionObj.question_text || '',
                        topic_name: questionObj.csv_topic_name || questionObj.topic_name || questionObj.topic || 'General',
                        option_a: questionObj.option_a || questionObj.opt_a || '',
                        option_b: questionObj.option_b || questionObj.opt_b || '',
                        option_c: questionObj.option_c || questionObj.opt_c || '',
                        option_d: questionObj.option_d || questionObj.opt_d || '',
                        correct_answer: questionObj.correct_answer || 'A',
                        explanation: questionObj.explanation || ''
                    }]
                })
            });
            if (extRes.ok) {
                const extData = await extRes.json();
                if (Array.isArray(extData.questions) && extData.questions[0]) {
                    const qRes = extData.questions[0];
                    questionObj.topics = qRes.topics || [];
                    questionObj.topic_details = qRes.topic_details || [];
                    (qRes.topics || []).forEach(t => {
                        if (t && t !== 'General Medical Concept') topicsSet.add(t);
                    });
                }
            }
        } catch (_) {}
    }

    const mainTopic = questionObj?.csv_topic_name || questionObj?.topic_name || questionObj?.topic;
    if (topicsSet.size === 0 && mainTopic && mainTopic !== 'General Medical Concept' && mainTopic.toLowerCase() !== 'general') {
        topicsSet.add(mainTopic);
    }

    if (topicsSet.size === 0) {
        const fallback = currentQuiz?.customTitle || 'General';
        topicsSet.add(fallback);
    }

    const topicsArray = Array.from(topicsSet);

    // Initial loading state in Right Inspector
    window.openInspector(
        `Q${currentIndex + 1} Topics & References`,
        `<div class="text-center py-10 text-muted text-sm animate-pulse">Loading topics for Q${currentIndex + 1}...</div>`,
        false
    );

    // Fetch reference & note availability statuses for collected topics
    let statuses = {};
    try {
        const statusRes = await fetch('/api/mcq/topics/status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ topics: topicsArray })
        });
        if (statusRes.ok) {
            const data = await statusRes.json();
            statuses = data.topics || {};
        }
    } catch (e) {
        console.warn("Failed to fetch topic statuses for inspector:", e);
    }

    // Build sidebar topic list HTML
    let html = `<div class="flex flex-col text-xs space-y-4 pb-6">`;
    html += `<div class="glass-panel p-3 rounded-xl border border-gray-700/50 mb-1">
                <span class="text-[10px] text-muted uppercase tracking-wider font-bold">MCQ ${currentIndex + 1} Topic List</span>
                <p class="text-[11px] text-muted mt-1">Click any topic below to view clinical notes in center view &amp; references here.</p>
             </div>`;

    html += `<div class="space-y-2" id="inspector-topic-list">`;
    topicsArray.forEach((topic, idx) => {
        const st = statuses[topic] || { has_refs: false, has_note: false };
        const isPrimary = idx === 0;

        html += `
            <div class="quiz-topic-item glass-panel p-3 rounded-xl border ${isPrimary ? 'border-brand-500/60 bg-brand-500/10' : 'border-gray-700/50'} hover:border-brand-500 cursor-pointer transition-all flex items-center justify-between gap-2"
                 data-topic="${window.escapeHTML(topic)}">
                <div class="min-w-0 flex-1">
                    <p class="font-bold text-xs truncate text-neutral-100 flex items-center gap-1.5">
                        <span>${idx + 1}. ${window.escapeHTML(topic)}</span>
                        ${isPrimary ? '<span class="text-[9px] px-1.5 py-0.2 bg-brand-500/30 text-brand-300 rounded uppercase">Main</span>' : ''}
                    </p>
                </div>
                <div class="flex items-center gap-1 shrink-0">
                    <span class="px-1.5 py-0.5 rounded text-[9px] font-mono ${st.has_refs ? 'bg-brand-600/20 text-brand-300 border border-brand-500/30' : 'opacity-40 glass-panel'}" title="${st.has_refs ? 'Reference links available' : 'No references'}">
                        🔗 ${st.has_refs ? 'Refs' : 'No Refs'}
                    </span>
                    <span class="px-1.5 py-0.5 rounded text-[9px] font-mono ${st.has_note ? 'bg-success-500/20 text-success-300 border border-success-500/30' : 'opacity-40 glass-panel'}" title="${st.has_note ? 'LLM note available' : 'No note'}">
                        🤖 ${st.has_note ? 'Note' : 'No Note'}
                    </span>
                </div>
            </div>
        `;
    });
    html += `</div>`;

    // Container for selected topic's reference links inside right inspector
    html += `<div id="inspector-topic-details" class="pt-3 border-t border-gray-700/50 space-y-3"></div>`;
    html += `</div>`;

    window.openInspector(`Q${currentIndex + 1} Topics`, html, false);

    // Bind click handlers to topic items
    const inspectorContent = document.getElementById('inspector-content');
    if (inspectorContent) {
        const topicItems = inspectorContent.querySelectorAll('.quiz-topic-item');
        topicItems.forEach(item => {
            item.addEventListener('click', () => {
                const topic = item.dataset.topic;
                if (!topic) return;

                // Highlight active topic item in inspector
                topicItems.forEach(el => el.classList.remove('border-brand-500', 'bg-brand-500/20'));
                item.classList.add('border-brand-500', 'bg-brand-500/20');

                // Open Topic Carousel Subview in center stage for this question's topics
                if (typeof window.openTopicCarouselForQuiz === 'function') {
                    window.openTopicCarouselForQuiz(topicsArray, topic);
                } else if (typeof loadTopicNotesToCenterView === 'function') {
                    loadTopicNotesToCenterView(topic);
                }

                // Load reference links for this topic into lower inspector section
                renderTopicSubDetailsInInspector(topic);
            });
        });

        // Automatically load details for the primary (first) topic
        if (topicsArray.length > 0) {
            renderTopicSubDetailsInInspector(topicsArray[0]);
        }
    }
}

async function loadTopicNotesToCenterView(topic) {
    const feedbackContainer = document.getElementById('feedback-container');
    if (!feedbackContainer) return;

    feedbackContainer.innerHTML = `
        <div class="animate-fade-in glass-panel border border-brand-500/40 rounded-2xl p-5 text-center py-8 text-muted text-sm">
            <p class="animate-pulse">Loading topic notes for <strong>${window.escapeHTML(topic)}</strong>...</p>
        </div>
    `;

    try {
        const res = await fetch(`/api/mcq/synthesis?topic=${encodeURIComponent(topic)}`);
        if (!res.ok) throw new Error("No synthesis available");
        const data = await res.json();

        const llmEnabled = window.AppState ? window.AppState.get().llmEnabled !== false : true;
        let fbHtml = `<div class="animate-fade-in space-y-4">`;

        fbHtml += `
            <div class="glass-panel border border-brand-500/40 rounded-2xl p-4 flex justify-between items-center">
                <div>
                    <span class="text-[10px] text-muted uppercase font-bold tracking-wider">Active Topic Note (Center View)</span>
                    <h3 class="text-base font-bold text-brand-300 mt-0.5">${window.escapeHTML(topic)}</h3>
                </div>
                <span class="text-xs px-2.5 py-1 rounded-full bg-brand-500/20 text-brand-300 border border-brand-500/30 font-medium">
                    📖 Center Inspector
                </span>
            </div>
        `;

        if (llmEnabled && data.unified_article) {
            try {
                const art = JSON.parse(data.unified_article);
                if (art && Array.isArray(art.clinical_points) && art.clinical_points.length > 0) {
                    fbHtml += `
                        <div class="glass-card p-5 rounded-2xl border border-brand-500/30">
                            <h4 class="font-bold text-brand-400 text-xs uppercase tracking-wider mb-3">🤖 High-Yield Synthesis: ${window.escapeHTML(art.subtopic || topic)}</h4>
                            ${art.conditional_context ? `<p class="text-xs text-muted mb-3 leading-relaxed"><strong>Context:</strong> ${window.escapeHTML(art.conditional_context)}</p>` : ''}
                            <ul class="list-disc pl-5 space-y-2 text-xs leading-relaxed">
                                ${art.clinical_points.map(pt => `
                                    <li>
                                        ${window.escapeHTML(pt.point)}${pt.source ? `<span class="text-[10px] text-brand-400 font-mono ml-1">[${window.escapeHTML(pt.source)}${pt.page ? ` p.${pt.page}` : ''}]</span>` : ''}
                                    </li>
                                `).join('')}
                            </ul>
                            ${art.discrepancies ? `
                                <div class="mt-4 p-3 rounded-xl bg-danger-500/15 border border-danger-500/30 text-danger-300 text-xs">
                                    <strong>⚠️ Clinical Variance:</strong><br/>${window.escapeHTML(art.discrepancies)}
                                </div>
                            ` : ''}
                        </div>
                    `;
                } else {
                    fbHtml += `
                        <div class="glass-card p-5 rounded-2xl border border-brand-500/30">
                            <h4 class="font-bold text-brand-400 text-xs uppercase tracking-wider mb-3">🤖 High-Yield Synopsis</h4>
                            <div class="prose prose-sm max-w-none text-xs leading-relaxed">${window.renderMarkdownAndMath(data.unified_article)}</div>
                        </div>
                    `;
                }
            } catch (_) {
                fbHtml += `
                    <div class="glass-card p-5 rounded-2xl border border-brand-500/30">
                        <h4 class="font-bold text-brand-400 text-xs uppercase tracking-wider mb-3">🤖 High-Yield Synopsis</h4>
                        <div class="prose prose-sm max-w-none text-xs leading-relaxed">${window.renderMarkdownAndMath(data.unified_article)}</div>
                    </div>
                `;
            }
        } else if (data.enhanced_explanation) {
            fbHtml += `
                <div class="glass-panel border border-gray-700/50 rounded-2xl p-5">
                    <h4 class="font-bold text-brand-300 text-xs uppercase tracking-wider mb-2">📄 Baseline Clinical Note</h4>
                    <div class="prose prose-sm max-w-none text-xs leading-relaxed">${window.renderMarkdownAndMath(data.enhanced_explanation)}</div>
                </div>
            `;
        } else {
            fbHtml += `
                <div class="glass-panel border border-gray-700/50 rounded-2xl p-5 text-sm text-muted">
                    No synthesized LLM notes found for "${window.escapeHTML(topic)}". See references in right sidebar.
                </div>
            `;
        }

        fbHtml += `</div>`;
        feedbackContainer.innerHTML = fbHtml;

    } catch (e) {
        feedbackContainer.innerHTML = `
            <div class="animate-fade-in glass-panel border border-gray-700/50 rounded-2xl p-5 text-center text-muted text-xs">
                <p class="font-semibold mb-1">No Notes Cached</p>
                <p>No synthesized notes exist for <strong>${window.escapeHTML(topic)}</strong> yet.</p>
            </div>
        `;
    }
}

async function renderTopicSubDetailsInInspector(topic) {
    const detailsContainer = document.getElementById('inspector-topic-details');
    if (!detailsContainer) return;

    detailsContainer.innerHTML = `<div class="text-center py-4 text-muted text-xs animate-pulse">Loading references for ${window.escapeHTML(topic)}...</div>`;

    try {
        const res = await fetch(`/api/mcq/synthesis?topic=${encodeURIComponent(topic)}`);
        if (!res.ok) throw new Error("No synthesis");
        const data = await res.json();

        let subHtml = `<div class="space-y-3">`;
        subHtml += `<span class="text-[10px] text-brand-400 uppercase font-bold tracking-wider">References for: ${window.escapeHTML(topic)}</span>`;

        const refsHtml = renderReferenceBadgesHTML(data.references);
        if (refsHtml) {
            subHtml += refsHtml;
        } else {
            subHtml += `<p class="text-muted italic text-[11px]">No local PDF, Obsidian, or ZIM references matched "${window.escapeHTML(topic)}".</p>`;
        }
        subHtml += `</div>`;

        detailsContainer.innerHTML = subHtml;
    } catch (_) {
        detailsContainer.innerHTML = `<p class="text-muted italic text-[11px] text-center py-2">No references indexed for ${window.escapeHTML(topic)}.</p>`;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const navBtns = document.querySelectorAll('.nav-btn');
    navBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            if (e.currentTarget.getAttribute('data-target') === 'quiz-view') {
                if (!currentQuiz) initQuizSetup();
            }
        });
    });
});
