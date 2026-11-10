// ui/js/habits.js

// Basis-Point Precision Math & Coalesced Counter Controller
class PrecisionCounterController {
    constructor(displayElement, initialValue = 0, options = {}) {
        this.el = displayElement;
        // Store internally as integer basis points (1 unit = 10000 bp) to eliminate IEEE 754 drift
        this.valueBp = Math.round(Number(initialValue) * 10000);
        this.minBp = Math.round((options.min ?? 0) * 10000);
        this.maxBp = Math.round((options.max ?? 100000) * 10000);
        this.decimals = options.decimals ?? 0;
        this.suffix = options.suffix ?? '';
        this.onSyncCallback = options.onSyncCallback || null;

        this.rafId = null;
        this.displayedBp = this.valueBp;
        this.pendingDeltaBp = 0;
        this.syncTimer = null;
        this.renderImmediate(this.valueBp);
    }

    static formatBasisPoints(bpValue, decimals = 1, suffix = '') {
        const factor = Math.pow(10, decimals);
        const exactScaled = Math.round((bpValue / 10000) * factor) / factor;
        return `${exactScaled.toFixed(decimals)}${suffix}`;
    }

    static computeWeightedMasteryPercent(clusters) {
        if (!Array.isArray(clusters) || clusters.length === 0) return '0.0%';
        let totalWeightedBp = 0;
        let totalWeight = 0;
        for (const c of clusters) {
            const weight = Math.max(1, Number(c.priority_weight) || 1);
            const scoreBp = Math.round((Number(c.score) || (c.verified ? 0.88 : 0.40)) * 10000);
            totalWeightedBp += scoreBp * weight;
            totalWeight += weight;
        }
        // Convert 0..10000 basis points to 0..100.0%
        const percentBp = Math.round((totalWeightedBp / totalWeight) * 100);
        return PrecisionCounterController.formatBasisPoints(percentBp, 1, '%');
    }

    increment(deltaUnits = 1) {
        const deltaBp = Math.round(Number(deltaUnits) * 10000);
        const nextBp = Math.min(this.maxBp, Math.max(this.minBp, this.valueBp + deltaBp));
        const actualAppliedBp = nextBp - this.valueBp;
        if (actualAppliedBp === 0) return;

        this.valueBp = nextBp;
        this.pendingDeltaBp += actualAppliedBp;
        this.animateToTarget(this.valueBp);
        this.scheduleCoalescedSync();
    }

    animateToTarget(targetBp) {
        if (this.rafId) {
            cancelAnimationFrame(this.rafId);
        }
        const startBp = this.displayedBp;
        const diffBp = targetBp - startBp;
        const durationMs = 180;
        const startTime = performance.now();

        const step = (now) => {
            const elapsed = Math.min(1, (now - startTime) / durationMs);
            const eased = 1 - Math.pow(1 - elapsed, 3); // Cubic ease-out
            this.displayedBp = Math.round(startBp + diffBp * eased);
            this.renderImmediate(this.displayedBp);

            if (elapsed < 1) {
                this.rafId = requestAnimationFrame(step);
            } else {
                this.displayedBp = targetBp;
                this.renderImmediate(targetBp);
                this.rafId = null;
            }
        };
        this.rafId = requestAnimationFrame(step);
    }

    renderImmediate(bp) {
        if (!this.el) return;
        this.el.textContent = PrecisionCounterController.formatBasisPoints(bp, this.decimals, this.suffix);
    }

    scheduleCoalescedSync() {
        if (!this.onSyncCallback) return;
        if (this.syncTimer) clearTimeout(this.syncTimer);

        this.syncTimer = setTimeout(async () => {
            const deltaToCommit = this.pendingDeltaBp / 10000;
            const committedTotal = this.valueBp / 10000;
            this.pendingDeltaBp = 0;
            try {
                await this.onSyncCallback({ delta: deltaToCommit, total: committedTotal });
            } catch (err) {
                // Rollback on server failure
                this.valueBp -= Math.round(deltaToCommit * 10000);
                this.animateToTarget(this.valueBp);
            }
        }, 350);
    }
}

window.PrecisionCounterController = PrecisionCounterController;

// Activity-Aware Focus Mode & Wellness Break Engine
class FocusModeEngine {
  static CHECK_IN_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes
  static AUTO_PAUSE_TIMEOUT_MS = 10 * 1000;    // 10 seconds to respond
  static APP_ACTIVE_GRACE_MS = 4.5 * 60 * 1000; // If user interacted in-app within 4.5m, skip popup

  static CHECK_IN_PROMPTS = [
    "Still locked into your hardcopy textbook or notes?",
    "Quick check-in! Are you still actively reviewing your clinical material?",
    "5-minute textbook pulse check — still studying strong?",
    "Reading printed notes? Tap 'Yes' to keep your Focus Timer uninterrupted!",
    "Clinical mastery in progress! Confirm you're still at your desk:"
  ];

  static WELLNESS_TIPS = [
    {
      minMinutes: 0,
      maxMinutes: 20,
      type: "hydration",
      icon: "💧",
      title: "Hydration Micro-Check",
      message: "Take a quick sip of water. Even mild dehydration reduces working memory and MCQ recall speed."
    },
    {
      minMinutes: 20,
      maxMinutes: 40,
      type: "eyes",
      icon: "👁️",
      title: "20-20-20 Visual Reset",
      message: "Rest your eyes: look at something 20 feet away for 20 seconds to relieve ciliary muscle strain."
    },
    {
      minMinutes: 40,
      maxMinutes: 60,
      type: "music",
      icon: "🎵",
      title: "Auditory / Music Breather",
      message: "You've sustained 40+ mins of focus. A 2-minute lo-fi or instrumental music break can reset attention."
    },
    {
      minMinutes: 60,
      maxMinutes: 9999,
      type: "exercise",
      icon: "🚶",
      title: "Short Walk & Thoracic Stretch",
      message: "60+ minutes of deep study! Stand up for a 2-minute walk, neck rolls, or light stretching to boost circulation."
    }
  ];

  constructor(onStateChangeCallback) {
    this.onStateChange = onStateChangeCallback;
    this.isRunning = false;
    this.isPausedByTimeout = false;
    this.sessionContinuousMs = 0;
    this.todayFocusSeconds = 0;
    this.lastTickTs = null;
    this.lastAppInteractionTs = Date.now();
    this.lastCheckInTs = Date.now();

    this.tickIntervalId = null;
    this.modalCountdownId = null;
    this.modalRemainingMs = 0;
    this.activeModalEl = null;

    this._persistedDateStr = _getHabitLocalDateStr(new Date());
    this._saveDebounceTimer = null;

    this._bindGlobalActivityListeners();
    this._loadPersistedTodayFocus();
  }

  async _loadPersistedTodayFocus() {
    try {
      const res = await fetch('/api/state/habit_focus_tracker_v2');
      if (res.ok) {
        const data = await res.json();
        const todayStr = _getHabitLocalDateStr(new Date());
        if (data && data.date === todayStr && typeof data.todayFocusSeconds === 'number') {
          this.todayFocusSeconds = data.todayFocusSeconds;
          this._emitChange();
        }
      }
    } catch (_) {}
  }

  _persistFocusData() {
    if (this._saveDebounceTimer) clearTimeout(this._saveDebounceTimer);
    this._saveDebounceTimer = setTimeout(async () => {
      const todayStr = _getHabitLocalDateStr(new Date());
      const payload = {
        date: todayStr,
        todayFocusSeconds: Math.floor(this.todayFocusSeconds),
        updatedAt: Date.now()
      };
      try {
        await fetch('/api/state/habit_focus_tracker_v2', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
      } catch (_) {}
    }, 500);
  }

  _bindGlobalActivityListeners() {
    let throttleTs = 0;
    const markActivity = () => {
      const now = Date.now();
      if (now - throttleTs > 1500) {
        throttleTs = now;
        this.lastAppInteractionTs = now;
      }
    };

    ['pointerdown', 'keydown', 'wheel', 'touchstart'].forEach((evt) => {
      window.addEventListener(evt, markActivity, { passive: true });
    });
    // Capture scroll inside nested containers (reader, dossier, quiz)
    document.addEventListener('scroll', markActivity, { capture: true, passive: true });

    // Custom app events
    window.addEventListener('quiz:answer_submitted', markActivity, { passive: true });
    window.addEventListener('reader:scroll', markActivity, { passive: true });
  }

  markActiveInteraction() {
    this.lastAppInteractionTs = Date.now();
  }

  getWellnessRecommendation() {
    const mins = Math.floor(this.sessionContinuousMs / 60000);
    return (
      FocusModeEngine.WELLNESS_TIPS.find((w) => mins >= w.minMinutes && mins < w.maxMinutes) ||
      FocusModeEngine.WELLNESS_TIPS[0]
    );
  }

  startOrResume() {
    if (this.isRunning) return;
    const now = Date.now();
    this.isRunning = true;
    this.isPausedByTimeout = false;
    this.lastTickTs = now;
    this.lastCheckInTs = now;
    this.lastAppInteractionTs = now;

    if (this.tickIntervalId) clearInterval(this.tickIntervalId);
    this.tickIntervalId = setInterval(() => this._onTick(), 250);
    this._emitChange();
  }

  pause(reason = 'manual') {
    if (!this.isRunning) return;
    this._syncElapsedMs();
    this.isRunning = false;
    this.isPausedByTimeout = reason === 'timeout';
    this.lastTickTs = null;
    if (this.tickIntervalId) {
      clearInterval(this.tickIntervalId);
      this.tickIntervalId = null;
    }
    if (reason === 'manual') {
      this.sessionContinuousMs = 0;
    }
    this._closeCheckInModal();
    this._persistFocusData();
    this._emitChange();
  }

  toggle() {
    if (this.isRunning) {
      this.pause('manual');
    } else {
      this.startOrResume();
    }
  }

  _syncElapsedMs() {
    if (!this.isRunning || !this.lastTickTs) return;
    const now = Date.now();
    const deltaMs = Math.max(0, now - this.lastTickTs);
    this.lastTickTs = now;
    this.sessionContinuousMs += deltaMs;
    this.todayFocusSeconds += deltaMs / 1000;
  }

  _onTick() {
    this._syncElapsedMs();
    const now = Date.now();

    // Check if 5 minutes have elapsed since last check-in
    if (!this.activeModalEl && now - this.lastCheckInTs >= FocusModeEngine.CHECK_IN_INTERVAL_MS) {
      const idleDurationMs = now - this.lastAppInteractionTs;

      // If user was actively answering MCQs or scrolling notes inside the app, do NOT interrupt!
      if (idleDurationMs < FocusModeEngine.APP_ACTIVE_GRACE_MS) {
        this.lastCheckInTs = now;
      } else {
        // User has not touched the app for ~5 minutes (e.g., reading hardcopy notes) -> Show 10s check-in popup
        this._openCheckInModal();
      }
    }
    this._emitChange();
  }

  _playSubtleChime() {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      const ctx = new AudioCtx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(587.33, ctx.currentTime); // D5
      osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.18); // A5
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.45);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.45);
    } catch (_) {
      // Ignore if browser autoplay policy blocks audio
    }
  }

  _openCheckInModal() {
    if (this.activeModalEl) return;
    this._playSubtleChime();

    const promptText =
      FocusModeEngine.CHECK_IN_PROMPTS[
        Math.floor(Math.random() * FocusModeEngine.CHECK_IN_PROMPTS.length)
      ];
    const wellness = this.getWellnessRecommendation();
    const deadlineTs = Date.now() + FocusModeEngine.AUTO_PAUSE_TIMEOUT_MS;

    const modal = document.createElement('div');
    modal.id = 'focus-checkin-modal';
    modal.className =
      'fixed inset-0 z-[9999] flex items-center justify-center bg-slate-950/75 backdrop-blur-md animate-fade-in';
    modal.innerHTML = `
      <div class="relative w-full max-w-md p-6 rounded-2xl bg-slate-900/95 border border-emerald-500/40 shadow-2xl text-slate-100 transform transition-all scale-100">
        <div class="flex items-center justify-between mb-3">
          <span class="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
            <span class="w-2 h-2 rounded-full bg-emerald-400 animate-ping"></span>
            5-Minute Focus Check-In
          </span>
          <span id="focus-countdown-badge" class="text-sm font-mono font-bold text-amber-400">10.0s</span>
        </div>

        <h3 class="text-lg font-bold text-white mb-1">${promptText}</h3>
        <p class="text-xs text-slate-400 mb-4">
          Timer continues seamlessly when you confirm. Auto-pauses if unconfirmed in 10 seconds.
        </p>

        <div class="p-3.5 rounded-xl bg-sky-950/40 border border-sky-500/30 mb-5 flex items-start gap-3">
          <span class="text-2xl leading-none">${wellness.icon}</span>
          <div>
            <div class="text-xs font-bold text-sky-300 uppercase tracking-wider">${wellness.title}</div>
            <p class="text-xs text-slate-300 mt-0.5 leading-relaxed">${wellness.message}</p>
          </div>
        </div>

        <div class="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden mb-5">
          <div id="focus-countdown-bar" class="h-full bg-gradient-to-r from-emerald-400 to-amber-400 transition-all duration-100" style="width: 100%"></div>
        </div>

        <div class="flex items-center gap-3">
          <button type="button" id="btn-focus-confirm-yes" class="flex-1 py-2.5 px-4 rounded-xl bg-emerald-500 hover:bg-emerald-400 text-slate-950 font-bold text-sm shadow-lg shadow-emerald-500/25 transition transform active:scale-95">
            Yes, Still Studying! (Keep Timer Running)
          </button>
          <button type="button" id="btn-focus-pause-now" class="py-2.5 px-3.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-semibold transition">
            Take a Break
          </button>
        </div>
      </div>
    `;

    document.body.appendChild(modal);
    this.activeModalEl = modal;

    const confirmBtn = modal.querySelector('#btn-focus-confirm-yes');
    const pauseBtn = modal.querySelector('#btn-focus-pause-now');
    const badgeEl = modal.querySelector('#focus-countdown-badge');
    const barEl = modal.querySelector('#focus-countdown-bar');

    confirmBtn?.focus();

    confirmBtn?.addEventListener('click', () => {
      // Seamless continuation: timer never stopped!
      this.lastCheckInTs = Date.now();
      this.lastAppInteractionTs = Date.now();
      this._closeCheckInModal();
      this._emitChange();
    });

    pauseBtn?.addEventListener('click', () => {
      this.pause('manual');
    });

    this.modalCountdownId = setInterval(() => {
      const remMs = Math.max(0, deadlineTs - Date.now());
      const pct = (remMs / FocusModeEngine.AUTO_PAUSE_TIMEOUT_MS) * 100;
      if (badgeEl) badgeEl.textContent = `${(remMs / 1000).toFixed(1)}s`;
      if (barEl) barEl.style.width = `${pct}%`;

      if (remMs <= 0) {
        // Deduct the 10-second unconfirmed wait window and auto-pause
        this.todayFocusSeconds = Math.max(0, this.todayFocusSeconds - 10);
        this.pause('timeout');
      }
    }, 100);
  }

  _closeCheckInModal() {
    if (this.modalCountdownId) {
      clearInterval(this.modalCountdownId);
      this.modalCountdownId = null;
    }
    if (this.activeModalEl) {
      this.activeModalEl.remove();
      this.activeModalEl = null;
    }
  }

  static formatHMS(totalSeconds) {
    const sec = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    return [h, m, s].map((v) => String(v).padStart(2, '0')).join(':');
  }

  _emitChange() {
    if (typeof this.onStateChange === 'function') {
      this.onStateChange({
        isRunning: this.isRunning,
        isPausedByTimeout: this.isPausedByTimeout,
        todayFocusSeconds: Math.floor(this.todayFocusSeconds),
        sessionContinuousMs: this.sessionContinuousMs,
        wellness: this.getWellnessRecommendation()
      });
    }
    window.dispatchEvent(new CustomEvent('focusModeUpdated', {
      detail: {
        isRunning: this.isRunning,
        isPausedByTimeout: this.isPausedByTimeout,
        todayFocusSeconds: Math.floor(this.todayFocusSeconds),
        sessionContinuousMs: this.sessionContinuousMs,
        wellness: this.getWellnessRecommendation()
      }
    }));
  }
}

window.FocusModeEngine = FocusModeEngine;
if (!window.focusModeEngine) {
  window.focusModeEngine = new FocusModeEngine();
}

// Named Daily Checklist Builder, Big Green Tick & Historical SVG Line Graph
function renderHistoricalPerformanceSvgChart(historySeries = [], targetAccuracy = null) {
  if (!Array.isArray(historySeries) || historySeries.length === 0) {
    return `
      <div class="h-36 flex items-center justify-center rounded-xl bg-slate-950/60 border border-slate-800/80 text-xs text-slate-500">
        No quiz sessions recorded for this Subject / Topic / Keyword yet. Complete a set to unlock trend comparison!
      </div>
    `;
  }

  const width = 560;
  const height = 160;
  const padLeft = 36;
  const padRight = 20;
  const padTop = 18;
  const padBottom = 28;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const points = historySeries.map((item, idx) => {
    const x =
      historySeries.length === 1
        ? padLeft + plotW / 2
        : padLeft + (idx / (historySeries.length - 1)) * plotW;
    const acc = Math.max(0, Math.min(100, Number(item.accuracy) || 0));
    const y = padTop + plotH - (acc / 100) * plotH;
    return { x: Number(x.toFixed(1)), y: Number(y.toFixed(1)), item };
  });

  const polylinePoints = points.map((p) => `${p.x},${p.y}`).join(' ');
  const areaPoints = `${points[0].x},${padTop + plotH} ${polylinePoints} ${points[points.length - 1].x},${padTop + plotH}`;

  const targetY =
    targetAccuracy && targetAccuracy > 0
      ? Number((padTop + plotH - (Math.min(100, targetAccuracy) / 100) * plotH).toFixed(1))
      : null;

  return `
    <div class="relative rounded-xl bg-slate-950/70 border border-slate-800/90 p-3">
      <div class="flex items-center justify-between text-[11px] text-slate-400 mb-1">
        <span class="font-semibold text-slate-300">Performance Trajectory (Earlier Sessions vs. Today)</span>
        <span class="flex items-center gap-3">
          <span class="inline-flex items-center gap-1"><i class="w-2 h-2 rounded-full bg-sky-400 inline-block"></i> Earlier</span>
          <span class="inline-flex items-center gap-1"><i class="w-2.5 h-2.5 rounded-full bg-emerald-400 inline-block"></i> Today</span>
        </span>
      </div>
      <svg viewBox="0 0 ${width} ${height}" class="w-full h-36 overflow-visible">
        <defs>
          <linearGradient id="goalChartGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#10b981" stop-opacity="0.35"/>
            <stop offset="100%" stop-color="#0ea5e9" stop-opacity="0.0"/>
          </linearGradient>
        </defs>

        <!-- Horizontal Grid Lines (0%, 50%, 100%) -->
        ${[0, 50, 100].map((pct) => {
          const gy = (padTop + plotH - (pct / 100) * plotH).toFixed(1);
          return `
            <line x1="${padLeft}" y1="${gy}" x2="${width - padRight}" y2="${gy}" stroke="rgba(148,163,184,0.12)" stroke-dasharray="3,3" />
            <text x="${padLeft - 6}" y="${Number(gy) + 3}" text-anchor="end" fill="#64748b" font-size="9">${pct}%</text>
          `;
        }).join('')}

        ${targetY !== null ? `
          <line x1="${padLeft}" y1="${targetY}" x2="${width - padRight}" y2="${targetY}" stroke="#f59e0b" stroke-width="1.2" stroke-dasharray="4,4" />
          <text x="${width - padRight}" y="${targetY - 4}" text-anchor="end" fill="#fbbf24" font-size="9">Goal ${targetAccuracy}%</text>
        ` : ''}

        <!-- Shaded Area & Animated Trend Line -->
        <polygon points="${areaPoints}" fill="url(#goalChartGrad)" />
        <polyline
          fill="none"
          stroke="#38bdf8"
          stroke-width="2.5"
          stroke-linecap="round"
          stroke-linejoin="round"
          class="chart-line-draw"
          points="${polylinePoints}"
        />

        <!-- Data Nodes -->
        ${points.map((p, idx) => {
          const isToday = Boolean(p.item.is_today);
          const color = isToday ? '#10b981' : '#38bdf8';
          const radius = isToday ? 5 : 3.5;
          const shortDate = (p.item.date || '').slice(5) || `#${idx + 1}`;
          return `
            <g class="group cursor-pointer">
              <circle cx="${p.x}" cy="${p.y}" r="${radius}" fill="${color}" stroke="#0f172a" stroke-width="1.5" />
              <text x="${p.x}" y="${height - 6}" text-anchor="middle" fill="#64748b" font-size="8.5">${shortDate}</text>
              <title>${p.item.label} (${p.item.date}): ${p.item.accuracy}% (${p.item.correct}/${p.item.attempted}) in ${Math.round(p.item.time_spent_sec)}s</title>
            </g>
          `;
        }).join('')}
      </svg>
    </div>
  `;
}

window.renderHistoricalPerformanceSvgChart = renderHistoricalPerformanceSvgChart;

// Expanded Checklist Goal Card Markup
function renderDailyChecklistItemCard(goal, analyticsData, isExpanded = false) {
  const isCompleted = Boolean(goal.completed);
  const todayStats = analyticsData?.today || { attempted: 0, correct: 0, accuracy: 0, time_spent_sec: 0 };
  const earlierAvg = analyticsData?.earlier_baseline_accuracy ?? 0;
  const diffDelta = (todayStats.accuracy - earlierAvg).toFixed(1);
  const progressPct = Math.min(100, Math.round(((Number(goal.currentValue) || 0) / Math.max(1, Number(goal.targetValue) || 1)) * 100));

  return `
    <div class="checklist-card rounded-2xl border transition-all duration-300 ${
      isCompleted
        ? 'bg-emerald-950/25 border-emerald-500/50 shadow-lg shadow-emerald-500/10'
        : 'bg-slate-900/70 border-slate-800 hover:border-slate-700'
    }" data-goal-id="${goal.id}">
      <div class="p-4 flex items-center justify-between gap-4 cursor-pointer select-none checklist-card-header">
        <!-- Big Green Tick Checkbox Button -->
        <button
          type="button"
          class="goal-check-toggle relative flex items-center justify-center w-9 h-9 rounded-xl border-2 transition-all duration-300 shrink-0 ${
            isCompleted
              ? 'bg-emerald-500 border-emerald-400 shadow-md shadow-emerald-500/30 scale-105'
              : 'bg-slate-950 border-slate-600 hover:border-emerald-400/60'
          }"
          aria-label="Toggle goal completion"
          data-goal-id="${goal.id}"
        >
          <svg class="w-6 h-6 text-slate-950 ${isCompleted ? 'tick-svg-animated opacity-100' : 'opacity-0'}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
        </button>

        <!-- Goal Title, Scope Pill & Progress Bar -->
        <div class="flex-1 min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-bold text-sm ${isCompleted ? 'line-through text-emerald-300' : 'text-slate-100'}">
              ${goal.title}
            </span>
            ${goal.scopeValue ? `
              <span class="px-2 py-0.5 rounded-md text-[10px] font-semibold uppercase tracking-wide bg-sky-500/15 text-sky-300 border border-sky-500/30">
                ${goal.scopeType}:${goal.scopeValue}
              </span>
            ` : ''}
            ${isCompleted ? `
              <span class="px-2 py-0.5 rounded-full text-[10px] font-extrabold uppercase bg-emerald-500/20 text-emerald-300 border border-emerald-400/40 animate-bounce-once">
                ✓ Goal Achieved
              </span>
            ` : ''}
          </div>

          <div class="mt-2 flex items-center gap-3">
            <div class="flex-1 h-2 bg-slate-800 rounded-full overflow-hidden">
              <div class="h-full rounded-full transition-all duration-500 ${
                isCompleted ? 'bg-emerald-400' : 'bg-gradient-to-r from-sky-500 to-violet-500'
              }" style="width: ${isCompleted ? 100 : progressPct}%"></div>
            </div>
            <span class="text-xs font-mono text-slate-300 shrink-0">
              ${goal.currentValue ?? 0} / ${goal.targetValue}${goal.metricType === 'accuracy_pct' ? '%' : (goal.metricType === 'focus_minutes' ? 'm' : '')}
            </span>
          </div>
        </div>

        <!-- Controls: Delete & Expand Chevron -->
        <div class="flex items-center gap-2 shrink-0">
          <button type="button" class="btn-delete-checklist-goal text-slate-400 hover:text-rose-400 p-1 bg-transparent border-none cursor-pointer transition text-xs" data-goal-id="${goal.id}" title="Remove Goal">
            ✕
          </button>
          <div class="text-slate-400 text-xs flex items-center gap-1 cursor-pointer">
            <span class="hidden sm:inline text-[11px]">${isExpanded ? 'Hide Stats' : 'Analytics'}</span>
            <svg class="w-4 h-4 transition-transform duration-300 ${isExpanded ? 'rotate-180 text-sky-400' : ''}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="6 9 12 15 18 9"></polyline>
            </svg>
          </div>
        </div>
      </div>

      <!-- Expandable Analytics & Historical Line Graph Drawer -->
      <div class="checklist-card-drawer overflow-hidden transition-all duration-300 ${
        isExpanded ? 'max-h-[460px] opacity-100 border-t border-slate-800/80 p-4 pt-3' : 'max-h-0 opacity-0'
      }">
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-2.5 mb-3">
          <div class="p-2.5 rounded-xl bg-slate-950/70 border border-slate-800">
            <div class="text-[10px] uppercase text-slate-400">MCQs Attempted</div>
            <div class="text-base font-bold text-white mt-0.5">${todayStats.attempted}</div>
          </div>
          <div class="p-2.5 rounded-xl bg-slate-950/70 border border-slate-800">
            <div class="text-[10px] uppercase text-slate-400">Correct Answers</div>
            <div class="text-base font-bold text-emerald-400 mt-0.5">${todayStats.correct}</div>
          </div>
          <div class="p-2.5 rounded-xl bg-slate-950/70 border border-slate-800">
            <div class="text-[10px] uppercase text-slate-400">Today vs. Earlier</div>
            <div class="text-base font-bold text-sky-400 mt-0.5">
              ${todayStats.accuracy}%
              <span class="text-[11px] font-normal ${Number(diffDelta) >= 0 ? 'text-emerald-400' : 'text-rose-400'}">
                (${Number(diffDelta) >= 0 ? '+' : ''}${diffDelta}%)
              </span>
            </div>
          </div>
          <div class="p-2.5 rounded-xl bg-slate-950/70 border border-slate-800">
            <div class="text-[10px] uppercase text-slate-400">Time on Topic</div>
            <div class="text-base font-bold text-amber-300 mt-0.5">
              ${Math.floor(todayStats.time_spent_sec / 60)}m ${Math.round(todayStats.time_spent_sec % 60)}s
            </div>
          </div>
        </div>

        ${renderHistoricalPerformanceSvgChart(
          analyticsData?.history_series || [],
          goal.metricType === 'accuracy_pct' ? goal.targetValue : null
        )}
      </div>
    </div>
  `;
}

window.renderDailyChecklistItemCard = renderDailyChecklistItemCard;

function _getHabitLocalDateStr(dateObj = new Date()) {
    const year = dateObj.getFullYear();
    const month = String(dateObj.getMonth() + 1).padStart(2, '0');
    const day = String(dateObj.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

// In-memory cache for expanded checklist items and analytics
const _expandedGoalsMap = new Set();
const _analyticsCache = new Map();

async function fetchGoalAnalytics(scopeType = 'all', scopeValue = '') {
  const cacheKey = `${scopeType}::${scopeValue}`;
  if (_analyticsCache.has(cacheKey)) {
    return _analyticsCache.get(cacheKey);
  }
  try {
    const params = new URLSearchParams({ scope_type: scopeType, scope_value: scopeValue });
    const res = await fetch(`/api/sessions/checklist-analytics?${params.toString()}`);
    if (res.ok) {
      const data = await res.json();
      _analyticsCache.set(cacheKey, data);
      return data;
    }
  } catch (e) {
    console.warn("Failed fetching checklist analytics:", e);
  }
  return {
    scope_type: scopeType,
    scope_value: scopeValue,
    today: { attempted: 0, correct: 0, accuracy: 0, time_spent_sec: 0 },
    earlier_baseline_accuracy: 0,
    history_series: []
  };
}

async function initHabits() {
    if (!window.AppState || typeof window.AppState.get !== 'function') return;
    const state = window.AppState.get();
    if (!state) return;

    const container = document.getElementById('habit-tracker-view');
    if (!container) return;

    const escapeHTML = window.escapeHTML || ((s) => String(s ?? ''));

    const habitLog = Array.isArray(state.habitLog) ? state.habitLog : [];
    const testResults = Array.isArray(state.testResults) ? state.testResults : [];
    const streakDays = Number(state.streakDays || 0);
    const longestStreak = Number(state.longestStreak || 0);
    const sumFromResults = testResults.reduce((acc, r) => acc + Number(r.totalQuestions ?? r.total_questions ?? 0), 0);
    const totalQuestionsEver = Math.max(Number(state.totalQuestionsEver || 0), sumFromResults);
    const dailyTargets = state.dailyTargets || { questionsTarget: 50, correctTarget: 40, hoursTarget: 2, reviewTarget: 20, customGoals: [] };
    const targetQuestions = Math.max(1, Number(dailyTargets.questionsTarget || 50));
    const targetStudyHours = Math.max(0.1, Number(dailyTargets.hoursTarget || 2));
    const targetStudySec = Math.round(targetStudyHours * 3600);

    // Daily Checklist Support: named plan and goals list
    const checklistTitle = dailyTargets.checklistTitle || dailyTargets.dailyChecklistTitle || "Daily Study Plan";
    const rawChecklistGoals = Array.isArray(dailyTargets.dailyChecklist)
      ? dailyTargets.dailyChecklist
      : (Array.isArray(dailyTargets.customGoals) ? dailyTargets.customGoals.map(cg => ({
          id: cg.id || `goal_${Date.now()}_${Math.random()}`,
          title: cg.name || `${cg.targetType || 'subject'}: ${cg.value || ''}`,
          metricType: 'mcq_count',
          targetValue: cg.targetQuestions || 10,
          currentValue: cg.currentCount || 0,
          scopeType: cg.targetType || 'all',
          scopeValue: cg.value || '',
          completed: Boolean(cg.completed)
        })) : []);

    // 1. Data Aggregation & O(1) Lookups (Local Calendar Dates)
    const habitMap = new Map();
    habitLog.forEach(h => {
        if (h && h.date) habitMap.set(String(h.date).slice(0, 10), h);
    });

    const today = new Date();
    const todayStr = _getHabitLocalDateStr(today);
    const todayCount = Number(habitMap.get(todayStr)?.questionsAnswered || 0);

    // Active MCQ Solving Time for Today from testResults / session_questions
    let todayMcqSeconds = 0;
    let todayMcqAttempted = 0;
    let todayMcqCorrect = 0;
    testResults.forEach(r => {
      const rDate = r.date ? String(r.date).slice(0, 10) : '';
      const ts = r.timestamp || r.lastAttempted || r.last_attempted;
      const tsMs = Number(ts || 0) < 1e11 ? Number(ts || 0) * 1000 : Number(ts || 0);
      const isToday = rDate === todayStr || (tsMs && _getHabitLocalDateStr(new Date(tsMs)) === todayStr);
      if (isToday) {
        todayMcqSeconds += Number(r.timeUsedSeconds ?? r.time_used_seconds ?? 0);
        todayMcqAttempted += Number(r.totalQuestions ?? r.total_questions ?? 0);
        todayMcqCorrect += Number(r.correctAnswers ?? r.correct_answers ?? 0);
      }
    });

    // If habitLog had records, merge
    const todayHabitRec = habitMap.get(todayStr);
    if (todayHabitRec) {
      if (todayHabitRec.timeSpentSeconds && todayHabitRec.timeSpentSeconds > todayMcqSeconds) {
        todayMcqSeconds = todayHabitRec.timeSpentSeconds;
      }
      if (todayHabitRec.questionsAnswered && todayHabitRec.questionsAnswered > todayMcqAttempted) {
        todayMcqAttempted = todayHabitRec.questionsAnswered;
      }
      if (todayHabitRec.correctAnswers && todayHabitRec.correctAnswers > todayMcqCorrect) {
        todayMcqCorrect = todayHabitRec.correctAnswers;
      }
    }

    window._lastMcqSeconds = todayMcqSeconds;
    const todayFocusSec = window.focusModeEngine ? window.focusModeEngine.todayFocusSeconds : 0;
    const combinedStudySec = Math.floor(todayFocusSec + todayMcqSeconds);
    const todayAccuracyPct = todayMcqAttempted > 0 ? Math.round((todayMcqCorrect / todayMcqAttempted) * 100) : 0;
    const studyTimePct = Math.min(100, Math.round((combinedStudySec / targetStudySec) * 100));
    const mcqCompletedPct = Math.min(100, Math.round((todayMcqAttempted / targetQuestions) * 100));

    // Evaluate goals against live metrics
    // Fetch analytics for each goal concurrently
    const evaluatedGoals = await Promise.all(
      rawChecklistGoals.map(async (g) => {
        const goal = { ...g };
        let curVal = Number(goal.currentValue) || 0;
        let isDone = Boolean(goal.completed);

        if (goal.metricType === 'focus_minutes') {
          curVal = Math.floor(todayFocusSec / 60);
          if (curVal >= Number(goal.targetValue || 0)) isDone = true;
        } else if (goal.metricType === 'manual') {
          // Keep manual boolean toggle
          curVal = isDone ? Number(goal.targetValue || 1) : 0;
        } else {
          // MCQ metrics: query analytics for this scope
          const analytics = await fetchGoalAnalytics(goal.scopeType || 'all', goal.scopeValue || '');
          const att = analytics?.today?.attempted || 0;
          const corr = analytics?.today?.correct || 0;
          const acc = analytics?.today?.accuracy || 0;

          if (goal.metricType === 'mcq_count') {
            curVal = att;
            if (curVal >= Number(goal.targetValue || 1)) isDone = true;
          } else if (goal.metricType === 'correct_count') {
            curVal = corr;
            if (curVal >= Number(goal.targetValue || 1)) isDone = true;
          } else if (goal.metricType === 'accuracy_pct') {
            curVal = acc;
            const minAtt = Number(goal.minQuestions || 5);
            if (att >= minAtt && acc >= Number(goal.targetValue || 0)) isDone = true;
          }
        }

        goal.currentValue = curVal;
        goal.completed = isDone;
        return goal;
      })
    );

    // Save evaluated goals back to state if changed
    const checklistSummary = {
      listName: checklistTitle,
      done: evaluatedGoals.filter(g => g.completed).length,
      total: evaluatedGoals.length
    };
    window._lastChecklistSummary = checklistSummary;

    const calendarData = [];
    // Anchor 364 days ago from today, then align start to preceding Sunday so week grid (Sun-Sat) is contiguous
    const rawStart = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 364);
    const alignedStart = new Date(rawStart.getFullYear(), rawStart.getMonth(), rawStart.getDate() - rawStart.getDay());
    const curDate = new Date(alignedStart);

    while (curDate <= today) {
        const dateStr = _getHabitLocalDateStr(curDate);
        const habit = habitMap.get(dateStr);
        calendarData.push({
            date: dateStr,
            dateObj: new Date(curDate),
            count: Number(habit?.questionsAnswered || 0),
            day: curDate.getDay()
        });
        curDate.setDate(curDate.getDate() + 1);
    }

    // Group into weeks for the Heatmap (Sun=0 to Sat=6)
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

    // Slice Stats (last 7 and 30 calendar days)
    const last7Days = calendarData.slice(-7);
    const wQuestions = last7Days.reduce((acc, d) => acc + d.count, 0);
    const wActive = last7Days.filter(d => d.count > 0).length;

    const last30Days = calendarData.slice(-30);
    const mQuestions = last30Days.reduce((acc, d) => acc + d.count, 0);
    const mActive = last30Days.filter(d => d.count > 0).length;

    const activeHabits = habitLog.filter(h => Number(h?.questionsAnswered || 0) > 0);
    const bestDay = activeHabits.length > 0
        ? activeHabits.reduce((b, h) => Number(h.questionsAnswered || 0) > Number(b.questionsAnswered || 0) ? h : b, activeHabits[0])
        : null;

    // Month headers precisely aligned with week columns
    let prevMonthIdx = -1;
    const monthHeaderColsHtml = weeks.map((week, wi) => {
        const firstOfMonth = week.find(d => d.dateObj.getDate() === 1);
        let label = '';
        if (firstOfMonth) {
            label = firstOfMonth.dateObj.toLocaleString('default', { month: 'short' });
            prevMonthIdx = firstOfMonth.dateObj.getMonth();
        } else if (wi === 0) {
            label = week[0].dateObj.toLocaleString('default', { month: 'short' });
            prevMonthIdx = week[0].dateObj.getMonth();
        }
        return `
            <div class="w-3 shrink-0 relative flex items-center justify-start">
                ${label ? `<span class="text-[10px] text-muted font-medium select-none absolute left-0 -top-1 whitespace-nowrap z-10">${escapeHTML(label)}</span>` : ''}
            </div>
        `;
    }).join('');

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

        if (todayCount >= targetQuestions) {
            return { emoji: '🏆', text: "Mission accomplished! You hit all your goals for today.", color: 'text-success-500' };
        }
        if (streakDays >= 30) return { emoji: '👑', text: "Legendary! 30+ days of pure dedication.", color: 'text-warning-500' };
        if (streakDays >= 7) return { emoji: '🔥', text: "You're on fire! A week-long streak!", color: 'text-warning-500' };
        if (todayCount > 0) return { emoji: '📈', text: "Great start! Keep the momentum going.", color: 'text-brand-400' };

        return { emoji, text: greeting, color: 'text-muted' };
    };

    const motivation = getMotivation();
    const goalProgress = Math.min((todayCount / targetQuestions) * 100, 100);

    // 3. Heatmap Generator
    const getIntensity = (count) => {
        if (count === 0) return 'heatmap-cell-0';
        if (count <= 5) return 'heatmap-cell-1';
        if (count <= 15) return 'heatmap-cell-2';
        if (count <= 30) return 'heatmap-cell-3';
        return 'heatmap-cell-4';
    };

    const heatmapColsHtml = weeks.map((week, wi) => {
        let cellsHtml = '';
        if (wi === 0 && week[0]) {
            for (let p = 0; p < week[0].day; p++) cellsHtml += `<div class="w-3 h-3"></div>`;
        }
        week.forEach(day => {
            cellsHtml += `<div class="w-3 h-3 rounded-[2px] transition-transform hover:scale-125 cursor-pointer ${getIntensity(day.count)}" title="${escapeHTML(day.date)}: ${day.count} questions"></div>`;
        });
        return `<div class="flex flex-col gap-1">${cellsHtml}</div>`;
    }).join('');

    // 4. Recent Activity List
    const recentActivity = [...habitLog]
        .filter(h => h && h.date)
        .sort((a, b) => String(b.date).localeCompare(String(a.date)))
        .slice(0, 7);
    const recentActivityHtml = recentActivity.length === 0
        ? '<p class="text-sm text-muted">No activity recorded yet.</p>'
        : recentActivity.map(h => `
            <div class="flex items-center justify-between glass-panel border border-gray-700/50 rounded-lg p-3">
                <div>
                    <span class="text-sm font-medium">${escapeHTML(h.date)}</span>
                    <span class="text-xs text-muted ml-2 truncate max-w-[140px] inline-block align-bottom">${escapeHTML((h.subjects || []).join(', '))}</span>
                </div>
                <div class="flex items-center gap-4 text-sm font-medium">
                    <span class="text-brand-400">${Number(h.questionsAnswered || 0)} Q</span>
                    <span class="text-success-500">${Number(h.correctAnswers || 0)} ✓</span>
                </div>
            </div>
        `).join('');

    // 5. Milestones Array
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
        <div class="p-4 rounded-xl border transition-all ${achieved ? 'bg-success-500/10 border-success-500/30' : 'glass-panel border-gray-700/50 opacity-60'} text-center flex flex-col justify-center items-center">
            <span class="text-2xl mb-2">${achieved ? escapeHTML(m.icon) : '🔒'}</span>
            <p class="text-xs font-medium ${achieved ? 'text-success-400' : 'text-muted'}">${escapeHTML(m.label)}</p>
            ${!achieved ? `<p class="text-[10px] text-muted mt-1">${m.current}/${m.target}</p>` : ''}
        </div>`;
    }).join('');

    // SVG Icons
    const icons = {
        flame: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-warning-500 mb-2 mx-auto"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"></path></svg>`,
        award: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-warning-400 mb-2 mx-auto"><circle cx="12" cy="8" r="7"></circle><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"></polyline></svg>`,
        book: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-brand-400 mb-2 mx-auto"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"></path><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg>`,
        cal: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-purple-400 mb-2 mx-auto"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>`,
        trendUp: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-success-500 mb-2 mx-auto"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"></polyline><polyline points="17 6 23 6 23 12"></polyline></svg>`,
        target: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-brand-400 mb-2 mx-auto"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="6"></circle><circle cx="12" cy="12" r="2"></circle></svg>`
    };

    // Build checklist cards HTML
    const checklistCardsHtml = evaluatedGoals.map(g => {
      const isExpanded = _expandedGoalsMap.has(g.id);
      const analytics = _analyticsCache.get(`${g.scopeType || 'all'}::${g.scopeValue || ''}`);
      return renderDailyChecklistItemCard(g, analytics, isExpanded);
    }).join('');

    const focusEngine = window.focusModeEngine;
    const isFocusRunning = focusEngine ? focusEngine.isRunning : false;
    const focusTimerFormatted = FocusModeEngine.formatHMS(todayFocusSec);
    const mcqTimeFormatted = FocusModeEngine.formatHMS(todayMcqSeconds);
    const combinedTimeFormatted = FocusModeEngine.formatHMS(combinedStudySec);

    // 6. Render DOM
    container.innerHTML = `
      <div class="w-full p-6 animate-fade-in">
        <div class="flex items-center justify-between flex-wrap gap-4 mb-8">
            <div>
                <h1 class="text-3xl font-bold mb-2">📅 Habit Tracker</h1>
                <p class="subtitle">Track your daily learning consistency and build unbreakable momentum.</p>
            </div>
            <!-- Dual-Location Focus Control in Habit Tracker -->
            <div class="flex items-center gap-3">
              <div class="px-4 py-2 rounded-xl bg-slate-950/80 border border-slate-800 flex items-center gap-3">
                <span id="habit-focus-dot" class="w-2.5 h-2.5 rounded-full ${isFocusRunning ? 'bg-emerald-400 animate-ping' : 'bg-slate-500'}"></span>
                <div>
                  <div class="text-[10px] uppercase tracking-wider text-slate-400">Focus Mode</div>
                  <div id="habit-focus-timer-text" class="text-sm font-mono font-bold text-white">${focusTimerFormatted}</div>
                </div>
              </div>
              <button
                type="button"
                id="habit-focus-toggle-btn"
                class="px-4 py-2 rounded-xl font-bold text-xs shadow-lg transition-all ${
                  isFocusRunning ? 'bg-amber-500 hover:bg-amber-400 text-slate-950' : 'bg-emerald-500 hover:bg-emerald-400 text-slate-950'
                }"
              >
                ${isFocusRunning ? '⏸ Pause Focus' : '▶ Start Focus'}
              </button>
            </div>
        </div>

        <!-- Four Unified Daily Telemetry Cards -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            <div class="glass-card card-shade-1 p-4 rounded-2xl border border-sky-500/20">
                <div class="flex items-center justify-between mb-1">
                    <span class="text-xs uppercase tracking-wider text-sky-400 font-bold">Focus Mode Time</span>
                    <span class="text-lg">⏱️</span>
                </div>
                <p id="kpi-focus-time" class="text-2xl font-bold font-mono text-white mt-1">${focusTimerFormatted}</p>
                <p class="text-[11px] text-slate-400 mt-1">Devoted to textbook & deep reading</p>
            </div>
            <div class="glass-card card-shade-1 p-4 rounded-2xl border border-violet-500/20">
                <div class="flex items-center justify-between mb-1">
                    <span class="text-xs uppercase tracking-wider text-violet-400 font-bold">MCQ Solving Time</span>
                    <span class="text-lg">🎯</span>
                </div>
                <p id="kpi-mcq-time" class="text-2xl font-bold font-mono text-white mt-1">${mcqTimeFormatted}</p>
                <p class="text-[11px] text-slate-400 mt-1">Aggregated question solving time</p>
            </div>
            <div class="glass-card card-shade-1 p-4 rounded-2xl border border-emerald-500/30 relative overflow-hidden group">
                <div id="kpi-study-progress-fill" class="absolute inset-0 bg-gradient-to-r from-emerald-600/35 to-emerald-400/20 rounded-2xl pointer-events-none transition-all duration-700" style="width: ${studyTimePct}%;"></div>
                <div class="relative z-10">
                    <div class="flex items-center justify-between mb-1">
                        <span class="text-xs uppercase tracking-wider text-emerald-400 font-bold">Study Time</span>
                        <span class="text-lg">⚡</span>
                    </div>
                    <div class="flex items-baseline justify-between mt-1">
                        <p id="kpi-combined-time" class="text-2xl font-bold font-mono text-emerald-300">${combinedTimeFormatted}</p>
                        <span id="kpi-study-pct-badge" class="text-xs px-2 py-0.5 rounded-full font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 font-mono">
                            ${studyTimePct}%
                        </span>
                    </div>
                    <p class="text-[11px] text-slate-400 mt-1">Goal: ${targetStudyHours}h (${studyTimePct}% done)</p>
                </div>
            </div>
            <div class="glass-card card-shade-1 p-4 rounded-2xl border border-amber-500/30 relative overflow-hidden group">
                <div id="kpi-mcqs-progress-fill" class="absolute inset-0 bg-gradient-to-r from-amber-600/35 to-amber-400/20 rounded-2xl pointer-events-none transition-all duration-700" style="width: ${mcqCompletedPct}%;"></div>
                <div class="relative z-10">
                    <div class="flex items-center justify-between mb-1">
                        <span class="text-xs uppercase tracking-wider text-amber-400 font-bold">MCQs Completed</span>
                        <span class="text-lg">📝</span>
                    </div>
                    <div class="flex items-baseline justify-between mt-1">
                        <div class="flex items-baseline gap-2">
                            <span id="kpi-mcqs-done" class="text-2xl font-bold font-mono text-white">${todayMcqAttempted}</span>
                            <span class="text-xs px-1.5 py-0.5 rounded font-semibold bg-amber-500/20 text-amber-300 border border-amber-500/30">
                                ${todayAccuracyPct}% Acc
                            </span>
                        </div>
                        <span id="kpi-mcq-pct-badge" class="text-xs px-2 py-0.5 rounded-full font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30 font-mono">
                            ${mcqCompletedPct}%
                        </span>
                    </div>
                    <p class="text-[11px] text-slate-400 mt-1">Goal: ${targetQuestions} Qs (${mcqCompletedPct}% done)</p>
                </div>
            </div>
        </div>

        <!-- Motivation Banner -->
        <div class="glass-card card-shade-1 p-6 mb-8 flex items-center gap-5">
            <div class="w-16 h-16 glass-panel rounded-full flex items-center justify-center text-4xl shadow-inner border border-gray-700/50 shrink-0">
                ${motivation.emoji}
            </div>
            <div class="flex-1 min-w-0">
                <h2 class="text-2xl font-bold mb-1">
                    ${streakDays > 0 ? `${streakDays} Day Streak! 🔥` : 'Start Your Streak Today! 💪'}
                </h2>
                <p class="${motivation.color} text-sm">${escapeHTML(motivation.text)}</p>

                <!-- Progress Bar -->
                <div class="mt-4">
                    <div class="flex justify-between items-center mb-1 text-xs text-muted">
                        <span>Today's General Goal: ${todayCount}/${targetQuestions} questions</span>
                        <span class="font-medium">${goalProgress.toFixed(0)}%</span>
                    </div>
                    <div class="w-full glass-panel rounded-full h-2 shadow-inner border border-gray-700/50 overflow-hidden">
                        <div class="h-2 rounded-full transition-all duration-1000 ${goalProgress >= 100 ? 'bg-success-500 shadow-[0_0_10px_rgba(34,197,94,0.6)]' : 'bg-brand-500 shadow-[0_0_10px_rgba(139,92,246,0.6)]'}" style="width: ${goalProgress}%"></div>
                    </div>
                </div>
            </div>
        </div>

        <!-- NAMED DAILY CHECKLIST SECTION -->
        <div class="glass-card card-shade-2 p-6 mb-8">
            <div class="flex items-center justify-between flex-wrap gap-3 mb-5 border-b border-gray-700/50 pb-4">
                <div class="flex items-center gap-3 flex-1 min-w-[240px]">
                    <span class="text-2xl">📋</span>
                    <div class="flex-1">
                        <div class="flex items-center gap-2">
                            <input
                              type="text"
                              id="input-checklist-title"
                              value="${escapeHTML(checklistTitle)}"
                              class="text-lg font-bold bg-transparent border-b border-dashed border-sky-400/50 text-white focus:border-sky-400 outline-none px-1 py-0.5 rounded transition"
                              title="Click to rename today's checklist"
                            />
                            <span class="text-xs px-2.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 font-semibold">
                                ${checklistSummary.done} / ${checklistSummary.total} Completed
                            </span>
                        </div>
                        <p class="text-xs text-slate-400 mt-1">Rule-based MCQ & focus goals evaluated in real-time. Click any card to expand analytics.</p>
                    </div>
                </div>
                <button type="button" id="btn-add-checklist-goal" class="primary-btn px-4 py-2 rounded-xl text-xs font-bold shadow-lg flex items-center gap-2 cursor-pointer">
                    <span>➕</span> Add Goal to Checklist
                </button>
            </div>

            ${evaluatedGoals.length === 0 ? `
                <div class="text-center py-8 glass-panel rounded-xl border border-gray-700/50 text-muted text-xs">
                    No goals added to today's checklist yet. Click <strong>+ Add Goal to Checklist</strong> to set target MCQs, accuracy, or focus reading minutes!
                </div>
            ` : `
                <div class="space-y-3" id="checklist-items-container">
                    ${checklistCardsHtml}
                </div>
            `}
        </div>

        <!-- Stats Grid -->
        <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4 mb-8">
            <div class="glass-card card-shade-2 p-4 text-center">
                ${icons.flame}
                <p class="text-2xl font-bold">${streakDays}</p>
                <p class="text-xs text-muted mt-1">Current Streak</p>
            </div>
            <div class="glass-card card-shade-2 p-4 text-center">
                ${icons.award}
                <p class="text-2xl font-bold">${longestStreak}</p>
                <p class="text-xs text-muted mt-1">Best Streak</p>
            </div>
            <div class="glass-card card-shade-2 p-4 text-center">
                ${icons.book}
                <p class="text-2xl font-bold">${wQuestions}</p>
                <p class="text-xs text-muted mt-1">This Week</p>
            </div>
            <div class="glass-card card-shade-2 p-4 text-center">
                ${icons.cal}
                <p class="text-2xl font-bold">${wActive}/7</p>
                <p class="text-xs text-muted mt-1">Active (Week)</p>
            </div>
            <div class="glass-card card-shade-2 p-4 text-center">
                ${icons.trendUp}
                <p class="text-2xl font-bold">${mQuestions}</p>
                <p class="text-xs text-muted mt-1">This Month</p>
            </div>
            <div class="glass-card card-shade-2 p-4 text-center">
                ${icons.target}
                <p class="text-2xl font-bold">${mActive}/30</p>
                <p class="text-xs text-muted mt-1">Active (Month)</p>
            </div>
        </div>

        <!-- Heatmap -->
        <div class="glass-card card-shade-3 p-6 mb-8">
            <h3 class="text-lg font-semibold mb-4 opacity-90">📊 Activity Heatmap (Last 365 Days)</h3>

            <div class="overflow-x-auto pb-2 scrollbar-thin">
                <!-- Month labels row aligned with day columns -->
                <div class="flex gap-1 mb-2">
                    <div class="w-4 shrink-0 mr-2"></div>
                    ${monthHeaderColsHtml}
                </div>

                <!-- Days of week + heatmap grid -->
                <div class="flex gap-1">
                    <div class="flex flex-col gap-1 mr-2 text-[10px] text-muted font-mono shrink-0">
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
            </div>

            <div class="flex items-center gap-2 mt-4 text-xs text-muted">
                <span>Less</span>
                <div class="w-3 h-3 rounded-[2px] heatmap-cell-0"></div>
                <div class="w-3 h-3 rounded-[2px] heatmap-cell-1"></div>
                <div class="w-3 h-3 rounded-[2px] heatmap-cell-2"></div>
                <div class="w-3 h-3 rounded-[2px] heatmap-cell-3"></div>
                <div class="w-3 h-3 rounded-[2px] heatmap-cell-4"></div>
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

    // Habit Focus Button Toggle
    document.getElementById('habit-focus-toggle-btn')?.addEventListener('click', () => {
      window.focusModeEngine?.toggle();
    });

    // Rename Checklist Title Inline
    const titleInput = document.getElementById('input-checklist-title');
    if (titleInput) {
      titleInput.addEventListener('change', (e) => {
        const val = e.target.value.trim() || 'Daily Study Plan';
        window.AppState.update({
          dailyTargets: {
            ...dailyTargets,
            checklistTitle: val,
            dailyChecklistTitle: val
          }
        });
      });
    }

    // Toggle Goal Completion (Big Green Tick button click)
    container.querySelectorAll('.goal-check-toggle').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const goalId = btn.getAttribute('data-goal-id');
        const updatedList = evaluatedGoals.map(g => {
          if (g.id === goalId) {
            return { ...g, completed: !g.completed };
          }
          return g;
        });
        window.AppState.update({
          dailyTargets: {
            ...dailyTargets,
            dailyChecklist: updatedList
          }
        });
      });
    });

    // Delete Checklist Goal
    container.querySelectorAll('.btn-delete-checklist-goal').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const goalId = btn.getAttribute('data-goal-id');
        const updatedList = evaluatedGoals.filter(g => g.id !== goalId);
        window.AppState.update({
          dailyTargets: {
            ...dailyTargets,
            dailyChecklist: updatedList
          }
        });
      });
    });

    // Click to Expand Analytics Drawer
    container.querySelectorAll('.checklist-card').forEach(card => {
      const header = card.querySelector('.checklist-card-header');
      header?.addEventListener('click', (e) => {
        if (e.target.closest('.goal-check-toggle') || e.target.closest('.btn-delete-checklist-goal')) {
          return;
        }
        const goalId = card.getAttribute('data-goal-id');
        if (_expandedGoalsMap.has(goalId)) {
          _expandedGoalsMap.delete(goalId);
        } else {
          _expandedGoalsMap.add(goalId);
        }
        initHabits();
      });
    });

    // Add Goal Modal Trigger
    document.getElementById('btn-add-checklist-goal')?.addEventListener('click', () => {
      openChecklistGoalBuilderModal(dailyTargets, evaluatedGoals);
    });
}

function openChecklistGoalBuilderModal(dailyTargets, currentGoals) {
  let modal = document.getElementById('goal-builder-modal');
  if (modal) modal.remove();

  modal = document.createElement('div');
  modal.id = 'goal-builder-modal';
  modal.className = 'modal-backdrop fade-enter';
  modal.style.zIndex = '250';
  modal.innerHTML = `
    <div class="glass-modal w-full max-w-lg text-left p-6 rounded-2xl border border-sky-500/30 shadow-2xl">
      <div class="flex items-center justify-between pb-3 mb-4 border-b border-gray-700/50">
        <h3 class="text-lg font-bold text-white flex items-center gap-2">
          <span>🎯</span> Create Goal for Daily Checklist
        </h3>
        <button type="button" id="btn-close-goal-builder" class="text-slate-400 hover:text-white bg-transparent border-none text-base cursor-pointer">✕</button>
      </div>

      <div class="space-y-4 text-xs">
        <div>
          <label class="block text-slate-300 font-semibold mb-1">Goal Metric Type:</label>
          <select id="gb-metric-type" class="w-full glass-select rounded-xl px-3 py-2 text-xs">
            <option value="mcq_count">Attempt N Questions (MCQ Count)</option>
            <option value="correct_count">Get N MCQs Correct</option>
            <option value="accuracy_pct">Achieve Accuracy >= X% (with min 5 questions)</option>
            <option value="focus_minutes">Focus / Study Mode Minutes</option>
            <option value="manual">Custom Manual Checkbox Task</option>
          </select>
        </div>

        <div id="gb-scope-container">
          <label class="block text-slate-300 font-semibold mb-1">Target Filter Scope:</label>
          <div class="grid grid-cols-4 gap-2 mb-2">
            <button type="button" class="gb-scope-btn active py-1.5 px-2 rounded-lg border text-center font-bold text-[11px] bg-sky-500/20 text-sky-300 border-sky-500/40" data-scope="all">All</button>
            <button type="button" class="gb-scope-btn py-1.5 px-2 rounded-lg border text-center font-bold text-[11px] glass-panel border-gray-700 text-slate-300" data-scope="subject">Subject</button>
            <button type="button" class="gb-scope-btn py-1.5 px-2 rounded-lg border text-center font-bold text-[11px] glass-panel border-gray-700 text-slate-300" data-scope="topic">CSV Topic</button>
            <button type="button" class="gb-scope-btn py-1.5 px-2 rounded-lg border text-center font-bold text-[11px] glass-panel border-gray-700 text-slate-300" data-scope="keyword">Keyword</button>
          </div>
          <input type="text" id="gb-scope-val" placeholder="Subject / topic / keyword (e.g. Pathology, Amyloidosis, Heart)..." class="w-full glass-input rounded-xl px-3 py-2 text-xs hidden" />
        </div>

        <div class="grid grid-cols-2 gap-3">
          <div>
            <label id="gb-target-label" class="block text-slate-300 font-semibold mb-1">Target Number:</label>
            <input type="number" id="gb-target-val" value="20" min="1" class="w-full glass-input rounded-xl px-3 py-2 text-xs font-mono" />
          </div>
          <div>
            <label class="block text-slate-300 font-semibold mb-1">Custom Title (Optional):</label>
            <input type="text" id="gb-title" placeholder="e.g. Anatomy Arm 20 Qs" class="w-full glass-input rounded-xl px-3 py-2 text-xs" />
          </div>
        </div>
      </div>

      <div class="flex items-center justify-end gap-3 mt-6 pt-3 border-t border-gray-700/50">
        <button type="button" id="btn-cancel-goal-builder" class="secondary-btn px-4 py-2 rounded-xl text-xs font-medium cursor-pointer">Cancel</button>
        <button type="button" id="btn-save-goal-builder" class="primary-btn px-5 py-2 rounded-xl text-xs font-bold cursor-pointer">Add to Checklist</button>
      </div>
    </div>
  `;

  document.body.appendChild(modal);

  let selectedScope = 'all';
  const metricSelect = modal.querySelector('#gb-metric-type');
  const scopeContainer = modal.querySelector('#gb-scope-container');
  const scopeValInput = modal.querySelector('#gb-scope-val');
  const targetLabel = modal.querySelector('#gb-target-label');
  const targetValInput = modal.querySelector('#gb-target-val');

  metricSelect.addEventListener('change', () => {
    const val = metricSelect.value;
    if (val === 'focus_minutes') {
      scopeContainer.classList.add('hidden');
      targetLabel.textContent = 'Minutes to Study:';
      targetValInput.value = '30';
    } else if (val === 'manual') {
      scopeContainer.classList.add('hidden');
      targetLabel.textContent = 'Checkmark Target:';
      targetValInput.value = '1';
    } else {
      scopeContainer.classList.remove('hidden');
      if (val === 'accuracy_pct') {
        targetLabel.textContent = 'Target Accuracy %:';
        targetValInput.value = '65';
      } else {
        targetLabel.textContent = 'Target Questions:';
        targetValInput.value = '20';
      }
    }
  });

  modal.querySelectorAll('.gb-scope-btn').forEach(b => {
    b.addEventListener('click', () => {
      modal.querySelectorAll('.gb-scope-btn').forEach(x => {
        x.className = 'gb-scope-btn py-1.5 px-2 rounded-lg border text-center font-bold text-[11px] glass-panel border-gray-700 text-slate-300';
      });
      b.className = 'gb-scope-btn active py-1.5 px-2 rounded-lg border text-center font-bold text-[11px] bg-sky-500/20 text-sky-300 border-sky-500/40';
      selectedScope = b.getAttribute('data-scope');
      if (selectedScope === 'all') {
        scopeValInput.classList.add('hidden');
      } else {
        scopeValInput.classList.remove('hidden');
        scopeValInput.placeholder = `Enter ${selectedScope} name or filter pattern...`;
        scopeValInput.focus();
      }
    });
  });

  const closeModal = () => modal.remove();
  modal.querySelector('#btn-close-goal-builder').addEventListener('click', closeModal);
  modal.querySelector('#btn-cancel-goal-builder').addEventListener('click', closeModal);

  modal.querySelector('#btn-save-goal-builder').addEventListener('click', () => {
    const mType = metricSelect.value;
    const tVal = Number(targetValInput.value) || 1;
    const sVal = (selectedScope === 'all' || mType === 'focus_minutes' || mType === 'manual') ? '' : scopeValInput.value.trim();
    const customTitle = modal.querySelector('#gb-title').value.trim();

    let defTitle = '';
    if (mType === 'focus_minutes') {
      defTitle = `Focus Mode: ${tVal} Minutes`;
    } else if (mType === 'accuracy_pct') {
      defTitle = `Accuracy >= ${tVal}%${sVal ? ` (${selectedScope}: ${sVal})` : ''}`;
    } else if (mType === 'correct_count') {
      defTitle = `Get ${tVal} Correct${sVal ? ` (${selectedScope}: ${sVal})` : ''}`;
    } else if (mType === 'manual') {
      defTitle = customTitle || 'Complete reading assignment';
    } else {
      defTitle = `Attempt ${tVal} MCQs${sVal ? ` (${selectedScope}: ${sVal})` : ''}`;
    }

    const newGoal = {
      id: `goal_${Date.now()}`,
      title: customTitle || defTitle,
      metricType: mType,
      targetValue: tVal,
      currentValue: 0,
      scopeType: selectedScope,
      scopeValue: sVal,
      minQuestions: 5,
      completed: false
    };

    closeModal();
    const updatedGoals = [...currentGoals, newGoal];
    window.AppState.update({
      dailyTargets: {
        ...dailyTargets,
        dailyChecklist: updatedGoals
      }
    });
  });
}

window.initHabits = initHabits;

// Bind to event lifecycle
document.addEventListener('stateUpdated', initHabits);
window.addEventListener('focusModeUpdated', (e) => {
  const dot = document.getElementById('habit-focus-dot');
  const timer = document.getElementById('habit-focus-timer-text');
  const btn = document.getElementById('habit-focus-toggle-btn');
  const kpiFocus = document.getElementById('kpi-focus-time');
  const kpiCombined = document.getElementById('kpi-combined-time');
  const studyFill = document.getElementById('kpi-study-progress-fill');
  const studyBadge = document.getElementById('kpi-study-pct-badge');

  if (timer) timer.textContent = FocusModeEngine.formatHMS(e.detail.todayFocusSeconds);
  if (kpiFocus) kpiFocus.textContent = FocusModeEngine.formatHMS(e.detail.todayFocusSeconds);

  if (kpiCombined && window.AppState) {
    const st = window.AppState.get() || {};
    const dt = st.dailyTargets || {};
    const hT = Math.max(0.1, Number(dt.hoursTarget || 2));
    const targetSec = Math.round(hT * 3600);
    const mcqSec = window._lastMcqSeconds || 0;
    const totalSec = Math.floor(e.detail.todayFocusSeconds + mcqSec);
    kpiCombined.textContent = FocusModeEngine.formatHMS(totalSec);
    const pct = Math.min(100, Math.round((totalSec / targetSec) * 100));
    if (studyFill) studyFill.style.width = `${pct}%`;
    if (studyBadge) studyBadge.textContent = `${pct}%`;
  }

  if (dot) {
    dot.className = e.detail.isRunning
      ? 'w-2.5 h-2.5 rounded-full bg-emerald-400 animate-ping'
      : e.detail.isPausedByTimeout
      ? 'w-2.5 h-2.5 rounded-full bg-amber-400'
      : 'w-2.5 h-2.5 rounded-full bg-slate-500';
  }
  if (btn) {
    btn.textContent = e.detail.isRunning ? '⏸ Pause Focus' : '▶ Start Focus';
    btn.className = e.detail.isRunning
      ? 'px-4 py-2 rounded-xl font-bold text-xs shadow-lg transition-all bg-amber-500 hover:bg-amber-400 text-slate-950'
      : 'px-4 py-2 rounded-xl font-bold text-xs shadow-lg transition-all bg-emerald-500 hover:bg-emerald-400 text-slate-950';
  }
});

document.addEventListener('DOMContentLoaded', () => {
    const navBtns = document.querySelectorAll('.nav-btn');
    navBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            if (e.currentTarget.getAttribute('data-target') === 'habit-tracker-view') {
                initHabits();
            }
        });
    });
    initHabits();
});

// --- Phase 7 Local IANA Timezone Date Key Helper for Study Streaks ---
(function () {
    window.getLocalHabitDateString = function (dateObj) {
        const d = dateObj instanceof Date ? dateObj : new Date();
        const tz = (Intl && Intl.DateTimeFormat) ? Intl.DateTimeFormat().resolvedOptions().timeZone : undefined;
        try {
            return d.toLocaleDateString("en-CA", tz ? { timeZone: tz } : undefined); // YYYY-MM-DD in local TZ
        } catch (_err) {
            const year = d.getFullYear();
            const month = String(d.getMonth() + 1).padStart(2, "0");
            const day = String(d.getDate()).padStart(2, "0");
            return `${year}-${month}-${day}`;
        }
    };
})();
