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


// --- Initialization & Setup UI ---

async function initQuizSetup() {
    const container = document.getElementById('quiz-view');
    if (!container) return;

    // Fetch filters
    let subjects = [], topics = [];
    try {
        const [subRes, topRes] = await Promise.all([ fetch('/api/mcq/subjects'), fetch('/api/mcq/topics') ]);
        if (subRes.ok) subjects = await subRes.json();
        if (topRes.ok) topics = await topRes.json();
    } catch (e) { console.error("Failed to load filters", e); }

    container.innerHTML = `
        <div class="w-full p-6 animate-fade-in">
            <div class="flex items-center justify-between mb-8">
                <div>
                    <h1 class="text-3xl font-bold mb-2 flex items-center gap-3">⚙️ Quiz Configuration</h1>
                    <p class="subtitle">Customize your learning session and filter knowledge bases.</p>
                </div>
                <button id="toggle-inspector-btn" class="secondary-btn px-4 py-2 flex items-center gap-2">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg>
                    Toggle Inspector
                </button>
            </div>

            <div class="grid lg:grid-cols-2 gap-6 mb-8">
                <!-- Source Filters -->
                <div class="glass-card card-shade-1 p-6">
                    <h3 class="text-lg font-semibold mb-4 border-b border-white/10 pb-2 flex items-center gap-2">📂 1. Source Filters</h3>

                    <label class="block text-xs text-muted mb-1">Subject</label>
                    <select id="cfg-subject" class="w-full glass-select rounded-lg px-3 py-2.5 text-sm mb-4">
                        <option value="all">All Subjects</option>
                        ${subjects.map(s => `<option value="${window.escapeHTML(s.subject)}">${window.escapeHTML(s.subject)} (${s.count})</option>`).join('')}
                    </select>

                    <label class="block text-xs text-muted mb-1">Topic</label>
                    <select id="cfg-topic" class="w-full glass-select rounded-lg px-3 py-2.5 text-sm mb-4">
                        <option value="all">All Topics</option>
                        ${topics.map(t => `<option value="${window.escapeHTML(t.topic)}">${window.escapeHTML(t.topic)} (${t.count})</option>`).join('')}
                    </select>

                    <label class="block text-xs text-muted mb-1">Keyword Search</label>
                    <input type="text" id="cfg-keyword" placeholder="e.g., dyspnea, insulin..." class="w-full glass-input rounded-lg px-3 py-2.5 text-sm">
                </div>

                <!-- Session Rules -->
                <div class="glass-card card-shade-2 p-6">
                    <h3 class="text-lg font-semibold mb-4 border-b border-white/10 pb-2 flex items-center gap-2">🕹️ 2. Session Rules</h3>

                    <label class="block text-xs text-muted mb-1">Study Mode</label>
                    <div class="flex gap-2 mb-4">
                        <button id="mode-test" class="flex-1 py-2 rounded-lg text-sm font-medium border border-white/10 transition-colors ${quizConfig.mode === 'test' ? 'primary-btn' : 'secondary-btn'}">📝 Test</button>
                        <button id="mode-learn" class="flex-1 py-2 rounded-lg text-sm font-medium border border-white/10 transition-colors ${quizConfig.mode === 'learn' ? 'primary-btn' : 'secondary-btn'}">📖 Learn</button>
                        <button id="mode-target" class="flex-1 py-2 rounded-lg text-sm font-medium border border-white/10 transition-colors ${quizConfig.mode === 'target' ? 'primary-btn' : 'secondary-btn'}">🎯 Target</button>
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
                        <span class="text-sm opacity-80 group-hover:opacity-100 transition-opacity">🧠 Enable Spaced Repetition (FSRS)</span>
                    </label>
                </div>
            </div>

            <div class="glass-card card-shade-3 p-6 text-center">
                <h2 class="text-3xl font-black text-brand-400 mb-2" id="setup-match-count">...</h2>
                <p class="text-muted text-sm mb-6">Matching Questions Available</p>
                <button id="btn-start-quiz" class="primary-btn px-10 py-4 font-bold rounded-xl transition-all hover:scale-105 disabled:opacity-50 disabled:hover:scale-100 disabled:shadow-none w-full md:w-auto text-lg">
                    🚀 Launch Session
                </button>
            </div>
        </div>
    `;

    // Bind Setup Events
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
            document.getElementById('setup-match-count').textContent = data.count;
            document.getElementById('btn-start-quiz').disabled = data.count === 0;

            // Adjust slider max based on availability (unless Target mode)
            const countSlider = document.getElementById('cfg-count');
            if (quizConfig.mode !== 'target') {
                countSlider.max = Math.max(data.count, 1);
                if (parseInt(countSlider.value) > data.count) {
                    countSlider.value = data.count;
                    quizConfig.count = data.count;
                    document.getElementById('cfg-count-val').textContent = data.count;
                }
            } else {
                countSlider.max = 100;
            }
        } catch (e) { console.error(e); }
    };

    const bindInput = (id, key, isInt=false, callback=null) => {
        const el = document.getElementById(id);
        if(!el) return;
        el.addEventListener(el.tagName === 'INPUT' && el.type === 'range' ? 'input' : 'change', (e) => {
            quizConfig[key] = isInt ? parseInt(e.target.value) : e.target.value;
            if (callback) callback(e);
        });
    };

    bindInput('cfg-subject', 'subject', false, updateMatchCount);
    bindInput('cfg-topic', 'topic', false, updateMatchCount);

    let debounce;
    document.getElementById('cfg-keyword').addEventListener('input', (e) => {
        quizConfig.keyword = e.target.value;
        clearTimeout(debounce);
        debounce = setTimeout(updateMatchCount, 300);
    });

    bindInput('cfg-count', 'count', true, (e) => document.getElementById('cfg-count-val').textContent = e.target.value);
    bindInput('cfg-time', 'timeLimit', true, (e) => document.getElementById('cfg-time-val').textContent = e.target.value === '0' ? 'Unlimited' : e.target.value);

    document.getElementById('cfg-sr').addEventListener('change', (e) => quizConfig.spacedRepetition = e.target.checked);

    ['test', 'learn', 'target'].forEach(m => {
        document.getElementById(`mode-${m}`).addEventListener('click', () => {
            quizConfig.mode = m;
            initQuizSetup(); // Re-render to update classes
        });
    });

    document.getElementById('btn-start-quiz').addEventListener('click', startQuiz);
    document.getElementById('toggle-inspector-btn').addEventListener('click', toggleRightSidebar);

    updateMatchCount();
}

async function startQuiz() {
    const payload = {
        subjects: quizConfig.subject === 'all' ? null : [quizConfig.subject],
        topics: quizConfig.topic === 'all' ? null : [quizConfig.topic],
        keyword: quizConfig.keyword || null,
        count: quizConfig.mode === 'target' ? 100 : quizConfig.count
    };

    try {
        const res = await fetch('/api/mcq/generate_set', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.status === 'success' && data.questions.length > 0) {
            currentQuiz = {
                questions: data.questions,
                config: { ...quizConfig }
            };
            beginQuizRunner();
        } else {
            alert("No questions found for the selected criteria.");
        }
    } catch(e) {
        console.error(e);
        alert("Failed to start quiz.");
    }
}

// --- Active Quiz Engine ---

function beginQuizRunner() {
    currentIndex = 0;
    quizAnswers = {};
    scores = { correct: 0, wrong: 0 };
    isPaused = false;
    times.start = Date.now();

    if (quizConfig.timeLimit > 0) {
        let timeLeft = quizConfig.timeLimit * 60;
        timers.overall = setInterval(() => {
            if (isPaused) return;
            timeLeft--;
            if (timeLeft <= 0) finishQuiz();
            else {
                const el = document.getElementById('active-timer');
                if (el) el.textContent = `${Math.floor(timeLeft/60).toString().padStart(2,'0')}:${(timeLeft%60).toString().padStart(2,'0')}`;
            }
        }, 1000);
    }

    renderQuestion();
}

function renderQuestion() {
    const container = document.getElementById('quiz-view');
    const q = currentQuiz.questions[currentIndex];
    const isAnswered = !!quizAnswers[q.id];

    if (!isAnswered) {
        times.questionStart = Date.now();
        resetQuestionTimeout();
        // Auto-open and update right inspector for learning context
        updateInspector(q.topic_name);
    }

    const isTarget = quizConfig.mode === 'target';
    const isLearn = quizConfig.mode === 'learn';

    let headerText = isTarget
        ? `Target: ${scores.correct} / ${quizConfig.count} Correct`
        : `Question ${currentIndex + 1} of ${currentQuiz.questions.length}`;

    const progressPct = isTarget
        ? (scores.correct / quizConfig.count) * 100
        : (Object.keys(quizAnswers).length / currentQuiz.questions.length) * 100;

    let html = `
        <div class="w-full p-6 animate-fade-in flex flex-col h-full">
            <div class="flex items-center justify-between mb-6 flex-wrap gap-4">
                <div class="flex items-center gap-4">
                    <div class="glass-panel border border-white/10 rounded-xl px-5 py-2.5 shadow-inner">
                        <span class="text-brand-400 font-black text-lg tracking-wide">${headerText}</span>
                    </div>
                    <div class="flex items-center gap-4 text-sm font-bold glass-panel px-4 py-2.5 rounded-xl border border-white/5">
                        <span class="text-success-500 flex items-center gap-1.5"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> ${scores.correct}</span>
                        <span class="opacity-30">|</span>
                        <span class="text-danger-500 flex items-center gap-1.5"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg> ${scores.wrong}</span>
                    </div>
                </div>
                <div class="flex items-center gap-3">
                    <button id="toggle-inspector-active" class="icon-btn glass-panel border border-white/5" title="Toggle Inspector">
                        <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="9" y1="3" x2="9" y2="21"></line></svg>
                    </button>
                    ${quizConfig.timeLimit > 0 ? `<div id="active-timer" class="px-4 py-2 glass-panel border border-gray-700 rounded-xl font-mono text-lg font-bold text-brand-400 shadow-inner">--:--</div>` : ''}
                </div>
            </div>

            <div class="w-full glass-panel rounded-full h-2 mb-8 shadow-inner border border-white/5 overflow-hidden">
                <div class="h-2 rounded-full transition-all duration-500 ${isTarget ? 'bg-gradient-to-r from-purple-500 to-success-500' : 'bg-gradient-to-r from-brand-500 to-purple-500'}" style="width: ${Math.min(progressPct, 100)}%"></div>
            </div>

            <div class="glass-card card-shade-1 p-8 mb-6 relative overflow-hidden flex-shrink-0">
                <div class="absolute top-0 left-0 w-1 h-full bg-brand-500"></div>
                <div class="mb-5 flex items-center gap-2">
                    <span class="px-3 py-1 bg-brand-500/20 text-brand-300 rounded-full text-[11px] font-bold uppercase tracking-wider border border-brand-500/30">
                        ${window.escapeHTML(q.topic_name)}
                    </span>
                    <span class="px-3 py-1 glass-panel text-muted rounded-full text-[11px] font-bold uppercase tracking-wider border border-white/10">
                        Mode: ${quizConfig.mode}
                    </span>
                </div>
                <h2 class="text-xl font-semibold leading-relaxed mb-8 opacity-90">${window.escapeHTML(q.question)}</h2>

                <div class="space-y-3" id="options-container"></div>
            </div>

            <div id="feedback-container" class="flex-1 overflow-y-auto pr-2 mb-4"></div>

            <div class="flex justify-between items-center mt-auto pt-4 border-t border-white/10">
                <button id="btn-prev" class="secondary-btn px-6 py-3 rounded-xl disabled:opacity-30 font-medium" ${currentIndex === 0 ? 'disabled' : ''}>
                    ← Previous
                </button>
                <button id="btn-next" class="primary-btn px-8 py-3 rounded-xl transition-all shadow-lg font-bold disabled:opacity-30 disabled:shadow-none" ${!isAnswered && !isTarget ? 'disabled' : ''}>
                    ${currentIndex >= currentQuiz.questions.length - 1 && !isTarget ? 'Finish Quiz 🏁' : 'Next Question →'}
                </button>
            </div>
        </div>
    `;
    container.innerHTML = html;

    // Attach Options
    const optsDiv = document.getElementById('options-container');
    ['A', 'B', 'C', 'D'].forEach(opt => {
        const text = q[`option_${opt.toLowerCase()}`];
        if (!text) return;

        const btn = document.createElement('button');
        let classes = 'w-full text-left px-5 py-4 rounded-xl border-2 transition-all flex items-center justify-between font-medium shadow-sm ';

        if (!isAnswered) {
            classes += 'glass-card border-white/10 hover:border-brand-500 cursor-pointer';
            btn.onclick = () => handleSelectOption(q, opt);
        } else {
            classes += 'cursor-default ';
            const ans = quizAnswers[q.id];
            const isCorrectOpt = opt === q.correct_answer.toUpperCase();
            const isSelected = ans.selected === opt;

            if (ans.timedOut) classes += 'glass-panel border-white/5 opacity-50';
            else if (isCorrectOpt) classes += 'bg-success-500/20 border-success-500 text-success-300';
            else if (isSelected && !ans.isCorrect) classes += 'bg-danger-500/20 border-danger-500 text-danger-300';
            else classes += 'glass-panel border-white/5 opacity-50';
        }

        btn.className = classes;
        btn.innerHTML = `
            <div class="flex items-center gap-4">
                <span class="w-8 h-8 rounded-lg flex items-center justify-center text-sm font-bold glass-panel shadow-inner">
                    ${opt}
                </span>
                <span class="leading-snug">${window.escapeHTML(text)}</span>
            </div>
            ${isAnswered && !quizAnswers[q.id].timedOut && opt === q.correct_answer.toUpperCase() ? '<svg class="text-success-500" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg>' : ''}
            ${isAnswered && !quizAnswers[q.id].timedOut && quizAnswers[q.id].selected === opt && opt !== q.correct_answer.toUpperCase() ? '<svg class="text-danger-500" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>' : ''}
        `;
        optsDiv.appendChild(btn);
    });

    // Handle Feedback for Learn mode
    if (isAnswered && isLearn && !quizAnswers[q.id].timedOut) {
        const rawExplanation = q.explanation || 'No detailed explanation provided.';
        const cleanExp = window.DOMPurify && window.marked ? window.DOMPurify.sanitize(window.marked.parse(rawExplanation)) : window.escapeHTML(rawExplanation);

        document.getElementById('feedback-container').innerHTML = `
            <div class="animate-fade-in bg-brand-500/10 border border-brand-500/30 rounded-2xl p-6 shadow-inner">
                <h4 class="font-bold text-brand-300 mb-3 flex items-center gap-2"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg> Explanation</h4>
                <div class="prose prose-sm max-w-none">${cleanExp}</div>
            </div>
        `;
    }

    document.getElementById('btn-prev').onclick = () => { if(currentIndex > 0) { currentIndex--; renderQuestion(); } };
    const nextBtn = document.getElementById('btn-next');
    if (isTarget) {
        nextBtn.textContent = 'Skip';
        nextBtn.onclick = () => { handleSelectOption(q, null, true); };
        if (isAnswered) nextBtn.style.display = 'none';
    } else {
        nextBtn.onclick = () => {
            if (currentIndex < currentQuiz.questions.length - 1) { currentIndex++; renderQuestion(); }
            else finishQuiz();
        };
    }

    document.getElementById('toggle-inspector-active').addEventListener('click', toggleRightSidebar);
}

function handleSelectOption(q, option, isTimeout = false) {
    clearTimeout(timers.autoAdvance);
    clearInterval(timers.question);

    const isCorrect = !isTimeout && option === q.correct_answer.toUpperCase();
    const timeSpentMs = Date.now() - times.questionStart;

    quizAnswers[q.id] = { selected: option, isCorrect, timeSpentMs, timedOut: isTimeout };
    if (isCorrect) scores.correct++; else scores.wrong++;

    // FSRS Spaced Repetition Scoring
    if (quizConfig.spacedRepetition && !isTimeout) {
        const timeSec = timeSpentMs / 1000;
        let rating = 3; // Good
        if (!isCorrect) rating = 1; // Again
        else if (timeSec > 30) rating = 2; // Hard
        else if (timeSec < 8) rating = 4; // Easy

        fetch('/api/mcq/score', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ mcq_id: parseInt(q.id), rating })
        }).catch(console.error);
    }

    renderQuestion();

    if (quizConfig.mode === 'target' && scores.correct >= quizConfig.count) {
        timers.autoAdvance = setTimeout(finishQuiz, 1500);
        return;
    }

    if (quizConfig.mode !== 'learn') {
        timers.autoAdvance = setTimeout(() => {
            if (currentIndex < currentQuiz.questions.length - 1) { currentIndex++; renderQuestion(); }
            else finishQuiz();
        }, 1200);
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
    container.innerHTML = `
        <div class="max-w-2xl mx-auto p-6 flex items-center h-full">
            <div class="glass-card card-shade-3 border-warning-500/30 w-full text-center p-10 animate-fade-in shadow-[0_0_40px_rgba(245,158,11,0.1)]">
                <div class="w-20 h-20 bg-warning-500/20 rounded-full flex items-center justify-center mx-auto mb-6 border border-warning-500/40">
                    <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-warning-500"><path d="M17 8h1a4 4 0 1 1 0 8h-1"></path><path d="M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4Z"></path><line x1="6" y1="2" x2="6" y2="4"></line><line x1="10" y1="2" x2="10" y2="4"></line><line x1="14" y1="2" x2="14" y2="4"></line></svg>
                </div>
                <h2 class="text-3xl font-black mb-4">Need a Break?</h2>
                <p class="text-muted mb-8 text-lg">No answer given for ${quizConfig.timeout} minutes. Your progress is saved safely.</p>
                <div class="flex flex-col gap-3 max-w-sm mx-auto">
                    <button id="break-resume" class="primary-btn w-full py-4 font-bold rounded-xl shadow-lg transition-all">▶️ Resume Session</button>
                    <button id="break-skip" class="secondary-btn w-full py-3 rounded-xl transition-all">⏭️ Skip Question</button>
                    <button id="break-end" class="w-full py-2 text-muted hover:text-brand-400 transition-colors mt-2 bg-transparent border-none cursor-pointer">End Session & View Results</button>
                </div>
            </div>
        </div>
    `;

    document.getElementById('break-resume').onclick = () => { isPaused = false; times.questionStart = Date.now(); renderQuestion(); };
    document.getElementById('break-skip').onclick = () => { isPaused = false; handleSelectOption(currentQuiz.questions[currentIndex], null, true); };
    document.getElementById('break-end').onclick = finishQuiz;
}

// --- Results & Inspector ---

async function finishQuiz() {
    clearInterval(timers.overall);
    clearInterval(timers.question);
    clearTimeout(timers.autoAdvance);

    const answeredQuestions = [];
    const answersList = [];

    currentQuiz.questions.forEach(q => {
        const ans = quizAnswers[q.id];
        if (ans) {
            answeredQuestions.push(q);
            answersList.push({ questionId: q.id.toString(), selectedOption: ans.selected, isCorrect: ans.isCorrect, timeSpentSeconds: ans.timeSpentMs });
        }
    });

    const timeUsed = Math.floor((Date.now() - times.start) / 1000);
    const accuracy = answeredQuestions.length > 0 ? (scores.correct / answeredQuestions.length) * 100 : 0;

    const result = {
        id: crypto.randomUUID(),
        subjectName: quizConfig.subject === 'all' ? 'Mixed Topics' : quizConfig.subject,
        timestamp: Date.now(),
        totalQuestions: answeredQuestions.length,
        correctAnswers: scores.correct,
        wrongAnswers: scores.wrong,
        accuracy: accuracy,
        timeUsedSeconds: timeUsed,
        mode: quizConfig.mode,
        answers: answersList
    };

    try {
        await fetch('/api/sessions/save', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ ...result, questions: answeredQuestions })
        });
    } catch(e) { console.error("Failed to save session", e); }

    // Update global state optimistically to power dashboard metrics
    if (window.AppState) {
        const state = window.AppState.get();
        state.testResults.push(result);
        const today = new Date().toISOString().split('T')[0];
        let habit = state.habitLog.find(h => h.date === today);
        if(!habit) { habit = { date: today, questionsAnswered:0, correctAnswers:0, timeSpentSeconds:0 }; state.habitLog.push(habit); }
        habit.questionsAnswered += answeredQuestions.length;
        habit.correctAnswers += scores.correct;
        habit.timeSpentSeconds += timeUsed;
        window.AppState.update({ testResults: state.testResults, habitLog: state.habitLog, totalQuestionsEver: (state.totalQuestionsEver||0) + answeredQuestions.length });
    }

    renderResults(result, answeredQuestions);
}

function renderResults(result, answeredQs) {
    const container = document.getElementById('quiz-view');
    const wrongQs = answeredQs.filter(q => { const a = quizAnswers[q.id]; return a && !a.isCorrect && !a.timedOut; });

    container.innerHTML = `
        <div class="w-full p-6 animate-fade-in">
            <div class="text-center mb-10">
                <div class="inline-flex items-center justify-center w-24 h-24 rounded-full ${result.accuracy >= 70 ? 'bg-success-500/20 text-success-400' : 'bg-warning-500/20 text-warning-400'} mb-4 shadow-inner border border-white/5">
                    <span class="text-4xl font-black">${result.accuracy >= 90 ? 'A+' : result.accuracy >= 80 ? 'A' : result.accuracy >= 70 ? 'B' : result.accuracy >= 60 ? 'C' : 'D'}</span>
                </div>
                <h1 class="text-4xl font-black mb-2">${result.accuracy >= 70 ? '🎉 Excellent Work!' : '💪 Keep Practicing!'}</h1>
                <p class="subtitle text-lg">Session completed for <span class="text-brand-400 font-bold">${window.escapeHTML(result.subjectName)}</span></p>
            </div>

            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-10">
                <div class="glass-card card-shade-1 text-center p-6 border-brand-500/30 shadow-[0_0_15px_rgba(139,92,246,0.15)]">
                    <p class="text-3xl font-black text-brand-400 mb-1">${result.accuracy.toFixed(1)}%</p>
                    <p class="text-xs text-muted uppercase tracking-widest">Accuracy</p>
                </div>
                <div class="glass-card card-shade-2 text-center p-6 border-success-500/30">
                    <p class="text-3xl font-black text-success-500 mb-1">${result.correctAnswers}</p>
                    <p class="text-xs text-muted uppercase tracking-widest">Correct</p>
                </div>
                <div class="glass-card card-shade-3 text-center p-6 border-danger-500/30">
                    <p class="text-3xl font-black text-danger-500 mb-1">${result.wrongAnswers}</p>
                    <p class="text-xs text-muted uppercase tracking-widest">Wrong</p>
                </div>
                <div class="glass-card card-shade-4 text-center p-6 border-white/10">
                    <p class="text-3xl font-black mb-1">${Math.floor(result.timeUsedSeconds/60)}m ${result.timeUsedSeconds%60}s</p>
                    <p class="text-xs text-muted uppercase tracking-widest">Time Taken</p>
                </div>
            </div>

            <div class="flex flex-wrap gap-4 justify-center mb-10 pb-10 border-b border-white/10">
                <button id="res-home" class="secondary-btn px-8 py-3 rounded-xl font-medium">🏠 Dashboard</button>
                <button id="res-retake" class="secondary-btn px-8 py-3 rounded-xl font-medium">🔄 Retake Set</button>
                ${wrongQs.length > 0 ? `<button id="res-wrong" class="primary-btn px-8 py-3 shadow-lg rounded-xl font-bold">🧠 Practice Wrong (${wrongQs.length})</button>` : ''}
                <button id="res-setup" class="primary-btn px-8 py-3 shadow-lg rounded-xl font-bold">⚙️ New Config</button>
            </div>

            <h3 class="text-2xl font-bold mb-6 flex items-center gap-3"><svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="text-brand-400"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg> Detailed Breakdown</h3>
            <div class="space-y-6">
                ${answeredQs.map((q, i) => {
                    const a = quizAnswers[q.id];
                    const bgBorder = a.timedOut ? 'border-white/10 glass-panel' : a.isCorrect ? 'border-success-500/30 bg-success-900/10' : 'border-danger-500/30 bg-danger-900/10';
                    const icon = a.timedOut ? '⏭️' : a.isCorrect ? '<span class="text-success-500 font-bold">✓</span>' : '<span class="text-danger-500 font-bold">✗</span>';
                    const cleanExp = window.DOMPurify && window.marked ? window.DOMPurify.sanitize(window.marked.parse(q.explanation || 'No explanation.')) : window.escapeHTML(q.explanation);

                    return `
                    <div class="rounded-2xl border ${bgBorder} p-6">
                        <div class="flex items-start justify-between mb-4">
                            <h4 class="font-semibold text-lg leading-snug flex-1 pr-4"><span class="text-muted font-mono text-sm mr-2">Q${i+1}.</span>${window.escapeHTML(q.question)}</h4>
                            <div class="flex items-center gap-3 shrink-0">
                                <span class="text-xs font-mono glass-panel px-2 py-1 rounded border border-white/5 opacity-70">⏱️ ${(a.timeSpentMs/1000).toFixed(1)}s</span>
                                <div class="w-8 h-8 rounded-full glass-panel border border-white/10 flex items-center justify-center text-lg shadow-inner">${icon}</div>
                            </div>
                        </div>

                        ${!a.timedOut ? `
                        <div class="flex flex-wrap gap-4 mb-6">
                            <div class="flex items-center gap-2 text-sm glass-panel px-3 py-1.5 rounded-lg border border-white/5">
                                <span class="text-muted">Your Answer:</span>
                                <span class="font-bold ${a.isCorrect ? 'text-success-400' : 'text-danger-400'}">${a.selected}</span>
                            </div>
                            ${!a.isCorrect ? `
                            <div class="flex items-center gap-2 text-sm bg-success-500/10 px-3 py-1.5 rounded-lg border border-success-500/20">
                                <span class="text-muted">Correct Answer:</span>
                                <span class="font-bold text-success-400">${q.correct_answer}</span>
                            </div>` : ''}
                        </div>

                        <div class="glass-panel rounded-xl p-5 border border-white/5 shadow-inner">
                            <p class="text-xs font-bold text-brand-400 uppercase tracking-widest mb-3">Explanation</p>
                            <div class="prose prose-sm max-w-none opacity-80">${cleanExp}</div>
                        </div>
                        ` : '<p class="text-sm text-muted italic">Skipped due to timeout.</p>'}
                    </div>`;
                }).join('')}
            </div>
        </div>
    `;

    document.getElementById('res-home').onclick = () => document.querySelector('.nav-btn[data-target="dashboard-view"]')?.click();
    document.getElementById('res-setup').onclick = initQuizSetup;
    document.getElementById('res-retake').onclick = () => { currentQuiz.questions = answeredQs; beginQuizRunner(); };
    const wBtn = document.getElementById('res-wrong');
    if (wBtn) wBtn.onclick = () => { quizConfig.mode = 'learn'; currentQuiz.questions = wrongQs; beginQuizRunner(); };

    // Close Inspector when viewing results
    const sidebar = document.getElementById('right-sidebar');
    if (sidebar && !sidebar.classList.contains('collapsed')) sidebar.classList.add('collapsed');
}

// --- Right Inspector (RAG Synthesis) ---

function toggleRightSidebar() {
    const sidebar = document.getElementById('right-sidebar');
    if (sidebar) sidebar.classList.toggle('collapsed');
}

document.getElementById('close-inspector-btn')?.addEventListener('click', toggleRightSidebar);

// Make available globally for document clicks on citation badges
window.openLocalResource = async (type, path, extra) => {
    if (type === 'pdf') {
        fetch('/api/sources/open_pdf', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ file_path: path, page: parseInt(extra)||1 }) });
    } else if (type === 'obsidian') {
        window.location.href = `obsidian://open?path=${encodeURIComponent(path)}`;
    } else if (type === 'zim') {
        alert("ZIM article viewer coming soon to vanilla JS port! Query: " + path);
    }
};

function updateInspector(topic) {
    const sidebar = document.getElementById('right-sidebar');
    const content = document.getElementById('inspector-content');
    if (!sidebar || !content) return;

    // Auto open on new question if closed
    if (sidebar.classList.contains('collapsed')) sidebar.classList.remove('collapsed');

    content.innerHTML = `
        <div class="flex flex-col items-center justify-center py-16 text-brand-400 animate-pulse">
            <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mb-3 animate-spin"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
            <p class="text-sm font-medium">Extracting Local Context...</p>
            <p class="text-xs opacity-60 mt-1">${window.escapeHTML(topic)}</p>
        </div>
    `;

    fetch(`/api/mcq/synthesis?topic=${encodeURIComponent(topic)}`)
        .then(res => { if(!res.ok) throw new Error("Not processed"); return res.json(); })
        .then(data => {
            let html = `<div class="space-y-4 text-[13px] pb-6">`;

            if (data.enhanced_explanation) {
                html += `
                <div class="bg-brand-500/10 border border-brand-500/30 rounded-xl p-4 shadow-inner">
                    <h4 class="font-bold text-brand-300 mb-2 flex items-center gap-1.5"><svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg> High-Yield Synopsis</h4>
                    <div class="opacity-90 prose prose-sm leading-relaxed">${window.escapeHTML(data.enhanced_explanation)}</div>
                </div>`;
            }

            const varMatch = data.unified_article ? data.unified_article.match(/### Discrepancies(?:\s*&\s*Variances)?\n([\s\S]*)/i) : null;
            if (varMatch) {
                html += `
                <div class="bg-warning-500/10 border border-warning-500/30 rounded-xl p-4 shadow-inner">
                    <h4 class="font-bold text-warning-400 mb-2 flex items-center gap-1.5"><svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg> Clinical Variance</h4>
                    <div class="opacity-90 font-mono text-[11px] leading-relaxed text-warning-200">${window.escapeHTML(varMatch[1].trim())}</div>
                </div>`;
            }

            if (data.references && Object.keys(data.references).length > 0) {
                html += `<div class="mt-6"><h4 class="font-bold text-muted mb-3 text-sm px-1">Verified Reference Materials</h4><div class="space-y-2">`;

                if (data.references.pdf) {
                    data.references.pdf.forEach(pdf => {
                        html += `
                        <button onclick="window.openLocalResource('pdf', '${window.escapeHTML(pdf.file_path)}', ${pdf.page})" class="w-full text-left p-3 bg-red-500/10 hover:bg-red-500/20 border border-red-500/20 rounded-xl transition-all group flex items-center gap-3">
                            <div class="p-2 bg-red-500/20 rounded-lg text-red-400 group-hover:scale-110 transition-transform"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"></path><polyline points="14 2 14 8 20 8"></polyline></svg></div>
                            <div class="flex-1 overflow-hidden">
                                <p class="font-medium text-red-400 truncate text-sm">${window.escapeHTML(pdf.title)}</p>
                                <p class="text-[10px] text-muted mt-0.5">SumatraPDF • Page ${pdf.page || 1}</p>
                            </div>
                        </button>`;
                    });
                }

                if (data.references.obsidian) {
                    data.references.obsidian.forEach(obs => {
                        html += `
                        <button onclick="window.openLocalResource('obsidian', '${window.escapeHTML(obs.file_path)}') class="w-full text-left p-3 bg-purple-500/10 hover:bg-purple-500/20 border border-purple-500/20 rounded-xl transition-all group flex items-center gap-3">
                            <div class="p-2 bg-purple-500/20 rounded-lg text-purple-400 group-hover:scale-110 transition-transform"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"></path><polyline points="14 2 14 8 20 8"></polyline></svg></div>
                            <div class="flex-1 overflow-hidden">
                                <p class="font-medium text-purple-400 truncate text-sm">${window.escapeHTML(obs.title)}</p>
                                <p class="text-[10px] text-muted mt-0.5">Obsidian Vault Note</p>
                            </div>
                        </button>`;
                    });
                }
                html += `</div></div>`;
            }

            html += `</div>`;
            content.innerHTML = html;
        })
        .catch(err => {
            content.innerHTML = `
                <div class="text-center py-10 opacity-60 px-4">
                    <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mx-auto mb-3 opacity-50"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>
                    <p class="text-sm font-medium">Topic Not Synthesized</p>
                    <p class="text-xs mt-2 leading-relaxed">LM Studio has not processed <strong>${window.escapeHTML(topic)}</strong> yet. Check the Processing Queue tab to track offline background generation progress.</p>
                </div>
            `;
        });
}

// Bind to event lifecycle
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
