let hubSessions = [];
let hubSelected = new Set();
let isEditing = false;
let currentSessionTopics = [];
let carouselInterval = null;
let currentTopicIndex = 0;

async function loadSessions() {
    const loadingEl = document.getElementById('hub-loading');
    const emptyEl = document.getElementById('hub-empty');
    const gridEl = document.getElementById('hub-grid');

    if (loadingEl) loadingEl.style.display = 'block';
    if (emptyEl) emptyEl.style.display = 'none';
    if (gridEl) gridEl.style.display = 'none';

    try {
        const res = await fetch('/api/sessions/history');
        if (res.ok) {
            hubSessions = await res.json();
            renderHub();
        }
    } catch(e) {
        console.error("Failed to load sessions", e);
    }
}

function renderHub() {
    const loadingEl = document.getElementById('hub-loading');
    const emptyEl = document.getElementById('hub-empty');
    const gridEl = document.getElementById('hub-grid');

    if (loadingEl) loadingEl.style.display = 'none';

    if(hubSessions.length === 0) {
        if (emptyEl) emptyEl.style.display = 'block';
        if (gridEl) gridEl.style.display = 'none';
        return;
    }

    if (emptyEl) emptyEl.style.display = 'none';
    if (gridEl) {
        gridEl.style.display = 'grid';
        gridEl.innerHTML = '';
    }

    hubSessions.forEach(session => {
        const div = document.createElement('div');
        div.className = `bg-gray-800/60 rounded-xl border ${hubSelected.has(session.id) ? 'border-brand-500 ring-1 ring-brand-500' : 'border-gray-700 hover:border-gray-600'} transition-all flex flex-col h-full overflow-hidden shadow-lg relative cursor-default`;

        let selectHtml = '';
        if (isEditing) {
            const isChecked = hubSelected.has(session.id) ? 'checked' : '';
            selectHtml = `
            <div class="absolute top-3 right-3 z-10">
                <input type="checkbox" class="hub-select-cb w-5 h-5 rounded border-gray-600 bg-gray-900 text-brand-500 focus:ring-brand-500 cursor-pointer" data-id="${session.id}" ${isChecked}>
            </div>`;
        }

        const dateStr = new Date(session.timestamp).toLocaleDateString();
        const shortId = session.id.split('_').pop().substring(0,6);

        const topics = session.topics || [];
        const topicsHtml = topics.slice(0, 4).map(t => `<span class="text-[10px] px-2 py-0.5 bg-gray-700 text-gray-400 rounded-full h-[18px] truncate max-w-[100px] inline-block">${t}</span>`).join(' ');

        const accClass = session.accuracy >= 70 ? 'text-success-500' : session.accuracy >= 50 ? 'text-warning-500' : 'text-danger-500';

        div.innerHTML = `
            ${selectHtml}
            <div class="p-5 flex-1 hub-card-body">
                <div class="flex justify-between items-start mb-3 pr-8">
                  <span class="text-xs font-medium px-2.5 py-1 bg-brand-600/20 text-brand-400 rounded-lg truncate max-w-[150px]">
                    ${session.subject_name || 'Mixed Session'}
                  </span>
                  <div class="text-xs text-gray-500 flex items-center gap-1 shrink-0">
                    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                    ${dateStr}
                  </div>
                </div>

                <h3 class="text-lg font-semibold text-gray-200 mb-2">Session ${shortId}</h3>

                <div class="flex flex-wrap gap-1 mb-4 h-[44px] overflow-hidden relative">
                    ${topicsHtml}
                    ${topics.length > 4 ? `<span class="text-[10px] px-2 py-0.5 bg-gray-700 text-gray-400 rounded-full h-[18px]">+${topics.length - 4}</span>` : ''}
                </div>

                <div class="grid grid-cols-2 gap-4 bg-gray-900/50 rounded-lg p-3 border border-gray-700/50 mb-2">
                  <div class="text-center">
                    <p class="text-xs text-gray-500 mb-1">Score</p>
                    <p class="text-lg font-bold text-white">${session.score} / ${session.max_score}</p>
                  </div>
                  <div class="text-center">
                    <p class="text-xs text-gray-500 mb-1">Accuracy</p>
                    <p class="text-lg font-bold ${accClass}">${Math.round(session.accuracy)}%</p>
                  </div>
                </div>

                <div class="flex justify-between text-xs text-gray-400 px-1">
                  <span><span class="text-success-400">${session.correct_answers}</span> Correct</span>
                  <span><span class="text-danger-400">${session.wrong_answers}</span> Wrong</span>
                </div>
            </div>

            <div class="border-t border-gray-700 p-3 bg-gray-900/30 grid grid-cols-2 gap-2">
                <button class="btn-study py-2 px-3 text-xs font-medium bg-brand-600/20 text-brand-400 hover:bg-brand-600/30 hover:text-brand-300 rounded-lg transition-colors flex items-center justify-center gap-1.5 col-span-2" data-id="${session.id}">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg> Study Topics Covered
                </button>
                <button class="btn-review py-2 px-3 text-xs font-medium bg-gray-700 text-gray-300 hover:bg-gray-600 rounded-lg transition-colors flex items-center justify-center gap-1.5" data-id="${session.id}">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg> Explanations
                </button>
                <button class="btn-retake py-2 px-3 text-xs font-medium bg-gray-700 text-gray-300 hover:bg-gray-600 rounded-lg transition-colors flex items-center justify-center gap-1.5" data-id="${session.id}">
                  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"></polyline><polyline points="23 20 23 14 17 14"></polyline><path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"></path></svg> Retake Set
                </button>
            </div>
        `;

        if(isEditing) {
            const cb = div.querySelector('.hub-select-cb');
            if (cb) {
                cb.addEventListener('change', (e) => {
                    if(e.target.checked) {
                        hubSelected.add(session.id);
                        div.classList.add('border-brand-500', 'ring-1', 'ring-brand-500');
                        div.classList.remove('border-gray-700', 'hover:border-gray-600');
                    } else {
                        hubSelected.delete(session.id);
                        div.classList.remove('border-brand-500', 'ring-1', 'ring-brand-500');
                        div.classList.add('border-gray-700', 'hover:border-gray-600');
                    }
                    updateDeleteBtn();
                });
            }
            const body = div.querySelector('.hub-card-body');
            if (body) {
                body.addEventListener('click', (e) => {
                    if (cb) {
                        cb.checked = !cb.checked;
                        cb.dispatchEvent(new Event('change'));
                    }
                });
            }
        }

        const reviewBtn = div.querySelector('.btn-review');
        if(reviewBtn) {
            reviewBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                openSessionReview(session.id);
            });
        }

        const studyBtn = div.querySelector('.btn-study');
        if (studyBtn) {
            studyBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                openTopicCarousel(session.id);
            });
        }

        const retakeBtn = div.querySelector('.btn-retake');
        if (retakeBtn) {
            retakeBtn.addEventListener('click', (e) => {
                 e.stopPropagation();
                 // Call global state/quiz redirect logic if we implement retake, otherwise alert for now
                 alert("Retake feature is integrated via Quiz Engine.");
            });
        }

        gridEl.appendChild(div);
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
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ session_ids: Array.from(hubSelected) })
            });
            hubSelected.clear();
            isEditing = false;

            const editBtn = document.getElementById('hub-edit-btn');
            const deleteBtn = document.getElementById('hub-delete-btn');
            if (editBtn) editBtn.classList.remove('bg-brand-600', 'text-white');
            if (deleteBtn) deleteBtn.style.display = 'none';

            loadSessions();
        } catch(e) {
            console.error(e);
            alert("Failed to delete sessions");
        }
    }
}

// --- Session Review Logic ---
async function openSessionReview(sessionId) {
    document.getElementById('hub-main-container').style.display = 'none';
    document.getElementById('hub-review-container').style.display = 'flex';
    document.getElementById('hub-carousel-container').style.display = 'none';

    const questionsContainer = document.getElementById('hub-review-questions');
    questionsContainer.innerHTML = '<div class="text-center py-8 text-gray-400">Loading session details...</div>';

    try {
        const res = await fetch(`/api/sessions/${sessionId}/questions`);
        if (res.ok) {
            const questions = await res.json();
            renderSessionReview(questions);
        } else {
            questionsContainer.innerHTML = '<div class="text-center py-8 text-red-400">Failed to load questions.</div>';
        }
    } catch(e) {
        console.error(e);
        questionsContainer.innerHTML = '<div class="text-center py-8 text-red-400">Error loading questions.</div>';
    }
}

function renderSessionReview(questions) {
    const container = document.getElementById('hub-review-questions');
    container.innerHTML = '';

    questions.forEach((q, index) => {
        const isCorrect = q.is_correct === 1;
        const userAns = q.selected_option;
        const correctAns = q.correct_answer;

        const div = document.createElement('div');
        div.className = `bg-gray-800 rounded-xl p-6 border ${isCorrect ? 'border-success-500/30' : 'border-danger-500/30'}`;

        const iconHtml = isCorrect
            ? '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-success-500 mt-1"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>'
            : '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-danger-500 mt-1"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>';

        let optionsHtml = '';
        ['A', 'B', 'C', 'D'].forEach(opt => {
            const text = q[`opt_${opt.toLowerCase()}`];
            if (!text) return;
            const isUserOpt = userAns === opt;
            const isCorrectOpt = correctAns === opt;

            let bgClass = "bg-gray-900 border-gray-700 text-gray-400";
            if (isCorrectOpt) bgClass = "bg-success-500/20 border-success-500/50 text-success-300";
            else if (isUserOpt) bgClass = "bg-danger-500/20 border-danger-500/50 text-danger-300";

            optionsHtml += `
                <div class="p-3 rounded-lg border text-sm flex gap-3 ${bgClass}">
                  <span class="font-bold shrink-0">${opt}</span>
                  <span>${text}</span>
                </div>
            `;
        });

        let expHtml = '';
        if (q.explanation) {
            expHtml = `
            <div class="bg-brand-900/30 border border-brand-500/30 p-4 rounded-lg mt-4 text-sm text-gray-300">
                <p class="font-semibold text-brand-400 mb-1">Explanation:</p>
                <div>${q.explanation}</div>
            </div>`;
        }

        let refsHtml = '';
        if (q.reference_links) {
             refsHtml = renderReferencesHTML(q.reference_links, q.topic);
        }

        div.innerHTML = `
            <div class="flex items-start gap-4">
                ${iconHtml}
                <div class="flex-1">
                  <div class="flex justify-between mb-2 text-sm text-gray-400">
                    <span>Question ${index + 1}</span>
                    <span class="bg-gray-700 px-2 py-0.5 rounded text-xs">${q.topic || 'General'}</span>
                  </div>
                  <p class="text-gray-200 mb-4">${q.question_text}</p>
                  <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
                      ${optionsHtml}
                  </div>
                  ${expHtml}
                  ${refsHtml}
                </div>
            </div>
        `;
        container.appendChild(div);
    });
}

function renderReferencesHTML(refsStr, highlightTopic) {
    if (!refsStr) return '';
    try {
        const refs = JSON.parse(refsStr);
        let allRefs = [];
        if (refs.pdf) allRefs.push(...refs.pdf.map(r => ({...r, type: 'pdf'})));
        if (refs.obsidian) allRefs.push(...refs.obsidian.map(r => ({...r, type: 'obsidian'})));
        if (refs.zim) allRefs.push(...refs.zim.map(r => ({...r, type: 'zim'})));

        if (allRefs.length === 0) return '';

        let refButtons = '';
        allRefs.forEach(ref => {
            let cls = '';
            let isHighlight = highlightTopic && ref.title && ref.title.toLowerCase().includes(highlightTopic.toLowerCase());
            let hlStyle = isHighlight ? 'box-shadow: 0 0 0 1px var(--accent-color);' : '';

            if (ref.type === 'pdf') {
                refButtons += `<a href="#" class="citation-pdf" style="${hlStyle}" data-filepath="${ref.file_path}" data-page="${ref.page || 1}">${ref.title || 'PDF'} (Pg ${ref.page || 1})</a>`;
            } else if (ref.type === 'obsidian') {
                refButtons += `<a href="#" class="citation-obsidian" style="${hlStyle}" data-vault="Primary" data-file="${ref.title}">${ref.title || 'Note'}</a>`;
            } else if (ref.type === 'zim') {
                refButtons += `<a href="#" class="citation-zim" style="${hlStyle}" data-article-id="${ref.article_id}">${ref.title || 'ZIM Article'}</a>`;
            }
        });

        return `
        <div class="mt-4 border-t border-gray-700/50 pt-4">
          <h4 class="text-sm font-semibold text-gray-300 mb-3 flex items-center gap-2">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-brand-400"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg> Related Knowledge
          </h4>
          <div class="flex flex-wrap gap-2">
             ${refButtons}
          </div>
        </div>
        `;
    } catch(e) {
        return '';
    }
}

// --- Topic Carousel Logic ---
async function openTopicCarousel(sessionId) {
    document.getElementById('hub-main-container').style.display = 'none';
    document.getElementById('hub-review-container').style.display = 'none';
    document.getElementById('hub-carousel-container').style.display = 'flex';

    currentTopicIndex = 0;
    currentSessionTopics = [];
    document.getElementById('hub-carousel-content').innerHTML = '<div class="text-center py-12 animate-pulse">Loading topics...</div>';

    await fetchAndRenderTopics(sessionId);

    if (carouselInterval) clearInterval(carouselInterval);

    // Check if any pending
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

async function fetchAndRenderTopics(sessionId, silent = false) {
    try {
        const res = await fetch(`/api/sessions/${sessionId}/topics`);
        if (res.ok) {
            currentSessionTopics = await res.json();
            if (currentSessionTopics.length > 0) {
                renderCurrentTopic();
            } else if (!silent) {
                document.getElementById('hub-carousel-content').innerHTML = 'No topics found for this session.';
            }
        }
    } catch(e) {
        console.error("Failed to fetch topics", e);
    }
}

function renderCurrentTopic() {
    if (currentTopicIndex >= currentSessionTopics.length) currentTopicIndex = 0;
    if (currentTopicIndex < 0) currentTopicIndex = 0;

    const topic = currentSessionTopics[currentTopicIndex];
    if (!topic) return;

    document.getElementById('hub-carousel-counter').textContent = `Topic ${currentTopicIndex + 1} of ${currentSessionTopics.length}`;
    document.getElementById('hub-carousel-title').textContent = topic.display_title || topic.topic_name;

    const statusEl = document.getElementById('hub-carousel-status');
    if (topic.llm_status === 'pending') {
        statusEl.className = "inline-block mt-2 px-3 py-1 bg-warning/20 text-warning text-xs rounded-full border border-warning/20";
        statusEl.textContent = "Processing...";
    } else {
        statusEl.className = "inline-block mt-2 px-3 py-1 bg-brand-600/20 text-brand-400 text-xs rounded-full border border-brand-500/20";
        statusEl.textContent = "Clinical Synthesis & Reference";
    }

    const contentEl = document.getElementById('hub-carousel-content');
    if (topic.llm_status === 'pending') {
        contentEl.innerHTML = `<div class="text-gray-400 italic animate-pulse flex items-center gap-2">
            <span class="w-4 h-4 rounded-full border-2 border-brand-500 border-t-transparent animate-spin inline-block"></span>
            LM Studio generating detailed explanation...
        </div>`;
    } else if (topic.unified_article) {
        contentEl.innerHTML = topic.unified_article;
    } else {
        contentEl.innerHTML = topic.enhanced_explanation || "No detailed notes available.";
    }

    const refsContainer = document.getElementById('hub-carousel-references');
    const refList = document.getElementById('hub-carousel-ref-list');
    if (topic.top_references) {
        refsContainer.style.display = 'block';

        const html = renderReferencesHTML(topic.top_references, topic.topic_name);
        const match = html.match(/<div class="flex flex-wrap gap-2">([\s\S]*?)<\/div>\s*<\/div>\s*$/);
        if (match) {
            refList.innerHTML = match[1];
        } else {
            refList.innerHTML = '';
        }

    } else {
        refsContainer.style.display = 'none';
    }

    document.getElementById('hub-carousel-prev').disabled = currentTopicIndex === 0;
    document.getElementById('hub-carousel-next').disabled = currentTopicIndex === currentSessionTopics.length - 1;
}

document.addEventListener('DOMContentLoaded', () => {
    loadSessions();

    const editBtn = document.getElementById('hub-edit-btn');
    const deleteBtn = document.getElementById('hub-delete-btn');

    if(editBtn) {
        editBtn.addEventListener('click', () => {
            isEditing = !isEditing;
            hubSelected.clear();
            if(isEditing) {
                editBtn.classList.add('bg-brand-600', 'text-white');
                deleteBtn.style.display = 'flex';
            } else {
                editBtn.classList.remove('bg-brand-600', 'text-white');
                deleteBtn.style.display = 'none';
            }
            updateDeleteBtn();
            renderHub();
        });
    }

    if(deleteBtn) {
        deleteBtn.addEventListener('click', deleteSelectedSessions);
    }

    // Back buttons
    const reviewBackBtn = document.getElementById('hub-review-back-btn');
    if (reviewBackBtn) {
        reviewBackBtn.addEventListener('click', () => {
            document.getElementById('hub-review-container').style.display = 'none';
            document.getElementById('hub-main-container').style.display = 'block';
        });
    }

    const carouselBackBtn = document.getElementById('hub-carousel-back-btn');
    if (carouselBackBtn) {
        carouselBackBtn.addEventListener('click', () => {
            document.getElementById('hub-carousel-container').style.display = 'none';
            document.getElementById('hub-main-container').style.display = 'block';
            if (carouselInterval) {
                clearInterval(carouselInterval);
                carouselInterval = null;
            }
        });
    }

    // Carousel navigation
    const prevBtn = document.getElementById('hub-carousel-prev');
    const nextBtn = document.getElementById('hub-carousel-next');

    if (prevBtn) {
        prevBtn.addEventListener('click', () => {
            if (currentTopicIndex > 0) {
                currentTopicIndex--;
                renderCurrentTopic();
            }
        });
    }

    if (nextBtn) {
        nextBtn.addEventListener('click', () => {
            if (currentTopicIndex < currentSessionTopics.length - 1) {
                currentTopicIndex++;
                renderCurrentTopic();
            }
        });
    }

    // Keyboard navigation for carousel
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
