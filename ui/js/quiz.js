// ui/js/quiz.js

let currentQuiz = null;
let currentQuestionIndex = 0;
let quizAnswers = {};
let quizTimer = null;
let questionTimeoutTimer = null;
let autoAdvanceTimer = null;
let quizStartTime = null;
let questionStartTime = null;

let isPaused = false;
let correctCount = 0;
let wrongCount = 0;

function escapeHTML(str) {
    if (typeof str !== 'string') return '';
    return str.replace(/[&<>'"]/g, tag => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    }[tag] || tag));
}

async function loadSubjects() {
    try {
        const res = await fetch('/api/mcq/subjects');
        const subjects = await res.json();
        const sel = document.getElementById('quiz-subject-select');
        if(sel) {
            sel.innerHTML = '<option value="all">All Subjects</option>';
            subjects.forEach(s => {
                sel.innerHTML += `<option value="${escapeHTML(s.subject)}">${escapeHTML(s.subject)} (${s.count})</option>`;
            });
        }
    } catch(e) {
        console.error("Failed to load subjects", e);
    }
}

async function loadTopics() {
    try {
        const res = await fetch('/api/mcq/topics');
        const topics = await res.json();
        const sel = document.getElementById('quiz-topic-select');
        if(sel) {
            sel.innerHTML = '<option value="all">All Topics</option>';
            topics.forEach(t => {
                sel.innerHTML += `<option value="${escapeHTML(t.topic)}">${escapeHTML(t.topic)} (${t.count})</option>`;
            });
        }
    } catch(e) {
        console.error("Failed to load topics", e);
    }
}

let searchDebounceTimeout = null;

async function updateMatchCount() {
    const subject = document.getElementById('quiz-subject-select').value;
    const topic = document.getElementById('quiz-topic-select').value;
    const keyword = document.getElementById('quiz-keyword').value;

    const payload = {
        subjects: subject === 'all' ? null : [subject],
        topics: topic === 'all' ? null : [topic],
        keyword: keyword ? keyword : null,
        count: 0
    };

    try {
        const res = await fetch('/api/mcq/search_count', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        if(res.ok) {
            const data = await res.json();
            const el = document.getElementById('quiz-match-count');
            if (el) el.textContent = `Matching Questions: ${data.count}`;
        }
    } catch(e) {
        console.error("Failed to fetch match count", e);
    }
}

function handleSearchChange() {
    if (searchDebounceTimeout) clearTimeout(searchDebounceTimeout);
    searchDebounceTimeout = setTimeout(updateMatchCount, 300);
}

// Global reference for Topic Editor to use if needed
window.pendingQuizData = null;

async function startQuiz() {
    const subject = document.getElementById('quiz-subject-select').value;
    const topic = document.getElementById('quiz-topic-select').value;
    const keyword = document.getElementById('quiz-keyword').value;

    // Retrieve new settings from the UI if they exist, otherwise fallback to defaults
    const count = parseInt(document.getElementById('quiz-count')?.value) || 10;
    const timeLimit = parseInt(document.getElementById('quiz-time')?.value) || 0;

    // Default mode logic (assuming UI sets this, but fallback to test)
    const modeElements = document.querySelectorAll('button[data-mode]');
    let selectedMode = 'test';
    modeElements.forEach(btn => {
        if(btn.classList.contains('bg-brand-600') || btn.classList.contains('bg-purple-600')) {
            selectedMode = btn.getAttribute('data-mode') || 'test';
        }
    });

    const questionTimeoutMinutes = 5; // Default to 5 mins if no UI element exists

    const payload = {
        subjects: subject === 'all' ? null : [subject],
        topics: topic === 'all' ? null : [topic],
        keyword: keyword ? keyword : null,
        count: selectedMode === 'target' ? 100 : count // Target mode needs a larger pool
    };

    try {
        const res = await fetch('/api/mcq/generate_set', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });

        if(res.ok) {
            const data = await res.json();
            if(data.questions && data.questions.length > 0) {
                window.pendingQuizData = {
                    config: {
                        subjectName: subject === 'all' ? 'All Subjects' : subject,
                        timeLimitMinutes: timeLimit,
                        questionTimeoutMinutes: questionTimeoutMinutes,
                        mode: selectedMode,
                        targetCorrect: selectedMode === 'target' ? count : null
                    },
                    questions: data.questions
                };

                document.getElementById('pre-quiz-modal').style.display = 'flex';
            } else {
                alert("No questions found for the selected criteria.");
            }
        } else {
             alert("Error generating quiz.");
        }
    } catch(e) {
        console.error(e);
        alert("Failed to start quiz.");
    }
}

function beginQuizRunner() {
    document.getElementById('pre-quiz-modal').style.display = 'none';
    if (!window.pendingQuizData) return;

    currentQuiz = window.pendingQuizData;
    currentQuestionIndex = 0;
    quizAnswers = {};
    quizStartTime = Date.now();
    isPaused = false;
    correctCount = 0;
    wrongCount = 0;

    document.getElementById('quiz-setup').style.display = 'none';
    document.getElementById('quiz-results').style.display = 'none';
    document.getElementById('quiz-active').style.display = 'block';

    renderQuestion();

    if (currentQuiz.config.timeLimitMinutes > 0) {
        startTimer(currentQuiz.config.timeLimitMinutes * 60);
    }

    window.pendingQuizData = null;
}

function startTimer(seconds) {
    let timeLeft = seconds;
    const timerEl = document.getElementById('quiz-timer-display');

    quizTimer = setInterval(() => {
        if (isPaused) return; // Freeze timer if taking a break

        timeLeft--;
        if (timeLeft <= 0) {
            clearInterval(quizTimer);
            finishQuiz();
        } else {
            const m = Math.floor(timeLeft / 60);
            const s = timeLeft % 60;
            if(timerEl) timerEl.textContent = `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
        }
    }, 1000);
}

function resetQuestionTimeout() {
    if (questionTimeoutTimer) clearInterval(questionTimeoutTimer);
    if (!currentQuiz || currentQuiz.config.questionTimeoutMinutes <= 0) return;

    questionTimeoutTimer = setInterval(() => {
        if (isPaused) return;
        const elapsed = Math.floor((Date.now() - questionStartTime) / 1000);
        if (elapsed >= (currentQuiz.config.questionTimeoutMinutes * 60)) {
            triggerBreakModal();
        }
    }, 1000);
}

function triggerBreakModal() {
    isPaused = true;
    if (questionTimeoutTimer) clearInterval(questionTimeoutTimer);

    // Inject the Break UI into the active quiz container
    const activeContainer = document.getElementById('quiz-active');
    activeContainer.innerHTML = `
        <div class="bg-gray-800/60 border border-warning-500/30 rounded-2xl p-8 text-center animate-fade-in">
            <div class="text-warning-500 mb-4 flex justify-center">
                <svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 8h1a4 4 0 1 1 0 8h-1"></path><path d="M3 8h14v9a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4Z"></path><line x1="6" y1="2" x2="6" y2="4"></line><line x1="10" y1="2" x2="10" y2="4"></line><line x1="14" y1="2" x2="14" y2="4"></line></svg>
            </div>
            <h2 class="text-2xl font-bold text-gray-100 mb-2">Need a Break?</h2>
            <p class="text-gray-400 mb-6">
                No answer was given for ${currentQuiz.config.questionTimeoutMinutes} minutes.
                Take a moment if you need to - your progress is saved.
            </p>
            <div class="flex flex-col gap-3 max-w-md mx-auto">
                <button onclick="resumeQuiz()" class="w-full py-3 bg-brand-600 text-white font-bold rounded-xl hover:bg-brand-500 transition-all flex items-center justify-center gap-2">
                    Continue Quiz
                </button>
                <button onclick="skipQuestionDueToTimeout()" class="w-full py-3 bg-gray-700 text-gray-300 font-medium rounded-xl hover:bg-gray-600 transition-all">
                    Skip This Question
                </button>
                <button onclick="finishQuiz()" class="w-full py-2 text-gray-500 hover:text-gray-300 transition-colors text-sm border-none bg-transparent">
                    End Quiz & See Results
                </button>
            </div>
        </div>
    `;
}

window.resumeQuiz = function() {
    isPaused = false;
    questionStartTime = Date.now(); // Reset question start time so they get a fresh window
    renderQuestion(); // Re-render the normal question view
};

window.skipQuestionDueToTimeout = function() {
    isPaused = false;
    const q = currentQuiz.questions[currentQuestionIndex];
    const qId = q.id || currentQuestionIndex;

    quizAnswers[qId] = {
        selected: '',
        isCorrect: false,
        timeSpent: Date.now() - questionStartTime,
        timedOut: true
    };

    wrongCount++;
    nextQuestion();
};

function renderQuestion() {
    if (autoAdvanceTimer) clearTimeout(autoAdvanceTimer);

    const activeContainer = document.getElementById('quiz-active');
    const q = currentQuiz.questions[currentQuestionIndex];
    const qId = q.id || currentQuestionIndex;
    const isAnswered = !!quizAnswers[qId];

    // Reset exact timestamp for accurate FSRS elapsed time calculation
    if (!isAnswered) {
        questionStartTime = Date.now();
        resetQuestionTimeout();
    }

    const isTestMode = currentQuiz.config.mode === 'test';
    const isTargetMode = currentQuiz.config.mode === 'target';

    let headerText = '';
    if (isTargetMode) {
        headerText = `Target: ${correctCount} / ${currentQuiz.config.targetCorrect} Correct`;
    } else {
        headerText = `Question ${currentQuestionIndex + 1} of ${currentQuiz.questions.length}`;
    }

    // Build the UI dynamically
    let html = `
        <div class="flex justify-between items-center mb-10 flex-wrap gap-4">
            <div class="flex items-center gap-4">
                <div class="bg-gray-800 rounded-xl px-4 py-2">
                    <span class="text-brand-400 font-bold text-lg">${headerText}</span>
                </div>
                <div class="flex items-center gap-3 text-sm">
                    <span class="text-success-500 flex items-center gap-1">✓ ${correctCount}</span>
                    <span class="text-danger-500 flex items-center gap-1">✗ ${wrongCount}</span>
                </div>
            </div>
            <div class="flex items-center gap-3">
                <span class="px-3 py-1 rounded-full text-xs font-medium bg-brand-500/20 text-brand-400 uppercase">
                    ${currentQuiz.config.mode}
                </span>
                <div id="quiz-timer-display" class="text-xl font-mono text-brand-400 font-bold"></div>
            </div>
        </div>

        <div class="bg-gray-800/60 backdrop-blur-sm rounded-2xl border border-gray-700 p-8 mb-6">
            <div class="mb-4">
                <span class="px-3 py-1 bg-brand-600/20 text-brand-400 rounded-full text-xs font-medium">
                    ${escapeHTML(q.topic_name || 'General')}
                </span>
            </div>
            <h2 class="text-xl font-semibold text-gray-100 leading-relaxed mb-8">
                ${escapeHTML(q.question_text || q.question)}
            </h2>
            <div id="quiz-options" class="flex flex-col gap-3"></div>
        </div>

        <div id="quiz-feedback-container"></div>

        <div class="flex justify-between mt-6">
            <button id="quiz-prev-btn" class="secondary-btn" ${currentQuestionIndex === 0 ? 'disabled' : ''}>Previous</button>
            <button id="quiz-next-btn" class="secondary-btn" ${!isAnswered && !isTestMode ? 'disabled' : ''}>Next Question</button>
        </div>
    `;

    activeContainer.innerHTML = html;

    // Attach Options
    const optsContainer = document.getElementById('quiz-options');
    ['a', 'b', 'c', 'd'].forEach(opt => {
        const optText = q[`option_${opt}`] || q[opt];
        if(optText) {
            const btn = document.createElement('button');
            const upperOpt = opt.toUpperCase();

            let btnClass = 'quiz-option-btn w-full text-left p-4 rounded-xl border-2 transition-all flex items-center justify-between ';

            if (!isAnswered) {
                btnClass += 'bg-gray-800 border-gray-600 hover:border-brand-400 hover:bg-gray-700 cursor-pointer';
            } else {
                btnClass += 'cursor-default ';
                const prevAns = quizAnswers[qId];
                const isCorrectOpt = upperOpt === q.correct_answer?.toUpperCase();
                const isSelected = prevAns.selected === upperOpt;

                if (prevAns.timedOut) {
                    btnClass += 'bg-gray-800/50 border-gray-700 text-gray-500';
                } else if (isCorrectOpt) {
                    btnClass += 'bg-success-500/20 border-success-500 text-success-500';
                } else if (isSelected && !prevAns.isCorrect) {
                    btnClass += 'bg-danger-500/20 border-danger-500 text-danger-500';
                } else {
                    btnClass += 'bg-gray-800/50 border-gray-700 text-gray-500';
                }
            }

            btn.className = btnClass;
            btn.innerHTML = `
                <div class="flex items-center gap-4">
                    <span class="w-8 h-8 rounded-lg flex items-center justify-center text-sm font-bold bg-gray-700 text-gray-300">
                        ${upperOpt}
                    </span>
                    <span class="text-sm">${escapeHTML(optText)}</span>
                </div>
            `;

            if (!isAnswered) {
                btn.onclick = () => selectOption(qId, upperOpt);
            }
            optsContainer.appendChild(btn);
        }
    });

    // Attach Navigation
    document.getElementById('quiz-prev-btn').onclick = prevQuestion;
    const nextBtn = document.getElementById('quiz-next-btn');

    if (isTargetMode) {
        nextBtn.textContent = 'Skip';
        nextBtn.onclick = skipQuestionDueToTimeout;
        if (isAnswered) nextBtn.style.display = 'none'; // Auto advance handles it
    } else if (currentQuestionIndex >= currentQuiz.questions.length - 1) {
        nextBtn.textContent = 'Finish Quiz';
        nextBtn.classList.replace('secondary-btn', 'primary-btn');
        nextBtn.onclick = finishQuiz;
    } else {
        nextBtn.onclick = nextQuestion;
    }

    // Handle Feedback & Explanations (Learn Mode)
    if (isAnswered && !isTestMode && !isTargetMode) {
        const fbContainer = document.getElementById('quiz-feedback-container');
        const ans = quizAnswers[qId];

        let explanationHtml = `
            <div class="animate-fade-in bg-brand-600/10 border border-brand-500/30 rounded-2xl p-6 mb-6">
                <h4 class="font-semibold text-brand-400 mb-2">Explanation</h4>
                <div class="text-gray-300 text-sm leading-relaxed">
                    ${window.DOMPurify && window.marked ? window.DOMPurify.sanitize(window.marked.parse(q.explanation || 'No explanation provided.')) : escapeHTML(q.explanation || 'No explanation provided.')}
                </div>
            </div>
        `;
        fbContainer.innerHTML = explanationHtml;
    }
}

function selectOption(qId, option) {
    if (questionTimeoutTimer) clearInterval(questionTimeoutTimer);

    const q = currentQuiz.questions[currentQuestionIndex];
    const isCorrect = option.toUpperCase() === (q.correct_answer || '').toUpperCase();
    const timeSpent = Date.now() - questionStartTime;

    quizAnswers[qId] = {
        selected: option,
        isCorrect: isCorrect,
        timeSpent: timeSpent,
        timedOut: false
    };

    if (isCorrect) correctCount++;
    else wrongCount++;

    const isTestMode = currentQuiz.config.mode === 'test';
    const isTargetMode = currentQuiz.config.mode === 'target';

    renderQuestion(); // Re-render to show correct/wrong colors

    // Target Mode Completion Check
    if (isTargetMode && correctCount >= currentQuiz.config.targetCorrect) {
        autoAdvanceTimer = setTimeout(finishQuiz, 1500);
        return;
    }

    // Auto Advance Logic
    if (isTestMode || isTargetMode) {
        autoAdvanceTimer = setTimeout(() => {
            if (currentQuestionIndex < currentQuiz.questions.length - 1) {
                nextQuestion();
            } else {
                finishQuiz();
            }
        }, 1500);
    }
}

function nextQuestion() {
    if (currentQuestionIndex < currentQuiz.questions.length - 1) {
        currentQuestionIndex++;
        renderQuestion();
    } else {
        finishQuiz();
    }
}

function prevQuestion() {
    if (currentQuestionIndex > 0) {
        currentQuestionIndex--;
        renderQuestion();
    }
}

async function finishQuiz() {
    if (quizTimer) clearInterval(quizTimer);
    if (questionTimeoutTimer) clearInterval(questionTimeoutTimer);
    if (autoAdvanceTimer) clearTimeout(autoAdvanceTimer);

    const answersList = [];
    const scorePromises = [];

    // Filter questions that were actually answered/viewed
    const answeredQuestions = [];

    currentQuiz.questions.forEach((q, idx) => {
        const qId = q.id || idx;
        const ans = quizAnswers[qId];

        if (ans) {
            answeredQuestions.push(q);
            answersList.push({
                questionId: qId.toString(),
                selectedOption: ans.selected,
                isCorrect: ans.isCorrect,
                timeSpentSeconds: ans.timeSpent
            });

            // FSRS Algorithmic Scoring Linkage
            const numericMcqId = parseInt(qId, 10);
            if (!isNaN(numericMcqId) && String(numericMcqId) === String(qId)) {
                let rating = 3; // Default: 3 = Good

                if (!ans.isCorrect) {
                    rating = 1; // 1 = Again (Failed)
                } else {
                    const timeSec = ans.timeSpent / 1000;
                    if (timeSec > 30) rating = 2; // 2 = Hard (Struggled)
                    else if (timeSec < 8) rating = 4; // 4 = Easy (Instant Recall)
                }

                scorePromises.push(
                    fetch('/api/mcq/score', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ mcq_id: numericMcqId, rating: rating })
                    }).catch(e => console.error("FSRS Score Error:", e))
                );
            }
        }
    });

    const timeUsed = Math.floor((Date.now() - quizStartTime) / 1000);
    const accuracy = answeredQuestions.length > 0 ? (correctCount / answeredQuestions.length) * 100 : 0;

    const result = {
        id: crypto.randomUUID(),
        subjectName: currentQuiz.config.subjectName,
        timestamp: Date.now(),
        totalQuestions: answeredQuestions.length,
        correctAnswers: correctCount,
        accuracy: accuracy,
        timeUsedSeconds: timeUsed,
        mode: currentQuiz.config.mode,
        answers: answersList
    };

    // Update Habit State natively
    const state = window.AppState.get();
    const today = new Date().toISOString().split('T')[0];
    let habit = state.habitLog.find(h => h.date === today);
    if (!habit) {
        habit = { date: today, questionsAnswered: 0, correctAnswers: 0, timeSpentSeconds: 0 };
        state.habitLog.push(habit);
    }
    habit.questionsAnswered += answeredQuestions.length;
    habit.correctAnswers += correctCount;
    habit.timeSpentSeconds += timeUsed;

    state.testResults.push(result);
    window.AppState.update({ habitLog: state.habitLog, testResults: state.testResults });

    // Execute background APIs concurrently
    try {
        await Promise.all([
            fetch('/api/sessions/save', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ ...result, questions: answeredQuestions })
            }),
            ...scorePromises
        ]);
    } catch(e) {
        console.error("Failed to save session or FSRS scores to backend", e);
    }

    showResults(result, answeredQuestions);
}

function showResults(result, answeredQuestions) {
    document.getElementById('quiz-active').style.display = 'none';
    document.getElementById('quiz-results').style.display = 'block';

    document.getElementById('res-score').textContent = `${result.accuracy.toFixed(1)}%`;
    document.getElementById('res-correct').textContent = `${result.correctAnswers} / ${result.totalQuestions}`;

    const m = Math.floor(result.timeUsedSeconds / 60);
    const s = result.timeUsedSeconds % 60;
    document.getElementById('res-time').textContent = `${m}m ${s}s`;

    // Extract wrong questions for the "Practice Wrong Answers" feature
    const wrongAnswers = result.answers.filter(a => !a.isCorrect && !a.timedOut);
    const wrongQuestions = wrongAnswers.map(ans => answeredQuestions.find(q => String(q.id) === String(ans.questionId))).filter(Boolean);

    const practiceWrongBtn = document.getElementById('practice-wrong-btn');
    if (practiceWrongBtn) {
        if (wrongQuestions.length > 0) {
            practiceWrongBtn.style.display = 'inline-flex';
            practiceWrongBtn.onclick = () => {
                window.pendingQuizData = {
                    config: {
                        subjectName: `${result.subjectName} (Review)`,
                        timeLimitMinutes: 0,
                        questionTimeoutMinutes: 5,
                        mode: 'learn',
                        targetCorrect: null
                    },
                    questions: wrongQuestions
                };
                beginQuizRunner();
            };
        } else {
            practiceWrongBtn.style.display = 'none';
        }
    }

    // Safe Explanation Injection via DOMPurify
    const container = document.getElementById('res-explanations');
    container.innerHTML = '';

    answeredQuestions.forEach((q, idx) => {
        const ans = result.answers.find(a => a.questionId === String(q.id || idx));
        const isCorrect = ans ? ans.isCorrect : false;

        const div = document.createElement('div');
        div.className = `p-4 mb-4 rounded border ${isCorrect ? 'border-success-500 bg-success-900/20' : 'border-danger-500 bg-danger-900/20'}`;

        const rawExplanation = q.explanation || 'No explanation provided.';
        const cleanExplanation = window.DOMPurify && window.marked
            ? window.DOMPurify.sanitize(window.marked.parse(rawExplanation))
            : escapeHTML(rawExplanation);

        div.innerHTML = `
            <div class="flex justify-between items-start mb-2">
                <h4 class="font-bold text-gray-200 flex-1 pr-4">Q: ${escapeHTML(q.question_text || q.question)}</h4>
                <span class="text-xs text-gray-500 shrink-0">⏱️ ${ans && ans.timeSpentSeconds ? (ans.timeSpentSeconds/1000).toFixed(1) + 's' : '0s'}</span>
            </div>
            <p class="text-sm mb-1 text-gray-300">Your Answer: <span class="font-bold">${ans ? escapeHTML(ans.selectedOption || 'Skipped') : 'Skipped'}</span></p>
            <p class="text-sm mb-3 text-gray-300">Correct Answer: <span class="font-bold">${escapeHTML(q.correct_option || q.answer || q.correct_answer)}</span></p>
            <div class="text-sm text-gray-300 bg-gray-900 p-4 rounded border border-gray-700">
                <strong class="text-brand-400 block mb-1">Explanation:</strong>
                <div class="prose prose-sm prose-invert max-w-none">${cleanExplanation}</div>
            </div>
            ${q.topic_name ? `<div class="mt-3 text-xs font-semibold text-brand-400 uppercase tracking-wider">Topic: ${escapeHTML(q.topic_name)}</div>` : ''}
        `;
        container.appendChild(div);
    });
}

function resetQuizView() {
    document.getElementById('quiz-results').style.display = 'none';
    document.getElementById('quiz-setup').style.display = 'block';
    currentQuiz = null;
    if (quizTimer) clearInterval(quizTimer);
    if (questionTimeoutTimer) clearInterval(questionTimeoutTimer);
}

document.addEventListener('DOMContentLoaded', () => {
    loadSubjects();
    loadTopics();

    document.getElementById('start-quiz-btn')?.addEventListener('click', startQuiz);
    document.getElementById('quiz-next-btn')?.addEventListener('click', nextQuestion);
    document.getElementById('quiz-prev-btn')?.addEventListener('click', prevQuestion);
    document.getElementById('quiz-done-btn')?.addEventListener('click', resetQuizView);

    // Search bindings
    document.getElementById('quiz-subject-select')?.addEventListener('change', handleSearchChange);
    document.getElementById('quiz-topic-select')?.addEventListener('change', handleSearchChange);
    document.getElementById('quiz-keyword')?.addEventListener('input', handleSearchChange);

    // Modal bindings
    document.getElementById('start-mcq-directly-btn')?.addEventListener('click', beginQuizRunner);
    document.getElementById('cancel-pre-quiz-btn')?.addEventListener('click', () => {
        document.getElementById('pre-quiz-modal').style.display = 'none';
        window.pendingQuizData = null;
    });

    // Make sure we inject the Practice Wrong button into the Results view if it doesn't exist
    const resultsContainer = document.getElementById('quiz-results');
    if (resultsContainer && !document.getElementById('practice-wrong-btn')) {
        const actionsDiv = document.createElement('div');
        actionsDiv.className = "flex gap-4 mt-6 justify-center";
        actionsDiv.innerHTML = `
            <button id="practice-wrong-btn" class="primary-btn" style="display: none; background: #9333ea;">🧠 Practice Wrong Answers</button>
        `;
        resultsContainer.insertBefore(actionsDiv, document.getElementById('quiz-done-btn'));
    }
});
