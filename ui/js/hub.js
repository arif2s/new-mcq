// ui/js/hub.js

let hubSessions = [];
let hubSelected = new Set();
let isEditing = false;
let activeInspectorSessionId = null;
let currentSessionTopics = [];
let carouselInterval = null;
let currentTopicIndex = 0;
let hubSocket = null;

// Expose globally so app.js and quiz.js can refresh the Hub after saving or confirming topics
window.loadSessions = loadSessions;
window.clearActiveInspectorSession = function() {
    activeInspectorSessionId = null;
};

window.renderHubInspectorIfActive = function() {
    if (activeInspectorSessionId && Array.isArray(hubSessions)) {
        const activeSession = hubSessions.find(s => s.id === activeInspectorSessionId);
        if (activeSession) {
            openHubCardInspector(activeSession, false);
            return;
        }
    }
    if (typeof window.closeInspector === 'function') {
        window.closeInspector();
    }
};

window.closeTopicCarousel = function() {
    const carousel = document.getElementById('hub-carousel-container');
    if (carousel) carousel.style.display = 'none';
    if (carouselInterval) {
        clearInterval(carouselInterval);
        carouselInterval = null;
    }
};

async function loadSessions(silent = false) {
    const loadingEl = document.getElementById('hub-loading');
    const emptyEl = document.getElementById('hub-empty');
    const gridEl = document.getElementById('hub-grid');

    if (!silent) {
        if (loadingEl) loadingEl.style.display = 'block';
        if (emptyEl) emptyEl.style.display = 'none';
        if (gridEl) gridEl.style.display = 'none';
    }

    try {
        const res = await fetch('/api/sessions/history');
        if (res.ok) {
            hubSessions = await res.json();
            renderHub();
        }
    } catch (e) {
        console.error("Failed to load sessions:", e);
        if (loadingEl) loadingEl.style.display = 'none';
    }
}

function getCircleStates(session) {
    const c1Active = true; // Circle 1 is always active once the set is created
    const reviewed = Boolean(session.topics_reviewed);
    const refsStatus = (session.refs_status || 'none').toLowerCase();
    const llmStatus = (session.llm_status || 'none').toLowerCase();

    // Circle 2: Greyed if topics not reviewed; blinking if 'processing'; solid if 'ready'
    let c2State = 'grey';
    if (reviewed) {
        if (refsStatus === 'processing') c2State = 'blinking';
        else if (refsStatus === 'ready') c2State = 'solid';
        else c2State = 'blinking';
    }

    // Circle 3: Greyed if topics not reviewed; blinking if 'processing'; solid if 'ready'; red if 'failed'/'partial'
    let c3State = 'grey';
    if (reviewed) {
        if (llmStatus === 'processing') c3State = 'blinking';
        else if (llmStatus === 'ready') c3State = 'solid';
        else if (llmStatus === 'failed' || llmStatus === 'partial') c3State = llmStatus;
    }

    return { c1: c1Active ? 'solid' : 'grey', c2: c2State, c3: c3State };
}

function renderStatusCirclesHTML(session) {
    const { c1, c2, c3 } = getCircleStates(session);
    const reviewed = Boolean(session.topics_reviewed);

    const styleFor = (state) => {
        if (state === 'solid') {
            return 'bg-success-500 text-white border-success-400 shadow-[0_0_8px_rgba(34,197,94,0.5)]';
        }
        if (state === 'blinking') {
            return 'bg-brand-500 text-white border-brand-400 animate-pulse shadow-[0_0_10px_rgba(168,85,247,0.8)]';
        }
        if (state === 'failed' || state === 'partial') {
            return 'bg-danger-500 text-white border-danger-400 shadow-[0_0_8px_rgba(239,68,68,0.6)]';
        }
        return 'glass-panel text-muted opacity-40 border-gray-600';
    };

    const c1Title = reviewed
        ? '1: Question Set Created & Topics Verified'
        : '1: Question Set Created (Topic list not yet reviewed — click to review)';
    const c2Title = c2 === 'solid'
        ? '2: Reference Links Resolved (PDF, Obsidian, ZIM)'
        : c2 === 'blinking'
            ? '2: Resolving Reference Links...'
            : '2: Pending Topic Review';
    const c3Title = c3 === 'solid'
        ? '3: LM Studio Notes & MCQ Explanations Ready'
        : c3 === 'blinking'
            ? '3: LM Studio is Generating Notes & Explanations...'
            : c3 === 'failed'
                ? '3: LLM Processing Completely Failed (Click card to view details & retry)'
                : c3 === 'partial'
                    ? '3: LLM Processing Partially Completed / Failed (Click card to view details & retry)'
                    : '3: LLM Notes Not Generated';

    return `
        <div class="hub-status-circles flex items-center gap-1.5" data-session-id="${window.escapeHTML(session.id)}">
            <span class="hub-circle-1 w-6 h-6 rounded-full text-[11px] font-bold flex items-center justify-center border transition-all ${styleFor(c1)} ${!reviewed ? 'cursor-pointer hover:scale-110' : ''}"
                  title="${c1Title}">1</span>
            <span class="w-2 h-[1px] bg-gray-600"></span>
            <span class="hub-circle-2 w-6 h-6 rounded-full text-[11px] font-bold flex items-center justify-center border transition-all ${styleFor(c2)}"
                  title="${c2Title}">2</span>
            <span class="w-2 h-[1px] bg-gray-600"></span>
            <span class="hub-circle-3 w-6 h-6 rounded-full text-[11px] font-bold flex items-center justify-center border transition-all ${styleFor(c3)}"
                  title="${c3Title}">3</span>
        </div>
    `;
}

function renderHub() {
    const loadingEl = document.getElementById('hub-loading');
    const emptyEl = document.getElementById('hub-empty');
    const gridEl = document.getElementById('hub-grid');

    if (loadingEl) loadingEl.style.display = 'none';

    if (!hubSessions || hubSessions.length === 0) {
        if (emptyEl) emptyEl.style.display = 'block';
        if (gridEl) gridEl.style.display = 'none';
        return;
    }

    if (emptyEl) emptyEl.style.display = 'none';
    if (!gridEl) return;

    gridEl.style.display = 'grid';
    gridEl.innerHTML = '';

    hubSessions.forEach(session => {
        const isSelectedForDelete = hubSelected.has(session.id);
        const isInspectorActive = activeInspectorSessionId === session.id;
        const reviewed = Boolean(session.topics_reviewed);

        const totalQ = Number(session.total_questions || 0);
        const answeredQ = session.completed_questions !== undefined
            ? Number(session.completed_questions)
            : Number((session.correct_answers || 0) + (session.wrong_answers || 0));
        const isCompleted = Boolean(session.is_completed) || (totalQ > 0 && answeredQ >= totalQ);

        const cardHeading = session.custom_title || session.subject_name || 'Clinical Question Set';
        const subBadge = session.subject_name || 'General';
        const dateLabel = session.last_attempted && session.last_attempted > 0
            ? `Attempted ${new Date(session.last_attempted).toLocaleDateString()}`
            : `Created ${new Date(session.timestamp || Date.now()).toLocaleDateString()}`;

        const topics = Array.isArray(session.topics) ? session.topics.filter(t => t && t !== 'General Medical Concept') : [];

        const cardNode = document.createElement('div');
        cardNode.className = `hub-card glass-card rounded-2xl transition-all flex flex-col h-full overflow-hidden shadow-lg relative cursor-pointer ${
            isSelectedForDelete || isInspectorActive ? 'border-brand-500 ring-1 ring-brand-500' : ''
        }`;
        cardNode.dataset.sessionId = session.id;

        // Topic section: 2 lines clamped if reviewed, or "Review & Confirm Topics" button if unreviewed
        let topicsHtml = '';
        if (reviewed) {
            if (topics.length > 0) {
                const visibleTopics = topics.slice(0, 6);
                const hiddenCount = Math.max(0, topics.length - visibleTopics.length);
                topicsHtml = `
                    <div class="hub-card-topics flex flex-wrap gap-1.5 mb-4 overflow-hidden"
                         style="display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; max-height: 46px;">
                        ${visibleTopics.map(t => `
                            <span class="text-[11px] px-2.5 py-0.5 glass-panel rounded-full inline-block truncate max-w-[140px]" title="${window.escapeHTML(t)}">
                                ${window.escapeHTML(t)}
                            </span>
                        `).join('')}
                        ${hiddenCount > 0 ? `<span class="text-[10px] px-2 py-0.5 bg-brand-500/20 text-brand-300 rounded-full inline-block">+${hiddenCount} more</span>` : ''}
                    </div>
                `;
            } else {
                topicsHtml = `<div class="text-xs text-muted italic mb-4 h-[44px] flex items-center">Topics verified (General Clinical Set)</div>`;
            }
        } else {
            topicsHtml = `
                <div class="mb-4 h-[46px] flex items-center justify-between glass-panel px-3 py-2 rounded-xl border border-warning/30">
                    <span class="text-xs text-muted truncate pr-2">Topics not confirmed yet</span>
                    <button type="button" class="btn-review-topics secondary-btn px-2.5 py-1 text-xs font-semibold text-brand-300 shrink-0">
                        🩺 Review Topics
                    </button>
                </div>
            `;
        }

        const accValue = Math.round(Number(session.accuracy || 0));
        const accColor = !isCompleted && answeredQ === 0
            ? 'text-muted'
            : accValue >= 70
                ? 'text-success-500'
                : accValue >= 50
                    ? 'text-warning-500'
                    : 'text-danger-500';

        cardNode.innerHTML = `
            <div class="hub-select-container absolute top-3 right-3 z-10 ${isEditing ? '' : 'hidden'}">
                <input type="checkbox" class="hub-select-cb w-5 h-5 rounded glass-input text-brand-500 cursor-pointer" ${isSelectedForDelete ? 'checked' : ''}>
            </div>

            <div class="p-5 flex-1 hub-card-body">
                <!-- Top Row: Subject Tag + 1-2-3 Status Circles -->
                <div class="flex justify-between items-center gap-2 mb-3 ${isEditing ? 'pr-7' : ''}">
                    <span class="hub-card-subject text-xs font-medium px-2.5 py-1 bg-brand-600/20 text-brand-400 rounded-lg truncate max-w-[140px]">
                        ${window.escapeHTML(subBadge)}
                    </span>
                    ${renderStatusCirclesHTML(session)}
                </div>

                <!-- Card Heading (CSV Subject/Topic or Custom Search Topic) -->
                <h3 class="hub-card-title text-lg font-bold mb-1 truncate" title="${window.escapeHTML(cardHeading)}">
                    ${window.escapeHTML(cardHeading)}
                </h3>
                <div class="text-[11px] text-muted mb-3 flex items-center justify-between">
                    <span>${window.escapeHTML(dateLabel)}</span>
                    <span class="font-mono font-semibold text-brand-300">${answeredQ} / ${totalQ} Completed</span>
                </div>

                <!-- 2-Line Clamped Main Topics (or Review Topics Trigger) -->
                ${topicsHtml}

                <!-- Progress & Accuracy Box -->
                <div class="grid grid-cols-2 gap-3 glass-panel rounded-xl p-3 border border-gray-700/50 mb-2">
                    <div class="text-center">
                        <p class="text-[11px] text-muted mb-0.5">Questions Done</p>
                        <p class="hub-card-score text-base font-bold">${answeredQ} / ${totalQ}</p>
                    </div>
                    <div class="text-center">
                        <p class="text-[11px] text-muted mb-0.5">Accuracy</p>
                        <p class="hub-card-accuracy text-base font-bold ${accColor}">
                            ${answeredQ > 0 ? `${accValue}%` : 'Not Attempted'}
                        </p>
                    </div>
                </div>

                <div class="flex justify-between text-xs text-muted px-1">
                    <span><span class="hub-card-correct text-success-400 font-bold">${session.correct_answers || 0}</span> Correct</span>
                    <span><span class="hub-card-wrong text-danger-400 font-bold">${session.wrong_answers || 0}</span> Wrong</span>
                </div>
            </div>

            <!-- Action Footer -->
            <div class="border-t border-gray-700 p-3 glass-panel grid grid-cols-2 gap-2">
                <button type="button" class="btn-study cursor-pointer py-2 px-3 text-xs font-medium bg-brand-600/20 text-brand-400 hover:bg-brand-600/30 rounded-lg transition-colors flex items-center justify-center gap-1.5 col-span-2 border-none">
                    📖 Study Topics Covered
                </button>
                <button type="button" class="btn-review secondary-btn cursor-pointer py-2 px-3 text-xs font-medium rounded-lg transition-colors flex items-center justify-center gap-1.5">
                    📝 Explanations
                </button>
                <button type="button" class="btn-retake secondary-btn cursor-pointer py-2 px-3 text-xs font-medium rounded-lg transition-colors flex items-center justify-center gap-1.5">
                    ${answeredQ === 0 ? '▶️ Start Quiz' : '🔄 Retake Set'}
                </button>
            </div>
        `;

        // Edit Mode Checkbox Selection
        const cb = cardNode.querySelector('.hub-select-cb');
        if (isEditing && cb) {
            cb.addEventListener('change', (e) => {
                e.stopPropagation();
                if (e.target.checked) hubSelected.add(session.id);
                else hubSelected.delete(session.id);
                updateDeleteBtn();
                renderHub();
            });
        }

        // Card Body Click -> Open 3rd-Column Right Inspector (or toggle checkbox if editing)
        cardNode.querySelector('.hub-card-body')?.addEventListener('click', (e) => {
            if (e.target.closest('.btn-review-topics') || e.target.closest('.hub-circle-1')) return;
            if (isEditing && cb) {
                cb.checked = !cb.checked;
                cb.dispatchEvent(new Event('change'));
                return;
            }
            activeInspectorSessionId = session.id;
            renderHub();
            openHubCardInspector(session);
        });

        // Review Topics button or clicking Circle 1 when unreviewed -> Reopen Topic Verification Popup
        const triggerTopicPopup = async (e) => {
            e.stopPropagation();
            await reopenTopicVerificationForSession(session);
        };
        cardNode.querySelector('.btn-review-topics')?.addEventListener('click', triggerTopicPopup);
        if (!reviewed) {
            cardNode.querySelector('.hub-circle-1')?.addEventListener('click', triggerTopicPopup);
        }

        // Footer Buttons
        cardNode.querySelector('.btn-study')?.addEventListener('click', (e) => {
            e.stopPropagation();
            openTopicCarousel(session.id);
        });

        cardNode.querySelector('.btn-review')?.addEventListener('click', (e) => {
            e.stopPropagation();
            openSessionReview(session.id);
        });

        cardNode.querySelector('.btn-retake')?.addEventListener('click', (e) => {
            e.stopPropagation();
            retakeSession(session);
        });

        gridEl.appendChild(cardNode);
    });
}

async function reopenTopicVerificationForSession(session) {
    try {
        const res = await fetch(`/api/sessions/${encodeURIComponent(session.id)}/questions`);
        const questions = res.ok ? await res.json() : [];
        const mappedQuestions = questions.map(sq => ({
            id: sq.question_id,
            topic_name: sq.topic,
            question: sq.question_text,
            option_a: sq.opt_a,
            option_b: sq.opt_b,
            option_c: sq.opt_c,
            option_d: sq.opt_d,
            correct_answer: sq.correct_answer,
            explanation: sq.explanation,
            llm_explanation: sq.llm_explanation
        }));

        let savedTopicList = session.topic_list || [];
        if (typeof savedTopicList === 'string') {
            try { savedTopicList = JSON.parse(savedTopicList); } catch (_) { savedTopicList = []; }
        }

        if (typeof window.openTopicVerificationModal === 'function') {
            await window.openTopicVerificationModal({
                sessionId: session.id,
                subjectName: session.subject_name,
                customTitle: session.custom_title || session.subject_name,
                questions: mappedQuestions,
                existingTopicList: savedTopicList,
                isExistingHubSession: true
            });
        }
    } catch (e) {
        console.error("Failed to reopen topic verification popup:", e);
    }
}

// =========================================================
// Contextual 3rd-Column Right Sidebar Inspector for Hub Cards
// =========================================================

async function openHubCardInspector(session, forceOpen = true) {
    const title = session.custom_title || session.subject_name || 'Session Analytics';

    const inspectorContent = document.getElementById('inspector-content');
    if (forceOpen || !inspectorContent || !inspectorContent.innerHTML.trim()) {
        window.openInspector(
            title,
            `<div class="text-center py-10 text-muted text-xs animate-pulse">Loading session analytics &amp; queue status...</div>`,
            forceOpen
        );
    }

    let inspectorData = null;
    try {
        const res = await fetch(`/api/sessions/${encodeURIComponent(session.id)}/inspector`);
        if (res.ok) {
            inspectorData = await res.json();
        }
    } catch (e) {
        console.warn("Inspector endpoint fallback:", e);
    }

    if (inspectorData) {
        if (inspectorData.llm_status) session.llm_status = inspectorData.llm_status;
        if (inspectorData.refs_status) session.refs_status = inspectorData.refs_status;
        if (inspectorData.topics_reviewed !== undefined) session.topics_reviewed = inspectorData.topics_reviewed;
    }

    const state = window.AppState ? window.AppState.get() : {};
    const expertiseMap = state.topicExpertise || {};
    const topics = Array.isArray(session.topics) ? session.topics : [];

    const queueInfo = inspectorData?.queue_info || session.queue_info || { in_queue: false, position: 0, total_pending: 0, failed_count: 0, completed_count: 0 };
    const llmStatus = (inspectorData?.llm_status || session.llm_status || 'none').toLowerCase();
    const failedCnt = queueInfo?.failed_count || 0;
    const completedCnt = queueInfo?.completed_count || 0;

    const topicProficiency = inspectorData?.topic_proficiency || topics.map(tName => {
        const match = Object.values(expertiseMap).find(e => e.topic && e.topic.toLowerCase() === tName.toLowerCase());
        return {
            topic: tName,
            accuracy: match ? match.accuracy : 0,
            attempted: match ? match.attempted : 0,
            level: match ? match.level : 'beginner',
            last_attempted: match ? match.lastAttempted : 0
        };
    });

    const weakTopics = inspectorData?.weak_topics || topicProficiency.filter(tp => tp.attempted > 0 && tp.accuracy < 60);
    const nowMs = Date.now();
    const sevenDaysMs = 7 * 24 * 60 * 60 * 1000;
    const staleTopics = inspectorData?.stale_topics || topicProficiency.filter(tp => {
        if (!tp.last_attempted) return true;
        const tsMs = tp.last_attempted < 1e11 ? tp.last_attempted * 1000 : tp.last_attempted;
        return (nowMs - tsMs) > sevenDaysMs;
    });

    const totalQ = Number(session.total_questions || 0);
    const completedQ = session.completed_questions !== undefined
        ? Number(session.completed_questions)
        : Number((session.correct_answers || 0) + (session.wrong_answers || 0));

    const lastAttemptStr = session.last_attempted && session.last_attempted > 0
        ? new Date(session.last_attempted < 1e11 ? session.last_attempted * 1000 : session.last_attempted).toLocaleString()
        : (completedQ > 0 ? new Date(session.timestamp).toLocaleString() : 'Not attempted yet');

    let html = `<div class="space-y-5 text-xs pb-8">`;

    // 1. Attempt Summary & Pipeline Status
    html += `
        <div class="glass-card p-4 rounded-xl border border-gray-700/50">
            <div class="flex justify-between items-center mb-2">
                <span class="text-[10px] uppercase tracking-wider text-muted font-bold">Session Overview</span>
                ${renderStatusCirclesHTML(session)}
            </div>
            <p class="font-bold text-sm text-brand-300 mb-1">${window.escapeHTML(title)}</p>
            <p class="text-muted text-[11px]">🕒 <strong>Last Attempted:</strong> ${window.escapeHTML(lastAttemptStr)}</p>
            <p class="text-muted text-[11px] mt-1">📊 <strong>Progress:</strong> ${completedQ} of ${totalQ} Questions Completed</p>
            ${!session.topics_reviewed ? `
                <button type="button" id="insp-review-topics-btn" class="primary-btn w-full py-2 mt-3 text-xs font-bold rounded-lg cursor-pointer">
                    🩺 Review &amp; Confirm Topic List
                </button>
            ` : ''}
        </div>
    `;

    // 2. LLM Failure Alert Banner (with Retry Button)
    if (llmStatus === 'failed' || (llmStatus === 'partial' && completedCnt === 0)) {
        html += `
            <div class="glass-card p-4 rounded-xl border border-danger-500/40 bg-danger-500/10 text-danger-300">
                <div class="flex items-center gap-2 font-bold text-xs uppercase tracking-wider mb-1">
                    <span class="w-2.5 h-2.5 rounded-full bg-danger-500 animate-pulse"></span>
                    LLM Processing Completely Failed
                </div>
                <p class="text-[11px] opacity-80 mb-3 leading-relaxed">
                    Generation of notes or explanations failed due to a model error or lost endpoint connection.
                </p>
                <button type="button" id="btn-retry-session-llm" class="px-3 py-2 bg-danger-600 hover:bg-danger-500 text-white rounded-lg font-bold text-xs transition-colors flex items-center justify-center gap-1.5 w-full cursor-pointer shadow">
                    🔄 Retry LLM Processing
                </button>
            </div>
        `;
    } else if (llmStatus === 'partial' || failedCnt > 0) {
        html += `
            <div class="glass-card p-4 rounded-xl border border-warning-500/40 bg-warning-500/10 text-warning-300">
                <div class="flex items-center gap-2 font-bold text-xs uppercase tracking-wider mb-1">
                    <span class="w-2.5 h-2.5 rounded-full bg-warning-500 animate-pulse"></span>
                    LLM Processing Partially Completed
                </div>
                <p class="text-[11px] opacity-80 mb-3 leading-relaxed">
                    Some notes generated successfully, but ${failedCnt || 1} task(s) encountered processing errors.
                </p>
                <button type="button" id="btn-retry-session-llm" class="px-3 py-2 bg-warning-600 hover:bg-warning-500 text-white rounded-lg font-bold text-xs transition-colors flex items-center justify-center gap-1.5 w-full cursor-pointer shadow">
                    🔄 Retry Failed LLM Tasks
                </button>
            </div>
        `;
    }

    // 3. Processing Queue Position & Priority Boost
    if (queueInfo && queueInfo.in_queue) {
        html += `
            <div class="glass-card p-4 rounded-xl border border-brand-500/40 bg-brand-600/10">
                <div class="flex justify-between items-center mb-2">
                    <h4 class="font-bold text-brand-300 uppercase text-[11px]">⏳ Processing Queue Status</h4>
                    <span class="badge bg-warning">Position #${queueInfo.position} of ${queueInfo.total_pending}</span>
                </div>
                <p class="text-[11px] text-muted mb-3">
                    ${queueInfo.session_pending_count || 1} task(s) waiting for LM Studio synthesis.
                </p>
                <button type="button" id="insp-prioritize-btn" class="primary-btn w-full py-2 text-xs font-bold rounded-lg cursor-pointer">
                    🚀 Raise Priority (Move to #1 in Queue)
                </button>
            </div>
        `;
    }

    // 3. Frequently Wrong / Weak Topics + One-Click Question Set Creator
    html += `
        <div class="glass-card p-4 rounded-xl border border-gray-700/50">
            <h4 class="font-bold text-danger-400 uppercase text-[11px] mb-2">🎯 Focus Areas (Frequently Wrong Topics)</h4>
            ${weakTopics.length === 0
                ? '<p class="text-muted text-[11px]">No weak topics (<60% accuracy) detected in this set yet.</p>'
                : `<div class="space-y-2">
                    ${weakTopics.map(wt => {
                        const tName = wt.topic || wt.name || wt;
                        const acc = wt.accuracy !== undefined ? `${Math.round(wt.accuracy)}%` : 'Focus';
                        return `
                            <div class="glass-panel p-2.5 rounded-lg flex items-center justify-between gap-2">
                                <div class="min-w-0 flex-1">
                                    <p class="font-semibold truncate text-xs">${window.escapeHTML(tName)}</p>
                                    <p class="text-[10px] text-danger-400">Accuracy: ${acc}</p>
                                </div>
                                <button type="button" class="insp-create-set-btn secondary-btn px-2.5 py-1 text-[11px] shrink-0"
                                        data-topic="${window.escapeHTML(tName)}">
                                    🎯 Create Set
                                </button>
                            </div>
                        `;
                    }).join('')}
                </div>`
            }
        </div>
    `;

    // 4. Stale Revision Alerts (> 7 days since last attempt)
    if (staleTopics.length > 0) {
        html += `
            <div class="glass-card p-4 rounded-xl border border-warning/30">
                <h4 class="font-bold text-warning uppercase text-[11px] mb-2">⏰ Revision Due (Unpracticed &gt; 7 Days)</h4>
                <div class="flex flex-wrap gap-1.5">
                    ${staleTopics.slice(0, 10).map(st => {
                        const tName = st.topic || st.name || st;
                        return `
                            <button type="button" class="insp-create-set-btn text-[11px] px-2.5 py-1 rounded-full glass-panel border border-warning/30 hover:border-brand-500 cursor-pointer"
                                    data-topic="${window.escapeHTML(tName)}"
                                    title="Click to create a revision question set on ${window.escapeHTML(tName)}">
                                ↻ ${window.escapeHTML(tName)}
                            </button>
                        `;
                    }).join('')}
                </div>
            </div>
        `;
    }

    // 5. Topic Proficiency Breakdown
    html += `
        <div class="glass-card p-4 rounded-xl border border-gray-700/50">
            <h4 class="font-bold uppercase text-[11px] text-muted mb-3">📈 Topic Proficiency in This Set</h4>
            ${topicProficiency.length === 0
                ? '<p class="text-muted text-[11px]">Confirm topics on this card to track per-topic proficiency.</p>'
                : `<div class="space-y-2.5">
                    ${topicProficiency.map(tp => {
                        const acc = Math.round(Number(tp.accuracy || 0));
                        const barColor = acc >= 75 ? 'bg-success-500' : acc >= 50 ? 'bg-warning-500' : 'bg-danger-500';
                        return `
                            <div>
                                <div class="flex justify-between text-[11px] mb-1">
                                    <span class="font-medium truncate pr-2">${window.escapeHTML(tp.topic)}</span>
                                    <span class="font-mono text-muted shrink-0 capitalize">${window.escapeHTML(tp.level || 'new')} (${acc}%)</span>
                                </div>
                                <div class="w-full glass-panel rounded-full h-1.5 overflow-hidden">
                                    <div class="${barColor} h-1.5 rounded-full" style="width: ${Math.max(acc, 4)}%"></div>
                                </div>
                            </div>
                        `;
                    }).join('')}
                </div>`
            }
        </div>
    `;

    html += `</div>`;
    window.openInspector(`Hub Card: ${title}`, html, forceOpen);

    // Wire Inspector interactive buttons
    document.getElementById('btn-retry-session-llm')?.addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        btn.innerHTML = `<span class="animate-spin inline-block">⏳</span> Retrying...`;
        try {
            const res = await fetch(`/api/sessions/${encodeURIComponent(session.id)}/retry`, { method: 'POST' });
            if (res.ok) {
                if (window.showToast) window.showToast("LLM processing restarted!");
                session.llm_status = 'processing';
                renderHub();
                setTimeout(() => openHubCardInspector(session, false), 500);
            } else {
                if (window.showToast) window.showToast("Failed to trigger retry.");
                btn.disabled = false;
            }
        } catch (err) {
            console.error("Retry request failed:", err);
            btn.disabled = false;
        }
    });

    document.getElementById('insp-review-topics-btn')?.addEventListener('click', () => {
        reopenTopicVerificationForSession(session);
    });

    document.getElementById('insp-prioritize-btn')?.addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        btn.textContent = 'Prioritizing...';
        try {
            const res = await fetch('/api/queue/prioritize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: session.id })
            });
            if (res.ok) {
                btn.textContent = '✓ Moved to Top of Queue (#1)';
                setTimeout(() => openHubCardInspector(session), 800);
            }
        } catch (err) {
            console.error("Failed to prioritize session:", err);
            btn.disabled = false;
            btn.textContent = '🚀 Raise Priority (Move to #1)';
        }
    });

    document.querySelectorAll('.insp-create-set-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const topic = btn.dataset.topic;
            if (topic && typeof window.startCustomTopicQuiz === 'function') {
                window.startCustomTopicQuiz(topic);
            }
        });
    });
}

function updateDeleteBtn() {
    const deleteBtn = document.getElementById('hub-delete-btn');
    const deleteCount = document.getElementById('hub-delete-count');
    if (deleteBtn && deleteCount) {
        deleteCount.textContent = hubSelected.size;
        deleteBtn.disabled = hubSelected.size === 0;
    }
}

async function deleteSelectedSessions() {
    if (hubSelected.size === 0) return;
    if (confirm(`Are you sure you want to delete ${hubSelected.size} session(s)?`)) {
        try {
            await fetch('/api/sessions/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_ids: Array.from(hubSelected) })
            });
            hubSelected.clear();
            isEditing = false;

            const editBtn = document.getElementById('hub-edit-btn');
            const deleteBtn = document.getElementById('hub-delete-btn');
            if (editBtn) editBtn.classList.remove('bg-brand-600', 'text-white');
            if (deleteBtn) deleteBtn.style.display = 'none';

            loadSessions();
        } catch (e) {
            console.error(e);
            alert("Failed to delete sessions");
        }
    }
}

// =========================================================
// Retake / Start Session From Hub Card
// =========================================================

async function retakeSession(session) {
    try {
        const res = await fetch(`/api/sessions/${encodeURIComponent(session.id)}/questions`);
        if (res.ok) {
            const sessionQuestions = await res.json();
            const mappedQuestions = sessionQuestions.map(sq => ({
                id: sq.question_id,
                topic_name: sq.topic,
                question: sq.question_text,
                option_a: sq.opt_a,
                option_b: sq.opt_b,
                option_c: sq.opt_c,
                option_d: sq.opt_d,
                correct_answer: sq.correct_answer,
                explanation: sq.explanation,
                llm_explanation: sq.llm_explanation
            }));

            let parsedTopicList = session.topic_list || [];
            if (typeof parsedTopicList === 'string') {
                try { parsedTopicList = JSON.parse(parsedTopicList); } catch (_) { parsedTopicList = []; }
            }

            if (typeof window.launchPreparedQuiz === 'function') {
                window.launchPreparedQuiz({
                    sessionId: session.id,
                    customTitle: session.custom_title || session.subject_name,
                    topicsReviewed: Boolean(session.topics_reviewed),
                    topicList: parsedTopicList,
                    config: {
                        subjectName: session.subject_name || 'Mixed',
                        timeLimitMinutes: 0,
                        mode: 'learn'
                    },
                    questions: mappedQuestions
                });
            }
        } else {
            alert("Failed to load questions from this session.");
        }
    } catch (e) {
        console.error("Error preparing retake session:", e);
    }
}

// =========================================================
// Session Review (Dual CSV + LLM Explanations & Inspector Links)
// =========================================================

async function openSessionReview(sessionId) {
    document.getElementById('hub-main-container').style.display = 'none';
    document.getElementById('hub-review-container').style.display = 'flex';
    document.getElementById('hub-carousel-container').style.display = 'none';

    const questionsContainer = document.getElementById('hub-review-questions');
    questionsContainer.innerHTML = '<div class="text-center py-8 text-muted">Loading session questions &amp; explanations...</div>';

    try {
        const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/questions`);
        if (res.ok) {
            const questions = await res.json();
            renderSessionReview(questions);
        } else {
            questionsContainer.innerHTML = '<div class="text-center py-8 text-danger-500">Failed to load questions.</div>';
        }
    } catch (e) {
        console.error(e);
        questionsContainer.innerHTML = '<div class="text-center py-8 text-danger-500">Error loading questions.</div>';
    }
}

function renderSessionReview(questions) {
    const container = document.getElementById('hub-review-questions');
    container.innerHTML = '';
    const template = document.getElementById('review-card-template');
    const optionTemplate = document.getElementById('review-option-template');
    const llmEnabled = window.AppState ? window.AppState.get().llmEnabled !== false : true;

    questions.forEach((q, index) => {
        const clone = template.content.cloneNode(true);
        const card = clone.querySelector('.review-card');

        const userAns = q.selected_option;
        const correctAns = (q.correct_answer || 'A').toUpperCase();
        const isCorrect = q.is_correct === 1;

        if (!userAns) {
            card.classList.add('border-gray-700/50');
            card.querySelector('.review-icon').innerHTML = '<span class="text-muted font-bold text-lg">○</span>';
        } else if (isCorrect) {
            card.classList.add('border-success-500/30');
            card.querySelector('.review-icon').innerHTML = '<span class="text-success-500 font-bold text-xl">✓</span>';
        } else {
            card.classList.add('border-danger-500/30');
            card.querySelector('.review-icon').innerHTML = '<span class="text-danger-500 font-bold text-xl">✗</span>';
        }

        card.querySelector('.review-q-num').textContent = `Question ${index + 1}`;
        const topicEl = card.querySelector('.review-q-topic');
        topicEl.textContent = q.topic || 'General';
        topicEl.classList.add('cursor-pointer', 'hover:text-brand-400');
        topicEl.title = 'Click to open references in Right Sidebar';
        topicEl.addEventListener('click', () => {
            showQuestionReferencesInInspector(index + 1, q);
        });

        card.querySelector('.review-q-text').textContent = q.question_text;

        const optsContainer = card.querySelector('.review-options');
        ['A', 'B', 'C', 'D'].forEach(opt => {
            const text = q[`opt_${opt.toLowerCase()}`];
            if (!text) return;

            const optClone = optionTemplate.content.cloneNode(true);
            const optDiv = optClone.querySelector('.review-option');
            optDiv.querySelector('.review-option-letter').textContent = opt;
            optDiv.querySelector('.review-option-text').textContent = text;

            if (opt === correctAns) {
                optDiv.classList.add('bg-success-500/20', 'border-success-500/50', 'text-success-300');
            } else if (userAns === opt) {
                optDiv.classList.add('bg-danger-500/20', 'border-danger-500/50', 'text-danger-300');
            }
            optsContainer.appendChild(optDiv);
        });

        // Dual Explanations: CSV Baseline Explanation + LM Studio Detailed MCQ Breakdown
        const expContainer = card.querySelector('.review-explanation-container');
        const expContent = card.querySelector('.review-explanation');
        const hasCsvExp = Boolean(q.explanation && q.explanation !== 'No explanation provided.');
        const hasLlmExp = Boolean(llmEnabled && q.llm_explanation && q.llm_explanation.trim());

        if (hasCsvExp || hasLlmExp) {
            expContainer.classList.remove('hidden');
            let combinedExpHtml = '';

            if (hasCsvExp) {
                combinedExpHtml += `
                    <div class="mb-3">
                        <p class="text-xs font-bold text-brand-300 uppercase tracking-wider mb-1">📄 CSV Baseline Explanation</p>
                        <div>${window.renderMarkdownAndMath(q.explanation)}</div>
                    </div>
                `;
            }

            if (hasLlmExp) {
                combinedExpHtml += `
                    <div class="${hasCsvExp ? 'pt-3 border-t border-gray-700/50' : ''}">
                        <p class="text-xs font-bold text-brand-400 uppercase tracking-wider mb-1">🤖 LM Studio Detailed MCQ Breakdown</p>
                        <div>${window.renderMarkdownAndMath(q.llm_explanation)}</div>
                    </div>
                `;
            }

            expContent.innerHTML = combinedExpHtml;
        }

        if (q.reference_links) {
            const refsContainer = card.querySelector('.review-references-container');
            const refsTarget = card.querySelector('.review-references');
            const added = appendReferences(refsTarget, q.reference_links, q.topic);
            if (added > 0) refsContainer.classList.remove('hidden');
        }

        container.appendChild(card);
    });
}

function showQuestionReferencesInInspector(qNum, qObj) {
    let refsHtml = '';
    if (qObj.reference_links) {
        const tempDiv = document.createElement('div');
        tempDiv.className = 'flex flex-col gap-2';
        appendReferences(tempDiv, qObj.reference_links, qObj.topic);
        refsHtml = tempDiv.innerHTML;
    }
    window.openInspector(
        `Question ${qNum} References`,
        `<div class="space-y-4 text-xs">
            <div class="glass-panel p-3 rounded-xl">
                <p class="text-[10px] text-muted uppercase">Topic</p>
                <p class="font-bold text-sm text-brand-400">${window.escapeHTML(qObj.topic || 'General')}</p>
            </div>
            <div>
                <h4 class="font-bold text-xs uppercase text-muted mb-2">Verified Reference Links</h4>
                <div class="flex flex-wrap gap-2">${refsHtml || '<p class="text-muted">No reference links cached for this question yet.</p>'}</div>
            </div>
        </div>`
    );
}

function appendReferences(container, refsStr, highlightTopic) {
    if (!refsStr) return 0;
    try {
        const refs = typeof refsStr === 'string' ? JSON.parse(refsStr) : refsStr;
        const allRefs = [];
        if (refs.pdf) allRefs.push(...refs.pdf.map(r => ({ ...r, type: 'pdf' })));
        if (refs.obsidian) allRefs.push(...refs.obsidian.map(r => ({ ...r, type: 'obsidian' })));
        if (refs.zim) allRefs.push(...refs.zim.map(r => ({ ...r, type: 'zim' })));

        if (allRefs.length === 0) return 0;

        const lowerHighlight = highlightTopic ? highlightTopic.toLowerCase() : '';

        allRefs.forEach(ref => {
            const a = document.createElement('a');
            a.href = "#";

            const isHighlight = lowerHighlight && ref.title && ref.title.toLowerCase().includes(lowerHighlight);
            if (isHighlight) {
                a.style.boxShadow = '0 0 0 1px var(--accent-color)';
            }

            if (ref.type === 'pdf') {
                a.className = "citation-pdf";
                a.dataset.filepath = ref.file_path || '';
                a.dataset.page = ref.page || 1;
                a.textContent = `📕 ${ref.title || 'PDF'} (Pg ${ref.page || 1})`;
            } else if (ref.type === 'obsidian') {
                a.className = "citation-obsidian";
                a.dataset.filepath = ref.file_path || '';
                a.dataset.file = ref.title || '';
                a.textContent = `🟣 ${ref.title || 'Obsidian Note'}`;
            } else if (ref.type === 'zim') {
                a.className = "citation-zim";
                // Always pass the article title/ID (not the .zim file path) so ZIM HTML lookup succeeds
                a.dataset.articleId = ref.article_id || ref.title || '';
                a.textContent = `🌐 ${ref.title || 'ZIM Article'}`;
            }
            container.appendChild(a);
        });

        return allRefs.length;
    } catch (e) {
        return 0;
    }
}

// =========================================================
// Topic Carousel Sub-View (Study Topics Covered)
// =========================================================

let carouselOriginMode = 'hub';

async function openTopicCarousel(sessionId) {
    carouselOriginMode = 'hub';
    const hubHeader = document.querySelector('#knowledge-hub-view .view-header');
    if (hubHeader) hubHeader.style.display = 'none';

    document.getElementById('hub-main-container').style.display = 'none';
    document.getElementById('hub-review-container').style.display = 'none';
    document.getElementById('hub-carousel-container').style.display = 'flex';

    const backLabel = document.getElementById('hub-carousel-back-label');
    if (backLabel) backLabel.textContent = 'Back to Hub';

    currentTopicIndex = 0;
    currentSessionTopics = [];

    const contentEl = document.getElementById('hub-carousel-content');
    contentEl.innerHTML = '<div class="text-center py-12 animate-pulse text-muted">Loading synthesized topic notes...</div>';

    await fetchAndRenderTopics(sessionId);

    if (carouselInterval) clearInterval(carouselInterval);

    const hasPending = currentSessionTopics.some(t => t.llm_status === 'pending');
    if (hasPending) {
        carouselInterval = setInterval(async () => {
            await fetchAndRenderTopics(sessionId, true);
            const stillPending = currentSessionTopics.some(t => t.llm_status === 'pending');
            if (!stillPending) {
                clearInterval(carouselInterval);
                carouselInterval = null;
            }
        }, 3000);
    }
}

window.openTopicCarouselForQuiz = async function(topicsArray, initialTopic) {
    carouselOriginMode = 'quiz';
    const hubHeader = document.querySelector('#knowledge-hub-view .view-header');
    if (hubHeader) hubHeader.style.display = 'none';

    document.getElementById('hub-carousel-container').style.display = 'flex';

    const backLabel = document.getElementById('hub-carousel-back-label');
    if (backLabel) backLabel.textContent = 'Back to Quiz';

    currentTopicIndex = 0;
    currentSessionTopics = [];

    const contentEl = document.getElementById('hub-carousel-content');
    contentEl.innerHTML = '<div class="text-center py-12 animate-pulse text-muted">Loading synthesized topic notes...</div>';

    if (carouselInterval) {
        clearInterval(carouselInterval);
        carouselInterval = null;
    }

    const topicsDataList = await Promise.all((topicsArray || []).map(async (topicName) => {
        try {
            const res = await fetch(`/api/mcq/synthesis?topic=${encodeURIComponent(topicName)}`);
            if (res.ok) {
                const data = await res.json();
                return {
                    topic_name: topicName,
                    display_title: data.display_title || topicName,
                    unified_article: data.unified_article || null,
                    enhanced_explanation: data.enhanced_explanation || null,
                    top_references: data.references ? JSON.stringify(data.references) : null,
                    llm_status: data.unified_article ? 'completed' : 'none'
                };
            }
        } catch (e) {
            console.warn("Failed to fetch topic synthesis for quiz carousel:", e);
        }
        return {
            topic_name: topicName,
            display_title: topicName,
            unified_article: null,
            enhanced_explanation: null,
            top_references: null,
            llm_status: 'none'
        };
    }));

    currentSessionTopics = topicsDataList;
    window.currentStudyTopics = topicsDataList.map(t => t.display_title || t.topic_name);

    if (initialTopic) {
        const lower = initialTopic.toLowerCase();
        const idx = topicsDataList.findIndex(t =>
            (t.topic_name && t.topic_name.toLowerCase() === lower) ||
            (t.display_title && t.display_title.toLowerCase() === lower)
        );
        if (idx !== -1) currentTopicIndex = idx;
    }

    renderCurrentTopic();
};

window.selectTopicInCarousel = function(topicName) {
    if (!currentSessionTopics || currentSessionTopics.length === 0) return false;
    const lower = topicName.toLowerCase();
    const idx = currentSessionTopics.findIndex(t =>
        (t.topic_name && t.topic_name.toLowerCase() === lower) ||
        (t.display_title && t.display_title.toLowerCase() === lower)
    );
    if (idx !== -1) {
        currentTopicIndex = idx;
        renderCurrentTopic();
        return true;
    }
    return false;
};

async function fetchAndRenderTopics(sessionId, silent = false) {
    try {
        const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/topics`);
        if (res.ok) {
            currentSessionTopics = await res.json();
            if (currentSessionTopics.length > 0) {
                window.currentStudyTopics = currentSessionTopics.map(t => t.display_title || t.topic_name);
                renderCurrentTopic();
            } else if (!silent) {
                document.getElementById('hub-carousel-content').textContent = 'No verified topics found for this session.';
            }
        }
    } catch (e) {
        console.error("Failed to fetch session topics:", e);
    }
}

function renderCurrentTopic() {
    if (currentTopicIndex >= currentSessionTopics.length) currentTopicIndex = 0;
    if (currentTopicIndex < 0) currentTopicIndex = 0;

    const topic = currentSessionTopics[currentTopicIndex];
    if (!topic) return;

    const llmEnabled = window.AppState ? window.AppState.get().llmEnabled !== false : true;

    document.getElementById('hub-carousel-counter').textContent = `Topic ${currentTopicIndex + 1} of ${currentSessionTopics.length}`;
    document.getElementById('hub-carousel-title').textContent = topic.display_title || topic.topic_name;

    const statusEl = document.getElementById('hub-carousel-status');
    if (!llmEnabled) {
        statusEl.className = "inline-block mt-2 px-3 py-1 glass-panel text-muted text-xs rounded-full";
        statusEl.textContent = "Reference-Only Mode (LLM Disabled)";
    } else if (topic.llm_status === 'pending') {
        statusEl.className = "inline-block mt-2 px-3 py-1 bg-warning/20 text-warning text-xs rounded-full border border-warning/20 animate-pulse";
        statusEl.textContent = "LM Studio Generating Clinical Note...";
    } else {
        statusEl.className = "inline-block mt-2 px-3 py-1 bg-brand-600/20 text-brand-400 text-xs rounded-full border border-brand-500/20";
        statusEl.textContent = "Clinical Synthesis & Verified References";
    }

    const contentEl = document.getElementById('hub-carousel-content');
    contentEl.innerHTML = '';

    if (topic.unified_article && llmEnabled) {
        let parsedArticle = null;
        try {
            parsedArticle = JSON.parse(topic.unified_article);
        } catch (_) {
            parsedArticle = null;
        }

        if (parsedArticle && typeof parsedArticle === 'object') {
            const h2 = document.createElement('h2');
            h2.className = "text-xl font-bold mb-3";
            h2.textContent = parsedArticle.subtopic || topic.display_title || topic.topic_name;
            contentEl.appendChild(h2);

            if (parsedArticle.section) {
                const pSec = document.createElement('p');
                pSec.innerHTML = `<strong>Section:</strong> ${window.escapeHTML(parsedArticle.section)}`;
                contentEl.appendChild(pSec);
            }
            if (parsedArticle.conditional_context) {
                const pCtx = document.createElement('p');
                pCtx.innerHTML = `<strong>Context:</strong> ${window.escapeHTML(parsedArticle.conditional_context)}`;
                contentEl.appendChild(pCtx);
            }

            const ul = document.createElement('ul');
            ul.className = "list-disc pl-5 mt-4 space-y-2";
            (parsedArticle.clinical_points || []).forEach(pt => {
                const li = document.createElement('li');
                li.textContent = (pt.point || '') + ' ';
                if (pt.source) {
                    const badge = document.createElement('span');
                    badge.className = "text-xs glass-panel text-brand-300 px-2 py-0.5 rounded-full ml-2 font-mono";
                    badge.title = `Source: ${pt.source} (Page ${pt.page || 1})`;
                    badge.textContent = `[Ref: ${pt.source}${pt.page ? ` p.${pt.page}` : ''}]`;
                    li.appendChild(badge);
                }
                ul.appendChild(li);
            });
            contentEl.appendChild(ul);

            if (parsedArticle.discrepancies) {
                const disc = document.createElement('div');
                disc.className = "mt-6 bg-danger-500/10 border border-danger-500/30 p-4 rounded-lg";
                disc.innerHTML = `<strong class="text-danger-400 block mb-1">⚠️ Clinical Discrepancies &amp; Staging Variances:</strong>
                                  <div class="whitespace-pre-wrap text-xs">${window.escapeHTML(parsedArticle.discrepancies)}</div>`;
                contentEl.appendChild(disc);
            }
        } else {
            window.renderMarkdownAndMath(topic.unified_article, contentEl);
        }
    } else if (topic.enhanced_explanation) {
        window.renderMarkdownAndMath(topic.enhanced_explanation, contentEl);
    } else if (topic.llm_status === 'pending' && llmEnabled) {
        contentEl.innerHTML = `
            <div class="text-muted italic animate-pulse flex items-center gap-2 py-6">
                <span class="w-4 h-4 rounded-full border-2 border-brand-500 border-t-transparent animate-spin inline-block"></span>
                LM Studio is synthesizing clinical notes from your PDF, Obsidian, and ZIM references...
            </div>
        `;
    } else {
        contentEl.innerHTML = `<p class="text-muted text-sm">Select any verified reference link below to read the source article in detail.</p>`;
    }

    const refsContainer = document.getElementById('hub-carousel-references');
    const refList = document.getElementById('hub-carousel-ref-list');
    refList.innerHTML = '';

    if (topic.top_references) {
        const added = appendReferences(refList, topic.top_references, topic.topic_name);
        refsContainer.style.display = added > 0 ? 'block' : 'none';
    } else {
        refsContainer.style.display = 'none';
    }

    document.getElementById('hub-carousel-prev').disabled = currentTopicIndex === 0;
    document.getElementById('hub-carousel-next').disabled = currentTopicIndex === currentSessionTopics.length - 1;
}

// =========================================================
// Live WebSocket Listener for 1-2-3 Status Circle Transitions
// =========================================================

function initHubWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    const wsUrl = `${protocol}${window.location.host}/ws/tasks`;

    try {
        hubSocket = new WebSocket(wsUrl);
        hubSocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.event === 'SESSION_STATUS_UPDATE' && data.session_id) {
                    const target = hubSessions.find(s => s.id === data.session_id);
                    if (target) {
                        target.topics_reviewed = data.topics_reviewed ? 1 : 0;
                        target.refs_status = data.refs_status || target.refs_status;
                        target.llm_status = data.llm_status || target.llm_status;
                        renderHub();
                        if (activeInspectorSessionId === data.session_id) {
                            openHubCardInspector(target, false);
                        }
                    } else {
                        loadSessions(true);
                    }
                } else if (data.event === 'TASK_UPDATE' && data.status === 'COMPLETED') {
                    loadSessions(true);
                }
            } catch (_) {}
        };
        hubSocket.onclose = () => {
            setTimeout(initHubWebSocket, 6000);
        };
    } catch (_) {}
}

document.addEventListener('DOMContentLoaded', () => {
    loadSessions();
    initHubWebSocket();

    const editBtn = document.getElementById('hub-edit-btn');
    const deleteBtn = document.getElementById('hub-delete-btn');

    if (editBtn) {
        editBtn.addEventListener('click', () => {
            isEditing = !isEditing;
            hubSelected.clear();
            if (isEditing) {
                editBtn.classList.add('bg-brand-600', 'text-white');
                if (deleteBtn) deleteBtn.style.display = 'flex';
            } else {
                editBtn.classList.remove('bg-brand-600', 'text-white');
                if (deleteBtn) deleteBtn.style.display = 'none';
            }
            updateDeleteBtn();
            renderHub();
        });
    }

    if (deleteBtn) {
        deleteBtn.addEventListener('click', deleteSelectedSessions);
    }

    document.getElementById('hub-review-back-btn')?.addEventListener('click', () => {
        document.getElementById('hub-review-container').style.display = 'none';
        document.getElementById('hub-main-container').style.display = 'block';
    });

    document.getElementById('hub-carousel-back-btn')?.addEventListener('click', () => {
        document.getElementById('hub-carousel-container').style.display = 'none';
        if (carouselInterval) {
            clearInterval(carouselInterval);
            carouselInterval = null;
        }
        if (carouselOriginMode === 'quiz') {
            const quizView = document.getElementById('quiz-view');
            if (quizView) quizView.style.display = '';
            const quizNav = document.querySelector('.nav-btn[data-target="quiz-view"]');
            if (quizNav) quizNav.click();
        } else {
            const hubHeader = document.querySelector('#knowledge-hub-view .view-header');
            if (hubHeader) hubHeader.style.display = 'flex';
            const mainContainer = document.getElementById('hub-main-container');
            if (mainContainer) mainContainer.style.display = 'block';
        }
    });

    document.getElementById('hub-carousel-toggle-sidebar-btn')?.addEventListener('click', () => {
        const sidebar = document.getElementById('right-sidebar');
        const isCollapsed = !sidebar || sidebar.classList.contains('collapsed');
        if (isCollapsed) {
            if (typeof window.renderStudyTopicsInspector === 'function') {
                window.renderStudyTopicsInspector(window.currentStudyTopics, true);
            }
        } else {
            if (typeof window.closeInspector === 'function') {
                window.closeInspector();
            }
        }
    });

    document.getElementById('hub-carousel-prev')?.addEventListener('click', () => {
        if (currentTopicIndex > 0) {
            currentTopicIndex--;
            renderCurrentTopic();
        }
    });

    document.getElementById('hub-carousel-next')?.addEventListener('click', () => {
        if (currentTopicIndex < currentSessionTopics.length - 1) {
            currentTopicIndex++;
            renderCurrentTopic();
        }
    });

    document.addEventListener('keydown', (e) => {
        const carousel = document.getElementById('hub-carousel-container');
        if (carousel && carousel.style.display === 'flex') {
            if (e.key === 'ArrowLeft' && currentTopicIndex > 0) {
                currentTopicIndex--;
                renderCurrentTopic();
            } else if (e.key === 'ArrowRight' && currentTopicIndex < currentSessionTopics.length - 1) {
                currentTopicIndex++;
                renderCurrentTopic();
            }
        }
    });
});
