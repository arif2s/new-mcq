let currentQuiz = null;
let currentQuestionIndex = 0;
let quizAnswers = {};
let quizTimer = null;
let quizStartTime = null;

async function loadSubjects() {
    try {
        const res = await fetch('/api/mcq/subjects');
        const subjects = await res.json();
        const sel = document.getElementById('quiz-subject-select');
        if(sel) {
            sel.innerHTML = '<option value="all">All Subjects</option>';
            subjects.forEach(s => {
                sel.innerHTML += `<option value="${s.subject}">${s.subject} (${s.count})</option>`;
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
                sel.innerHTML += `<option value="${t.topic}">${t.topic} (${t.count})</option>`;
            });
        }
    } catch(e) {
        console.error("Failed to load topics", e);
    }
}

async function startQuiz() {
    const subject = document.getElementById('quiz-subject-select').value;
    const topic = document.getElementById('quiz-topic-select').value;
    const count = parseInt(document.getElementById('quiz-count').value) || 10;
    const timeLimit = parseInt(document.getElementById('quiz-time').value) || 0;

    const payload = {
        subjects: subject === 'all' ? null : [subject],
        topics: topic === 'all' ? null : [topic],
        count: count
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
                currentQuiz = {
                    config: { subjectName: subject, timeLimitMinutes: timeLimit },
                    questions: data.questions
                };
                currentQuestionIndex = 0;
                quizAnswers = {};
                quizStartTime = Date.now();

                document.getElementById('quiz-setup').style.display = 'none';
                document.getElementById('quiz-active').style.display = 'block';

                renderQuestion();

                if (timeLimit > 0) {
                    startTimer(timeLimit * 60);
                }
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

function startTimer(seconds) {
    let timeLeft = seconds;
    const timerEl = document.getElementById('quiz-timer-display');

    quizTimer = setInterval(() => {
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

function renderQuestion() {
    const q = currentQuiz.questions[currentQuestionIndex];
    document.getElementById('quiz-progress').textContent = `Question ${currentQuestionIndex + 1} of ${currentQuiz.questions.length}`;
    document.getElementById('quiz-stem').textContent = q.question_text || q.question;

    const optsContainer = document.getElementById('quiz-options');
    optsContainer.innerHTML = '';

    ['a', 'b', 'c', 'd'].forEach(opt => {
        const optText = q[`option_${opt}`] || q[opt];
        if(optText) {
            const btn = document.createElement('button');
            btn.className = 'quiz-option-btn glass-card w-full text-left p-3 mb-2 rounded border border-gray-600 hover:border-brand-500 transition-colors';

            // Check if already answered
            const prevAns = quizAnswers[q.id || currentQuestionIndex];
            if(prevAns && prevAns.selected === opt) {
                 btn.classList.add('bg-brand-600', 'text-white');
            }

            btn.innerHTML = `<span class="font-bold mr-2">${opt.toUpperCase()})</span> ${optText}`;
            btn.onclick = () => selectOption(q.id || currentQuestionIndex, opt, btn);
            optsContainer.appendChild(btn);
        }
    });

    document.getElementById('quiz-prev-btn').disabled = currentQuestionIndex === 0;

    const nextBtn = document.getElementById('quiz-next-btn');
    if (currentQuestionIndex === currentQuiz.questions.length - 1) {
        nextBtn.textContent = 'Finish Quiz';
        nextBtn.classList.replace('secondary-btn', 'primary-btn');
    } else {
        nextBtn.textContent = 'Next Question';
        nextBtn.classList.replace('primary-btn', 'secondary-btn');
    }
}

function selectOption(qId, option, btnEl) {
    const q = currentQuiz.questions[currentQuestionIndex];
    const isCorrect = option.toLowerCase() === (q.correct_option || q.answer || '').toLowerCase();

    quizAnswers[qId] = {
        selected: option,
        isCorrect: isCorrect,
        timeSpent: 0 // Simplification for now
    };

    renderQuestion(); // Re-render to show selection
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

    let correct = 0;
    const answersList = [];

    currentQuiz.questions.forEach((q, idx) => {
        const ans = quizAnswers[q.id || idx];
        if (ans) {
            if (ans.isCorrect) correct++;
            answersList.push({
                questionId: q.id || idx,
                selectedOption: ans.selected,
                isCorrect: ans.isCorrect,
                timeSpentSeconds: ans.timeSpent
            });
        }
    });

    const timeUsed = Math.floor((Date.now() - quizStartTime) / 1000);
    const accuracy = currentQuiz.questions.length > 0 ? (correct / currentQuiz.questions.length) * 100 : 0;

    const result = {
        id: crypto.randomUUID(),
        subjectName: currentQuiz.config.subjectName,
        timestamp: Date.now(),
        totalQuestions: currentQuiz.questions.length,
        correctAnswers: correct,
        accuracy: accuracy,
        timeUsedSeconds: timeUsed,
        mode: 'test',
        answers: answersList
    };

    // Update Habit State
    const state = window.AppState.get();
    const today = new Date().toISOString().split('T')[0];
    let habit = state.habitLog.find(h => h.date === today);
    if (!habit) {
        habit = { date: today, questionsAnswered: 0, correctAnswers: 0, timeSpentSeconds: 0 };
        state.habitLog.push(habit);
    }
    habit.questionsAnswered += currentQuiz.questions.length;
    habit.correctAnswers += correct;
    habit.timeSpentSeconds += timeUsed;

    state.testResults.push(result);
    window.AppState.update({ habitLog: state.habitLog, testResults: state.testResults });

    // Attempt backend save
    try {
        await fetch('/api/sessions/save', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ ...result, questions: currentQuiz.questions })
        });
    } catch(e) {
        console.error("Failed to save session to backend", e);
    }

    showResults(result);
}

function showResults(result) {
    document.getElementById('quiz-active').style.display = 'none';
    document.getElementById('quiz-results').style.display = 'block';

    document.getElementById('res-score').textContent = `${result.accuracy.toFixed(1)}%`;
    document.getElementById('res-correct').textContent = `${result.correctAnswers} / ${result.totalQuestions}`;

    const m = Math.floor(result.timeUsedSeconds / 60);
    const s = result.timeUsedSeconds % 60;
    document.getElementById('res-time').textContent = `${m}m ${s}s`;

    const container = document.getElementById('res-explanations');
    container.innerHTML = '';

    currentQuiz.questions.forEach((q, idx) => {
        const ans = result.answers.find(a => a.questionId === (q.id || idx));
        const isCorrect = ans ? ans.isCorrect : false;

        const div = document.createElement('div');
        div.className = `p-4 mb-4 rounded border ${isCorrect ? 'border-success-500 bg-success-900/20' : 'border-danger-500 bg-danger-900/20'}`;

        div.innerHTML = `
            <h4 class="font-bold mb-2">Q: ${q.question_text || q.question}</h4>
            <p class="text-sm mb-1">Your Answer: <span class="font-bold">${ans ? ans.selectedOption : 'Skipped'}</span></p>
            <p class="text-sm mb-3">Correct Answer: <span class="font-bold">${q.correct_option || q.answer}</span></p>
            <div class="text-sm text-gray-300 bg-gray-900 p-3 rounded">
                <strong>Explanation:</strong> ${q.explanation || 'No explanation provided.'}
            </div>
            ${q.topic_name ? `<div class="mt-2 text-xs text-brand-400">Topic: ${q.topic_name}</div>` : ''}
        `;
        container.appendChild(div);
    });
}

function resetQuizView() {
    document.getElementById('quiz-results').style.display = 'none';
    document.getElementById('quiz-setup').style.display = 'block';
    currentQuiz = null;
}

document.addEventListener('DOMContentLoaded', () => {
    loadSubjects();
    loadTopics();

    document.getElementById('start-quiz-btn')?.addEventListener('click', startQuiz);
    document.getElementById('quiz-next-btn')?.addEventListener('click', nextQuestion);
    document.getElementById('quiz-prev-btn')?.addEventListener('click', prevQuestion);
    document.getElementById('quiz-done-btn')?.addEventListener('click', resetQuizView);
});
