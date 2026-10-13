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
        gridEl.innerHTML = ''; // Fast clearing of container
    }

    const template = document.getElementById('hub-card-template');

    hubSessions.forEach(session => {
        const clone = template.content.cloneNode(true);
        const cardNode = clone.querySelector('.hub-card');

        if (hubSelected.has(session.id)) {
            cardNode.classList.add('border-brand-500', 'ring-1', 'ring-brand-500');
            cardNode.classList.remove('border-gray-700', 'hover:border-gray-600');
        }

        const selectContainer = cardNode.querySelector('.hub-select-container');
        const cb = cardNode.querySelector('.hub-select-cb');
        if (isEditing) {
            selectContainer.classList.remove('hidden');
            cb.checked = hubSelected.has(session.id);
            cb.dataset.id = session.id;

            cb.addEventListener('change', (e) => {
                if (e.target.checked) {
                    hubSelected.add(session.id);
                    cardNode.classList.add('border-brand-500', 'ring-1', 'ring-brand-500');
                    cardNode.classList.remove('border-gray-700', 'hover:border-gray-600');
                } else {
                    hubSelected.delete(session.id);
                    cardNode.classList.remove('border-brand-500', 'ring-1', 'ring-brand-500');
                    cardNode.classList.add('border-gray-700', 'hover:border-gray-600');
                }
                updateDeleteBtn();
            });

            const body = cardNode.querySelector('.hub-card-body');
            body.addEventListener('click', () => {
                cb.checked = !cb.checked;
                cb.dispatchEvent(new Event('change'));
            });
        }

        cardNode.querySelector('.hub-card-subject').textContent = session.subject_name || 'Mixed Session';
        cardNode.querySelector('.hub-card-date span').textContent = new Date(session.timestamp).toLocaleDateString();
        cardNode.querySelector('.hub-card-title').textContent = `Session ${session.id.split('_').pop().substring(0,6)}`;

        cardNode.querySelector('.hub-card-score').textContent = `${session.score} / ${session.max_score}`;

        const accEl = cardNode.querySelector('.hub-card-accuracy');
        accEl.textContent = `${Math.round(session.accuracy)}%`;
        if (session.accuracy >= 70) accEl.classList.add('text-success-500');
        else if (session.accuracy >= 50) accEl.classList.add('text-warning-500');
        else accEl.classList.add('text-danger-500');

        cardNode.querySelector('.hub-card-correct').textContent = session.correct_answers;
        cardNode.querySelector('.hub-card-wrong').textContent = session.wrong_answers;

        const topicsContainer = cardNode.querySelector('.hub-card-topics');
        const topics = session.topics || [];
        topics.slice(0, 4).forEach(t => {
            const span = document.createElement('span');
            span.className = "text-[10px] px-2 py-0.5 bg-gray-700 text-gray-400 rounded-full h-[18px] truncate max-w-[100px] inline-block";
            span.textContent = t;
            topicsContainer.appendChild(span);
        });

        if (topics.length > 4) {
            const extra = document.createElement('span');
            extra.className = "text-[10px] px-2 py-0.5 bg-gray-700 text-gray-400 rounded-full h-[18px]";
            extra.textContent = `+${topics.length - 4}`;
            topicsContainer.appendChild(extra);
        }

        cardNode.querySelector('.btn-study').addEventListener('click', (e) => {
            e.stopPropagation();
            openTopicCarousel(session.id);
        });

        cardNode.querySelector('.btn-review').addEventListener('click', (e) => {
            e.stopPropagation();
            openSessionReview(session.id);
        });

        cardNode.querySelector('.btn-retake').addEventListener('click', (e) => {
            e.stopPropagation();
            retakeSession(session.id, session.subject_name);
        });

        gridEl.appendChild(cardNode);
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

// --- Retake Session Logic ---
async function retakeSession(sessionId, subjectName) {
    try {
        const res = await fetch(`/api/sessions/${sessionId}/questions`);
        if (res.ok) {
            const sessionQuestions = await res.json();

            // Map session questions back to the format expected by the Quiz Engine
            const mappedQuestions = sessionQuestions.map(sq => ({
                id: sq.question_id,
                topic_name: sq.topic,
                question: sq.question_text,
                option_a: sq.opt_a,
                option_b: sq.opt_b,
                option_c: sq.opt_c,
                option_d: sq.opt_d,
                correct_answer: sq.correct_answer,
                explanation: sq.explanation
            }));

            // Assign to the global pendingQuizData object used in quiz.js
            window.pendingQuizData = {
                config: {
                    subjectName: `${subjectName || 'Mixed'} (Retake)`,
                    timeLimitMinutes: 0,
                    mode: 'test'
                },
                questions: mappedQuestions
            };

            // Programmatically navigate to the Quiz Engine tab
            const navBtn = document.querySelector('.nav-btn[data-target="quiz-view"]');
            if (navBtn) navBtn.click();

            // Display the pre-quiz setup modal
            const preQuizModal = document.getElementById('pre-quiz-modal');
            if (preQuizModal) preQuizModal.style.display = 'flex';

        } else {
            alert("Failed to load questions from this session.");
        }
    } catch(e) {
        console.error("Error preparing retake session:", e);
        alert("Error connecting to server to prepare retake.");
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
    const template = document.getElementById('review-card-template');
    const optionTemplate = document.getElementById('review-option-template');

    questions.forEach((q, index) => {
        const clone = template.content.cloneNode(true);
        const card = clone.querySelector('.review-card');

        const isCorrect = q.is_correct === 1;
        const userAns = q.selected_option;
        const correctAns = q.correct_answer;

        if (isCorrect) {
            card.classList.add('border-success-500/30');
            card.querySelector('.review-icon').innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-success-500"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>';
        } else {
            card.classList.add('border-danger-500/30');
            card.querySelector('.review-icon').innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-danger-500"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>';
        }

        card.querySelector('.review-q-num').textContent = `Question ${index + 1}`;
        card.querySelector('.review-q-topic').textContent = q.topic || 'General';
        card.querySelector('.review-q-text').textContent = q.question_text;

        const optsContainer = card.querySelector('.review-options');
        ['A', 'B', 'C', 'D'].forEach(opt => {
            const text = q[`opt_${opt.toLowerCase()}`];
            if (!text) return;

            const optClone = optionTemplate.content.cloneNode(true);
            const optDiv = optClone.querySelector('.review-option');
            optDiv.querySelector('.review-option-letter').textContent = opt;
            optDiv.querySelector('.review-option-text').textContent = text;

            const isUserOpt = userAns === opt;
            const isCorrectOpt = correctAns === opt;

            if (isCorrectOpt) {
                optDiv.classList.replace('bg-gray-900', 'bg-success-500/20');
                optDiv.classList.replace('border-gray-700', 'border-success-500/50');
                optDiv.classList.replace('text-gray-400', 'text-success-300');
            } else if (isUserOpt) {
                optDiv.classList.replace('bg-gray-900', 'bg-danger-500/20');
                optDiv.classList.replace('border-gray-700', 'border-danger-500/50');
                optDiv.classList.replace('text-gray-400', 'text-danger-300');
            }
            optsContainer.appendChild(optDiv);
        });

        if (q.explanation) {
            const expContainer = card.querySelector('.review-explanation-container');
            const expContent = card.querySelector('.review-explanation');
            expContainer.classList.remove('hidden');

            // Safely parse markdown to HTML, then sanitize it to prevent XSS
            const htmlContent = window.marked && window.DOMPurify
                ? window.DOMPurify.sanitize(window.marked.parse(q.explanation))
                : window.DOMPurify ? window.DOMPurify.sanitize(q.explanation) : q.explanation;

            expContent.innerHTML = htmlContent;
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

function appendReferences(container, refsStr, highlightTopic) {
    if (!refsStr) return 0;
    try {
        const refs = JSON.parse(refsStr);
        let allRefs = [];
        if (refs.pdf) allRefs.push(...refs.pdf.map(r => ({...r, type: 'pdf'})));
        if (refs.obsidian) allRefs.push(...refs.obsidian.map(r => ({...r, type: 'obsidian'})));
        if (refs.zim) allRefs.push(...refs.zim.map(r => ({...r, type: 'zim'})));

        if (allRefs.length === 0) return 0;

        const lowerHighlight = highlightTopic ? highlightTopic.toLowerCase() : '';

        allRefs.forEach(ref => {
            const a = document.createElement('a');
            a.href = "#";

            let isHighlight = lowerHighlight && ref.title && ref.title.toLowerCase().includes(lowerHighlight);
            if (isHighlight) {
                a.style.boxShadow = '0 0 0 1px var(--accent-color)';
            }

            if (ref.type === 'pdf') {
                a.className = "citation-pdf";
                a.dataset.filepath = ref.file_path || '';
                a.dataset.page = ref.page || 1;
                a.textContent = `${ref.title || 'PDF'} (Pg ${ref.page || 1})`;
            } else if (ref.type === 'obsidian') {
                a.className = "citation-obsidian";
                a.dataset.vault = "Primary";
                a.dataset.file = ref.title || '';
                a.textContent = ref.title || 'Note';
            } else if (ref.type === 'zim') {
                a.className = "citation-zim";
                a.dataset.articleId = ref.article_id || ref.file_path || '';
                a.textContent = ref.title || 'ZIM Article';
            }
            container.appendChild(a);
        });

        return allRefs.length;
    } catch(e) {
        return 0;
    }
}

// --- Topic Carousel Logic ---
async function openTopicCarousel(sessionId) {
    document.getElementById('hub-main-container').style.display = 'none';
    document.getElementById('hub-review-container').style.display = 'none';
    document.getElementById('hub-carousel-container').style.display = 'flex';

    currentTopicIndex = 0;
    currentSessionTopics = [];

    const contentEl = document.getElementById('hub-carousel-content');
    contentEl.innerHTML = '';
    const loader = document.createElement('div');
    loader.className = "text-center py-12 animate-pulse text-gray-400";
    loader.textContent = "Loading topics...";
    contentEl.appendChild(loader);

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

async function fetchAndRenderTopics(sessionId, silent = false) {
    try {
        const res = await fetch(`/api/sessions/${sessionId}/topics`);
        if (res.ok) {
            currentSessionTopics = await res.json();
            if (currentSessionTopics.length > 0) {
                renderCurrentTopic();
            } else if (!silent) {
                document.getElementById('hub-carousel-content').textContent = 'No topics found for this session.';
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
    contentEl.innerHTML = ''; // Safe clear

    if (topic.llm_status === 'pending') {
        const div = document.createElement('div');
        div.className = "text-gray-400 italic animate-pulse flex items-center gap-2";
        div.innerHTML = `<span class="w-4 h-4 rounded-full border-2 border-brand-500 border-t-transparent animate-spin inline-block"></span> LM Studio generating detailed explanation...`;
        contentEl.appendChild(div);
    } else if (topic.unified_article) {
        // Attempt to parse JSON safely if it's JSON from the multi-pass pipeline
        let parsedArticle;
        let isJson = false;
        try {
            parsedArticle = JSON.parse(topic.unified_article);
            isJson = true;
        } catch(e) {
            isJson = false;
        }

        if (isJson && parsedArticle) {
            // Reconstruct HTML using safe DOM APIs
            const h2 = document.createElement('h2');
            h2.className = "text-xl font-bold mb-4";
            h2.textContent = `Unified Article: ${parsedArticle.subtopic || ''}`;

            const pSec = document.createElement('p');
            pSec.innerHTML = `<strong>Section:</strong> ${parsedArticle.section || ''}`;

            const pCtx = document.createElement('p');
            pCtx.innerHTML = `<strong>Context:</strong> ${parsedArticle.conditional_context || ''}`;

            const ul = document.createElement('ul');
            ul.className = "list-disc pl-5 mt-4 space-y-2";

            (parsedArticle.clinical_points || []).forEach(pt => {
                const li = document.createElement('li');
                li.textContent = pt.point + " ";
                if (pt.source) {
                    const badge = document.createElement('span');
                    badge.className = "text-xs bg-gray-700 text-brand-300 px-2 py-0.5 rounded-full ml-2";
                    badge.title = `Source: ${pt.source} (Page ${pt.page || 1})`;
                    badge.textContent = `[Ref: ${pt.source}]`;
                    li.appendChild(badge);
                }
                ul.appendChild(li);
            });

            contentEl.appendChild(h2);
            contentEl.appendChild(pSec);
            contentEl.appendChild(pCtx);
            contentEl.appendChild(ul);

            if (parsedArticle.discrepancies) {
                const disc = document.createElement('div');
                disc.className = "mt-6 bg-danger-500/10 border border-danger-500/30 p-4 rounded-lg";
                const discTitle = document.createElement('strong');
                discTitle.className = "text-danger-400 block mb-2";
                discTitle.textContent = "Discrepancies & Variances:";
                disc.appendChild(discTitle);
                const discText = document.createElement('span');
                discText.textContent = parsedArticle.discrepancies;
                disc.appendChild(discText);
                contentEl.appendChild(disc);
            }
        } else {
            // Fallback to markdown parsed output safely
            const htmlContent = window.marked && window.DOMPurify
                ? window.DOMPurify.sanitize(window.marked.parse(topic.unified_article))
                : window.DOMPurify ? window.DOMPurify.sanitize(topic.unified_article) : topic.unified_article;
            contentEl.innerHTML = htmlContent;
        }
    } else {
        const textNode = document.createTextNode(topic.enhanced_explanation || "No detailed notes available.");
        contentEl.appendChild(textNode);
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
