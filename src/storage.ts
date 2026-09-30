import type { AppPersistence, TopicExpertise, AnkiCard, ReviewQueueItem, HourlyStats, RankSimConfig, SimulatedRank } from './types';

const STORAGE_KEY = 'quiz_master_pro_data';

export function getDefaultPersistence(): AppPersistence {
  return {
    subjects: [],
    testResults: [],
    ankiCards: [],
    habitLog: [],
    topicExpertise: [],
    reviewQueue: [],
    dailyTargets: {
      questionsTarget: 20,
      reviewTarget: 5,
      correctTarget: 15,
      streakTarget: 7,
    },
    streakDays: 0,
    longestStreak: 0,
    totalQuestionsEver: 0,
    totalReviewedEver: 0,
    lastActiveDate: '',
    rankSimConfigs: [
      {
        examName: 'NEET PG (200Q, +4/−1)',
        totalQuestions: 200,
        marksPerCorrect: 4,
        negativePerWrong: 1,
        maxMarks: 800,
        dataPoints: [
          { marks: 750, rank: 10 },
          { marks: 700, rank: 25 },
          { marks: 670, rank: 110 },
          { marks: 640, rank: 500 },
          { marks: 610, rank: 1300 },
          { marks: 580, rank: 3500 },
          { marks: 550, rank: 7000 },
          { marks: 520, rank: 13000 },
          { marks: 490, rank: 20000 },
          { marks: 460, rank: 28000 },
          { marks: 400, rank: 45000 },
          { marks: 300, rank: 65000 },
        ],
        bands: [
          { marksMin: 700, marksMax: 800, rankMin: 1,     rankMax: 25,    label: 'Top 25',     outlook: 'Any speciality at PGIMER, VMMC; top institutes country-wide' },
          { marksMin: 670, marksMax: 699, rankMin: 26,    rankMax: 110,   label: 'Top 110',    outlook: 'Radiodiagnosis, Dermatology, General Medicine at PGIMER' },
          { marksMin: 640, marksMax: 669, rankMin: 111,   rankMax: 500,   label: 'Top 500',    outlook: 'Top clinical specialities at central govt colleges' },
          { marksMin: 610, marksMax: 639, rankMin: 501,   rankMax: 1300,  label: 'Top 1,300',  outlook: 'Competitive clinical branches at leading government colleges' },
          { marksMin: 580, marksMax: 609, rankMin: 1301,  rankMax: 3500,  label: 'Top 3,500',  outlook: 'General Medicine, Paediatrics at reputed government colleges' },
          { marksMin: 550, marksMax: 579, rankMin: 3501,  rankMax: 7000,  label: 'Top 7,000',  outlook: 'Obstetrics, Orthopaedics, ENT at government colleges in major states' },
          { marksMin: 520, marksMax: 549, rankMin: 7001,  rankMax: 13000, label: 'Top 13,000', outlook: 'Anaesthesiology, Psychiatry, Ophthalmology; some state quota clinical seats' },
          { marksMin: 490, marksMax: 519, rankMin: 13001, rankMax: 20000, label: 'Top 20,000', outlook: 'DNB seats; non-clinical branches at govt colleges; private college clinical seats' },
          { marksMin: 460, marksMax: 489, rankMin: 20001, rankMax: 28000, label: 'Top 28,000', outlook: 'Non-clinical MD (Pathology, Microbiology, Community Medicine); private colleges' },
          { marksMin: 400, marksMax: 459, rankMin: 28001, rankMax: 45000, label: 'Top 45,000', outlook: 'Private college options; state quota seats in less competitive specialities' },
          { marksMin: 300, marksMax: 399, rankMin: 45001, rankMax: 65000, label: '65,000+',    outlook: 'Qualifying range; limited options; PG Diploma seats; private colleges with high fees' },
        ],
      },
    ],
    reviewQueueLimit: 50,
  };
}

export function loadPersistence(): AppPersistence {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return getDefaultPersistence();
    const data = JSON.parse(raw) as AppPersistence;
    return { ...getDefaultPersistence(), ...data };
  } catch {
    return getDefaultPersistence();
  }
}

export function savePersistence(data: AppPersistence): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch (e) {
    console.error('Failed to save data:', e);
  }
}

export function exportPersistenceJSON(): string {
  const data = loadPersistence();
  return JSON.stringify(data, null, 2);
}

export function importPersistenceJSON(json: string): AppPersistence | null {
  try {
    const data = JSON.parse(json) as AppPersistence;
    savePersistence(data);
    return data;
  } catch {
    return null;
  }
}

// Habit tracking helpers
export function getTodayString(): string {
  return new Date().toISOString().split('T')[0];
}

export function getCurrentHour(): number {
  return new Date().getHours();
}

export function updateHabitLog(
  persistence: AppPersistence,
  questionsAnswered: number,
  correctAnswers: number,
  subject: string,
  timeSpent: number
): AppPersistence {
  const today = getTodayString();
  const hour = getCurrentHour();
  const existing = persistence.habitLog.find(h => h.date === today);

  if (existing) {
    existing.questionsAnswered += questionsAnswered;
    existing.correctAnswers += correctAnswers;
    if (!existing.subjects.includes(subject)) {
      existing.subjects.push(subject);
    }
    existing.sessionsCount += 1;
    existing.timeSpentSeconds += timeSpent;

    // Update hourly stats
    if (!existing.hourlyStats) {
      existing.hourlyStats = [];
    }
    const hourlyEntry = existing.hourlyStats.find(h => h.hour === hour);
    if (hourlyEntry) {
      hourlyEntry.questionsAnswered += questionsAnswered;
      hourlyEntry.correctAnswers += correctAnswers;
      hourlyEntry.timeSpentSeconds += timeSpent;
    } else {
      existing.hourlyStats.push({
        hour,
        questionsAnswered,
        correctAnswers,
        timeSpentSeconds: timeSpent,
      });
    }
  } else {
    persistence.habitLog.push({
      date: today,
      questionsAnswered,
      correctAnswers,
      subjects: [subject],
      sessionsCount: 1,
      timeSpentSeconds: timeSpent,
      hourlyStats: [{
        hour,
        questionsAnswered,
        correctAnswers,
        timeSpentSeconds: timeSpent,
      }],
    });
  }

  // Update streak
  const sortedDates = persistence.habitLog
    .map(h => h.date)
    .sort()
    .reverse();

  let streak = 0;
  const now = new Date();
  for (let i = 0; i < 365; i++) {
    const checkDate = new Date(now);
    checkDate.setDate(checkDate.getDate() - i);
    const dateStr = checkDate.toISOString().split('T')[0];
    if (sortedDates.includes(dateStr)) {
      streak++;
    } else if (i > 0) {
      break;
    }
  }

  persistence.streakDays = streak;
  persistence.longestStreak = Math.max(persistence.longestStreak, streak);
  persistence.totalQuestionsEver += questionsAnswered;
  persistence.lastActiveDate = today;

  return { ...persistence };
}

// Review queue helpers
export function addToReviewQueue(
  persistence: AppPersistence,
  item: Omit<ReviewQueueItem, 'id' | 'dateAdded' | 'reviewed'>
): AppPersistence {
  // Check if already in queue
  const exists = persistence.reviewQueue.some(
    r => r.questionId === item.questionId && r.subjectName === item.subjectName
  );
  if (exists) return persistence;

  const newItem: ReviewQueueItem = {
    ...item,
    id: `review_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
    dateAdded: new Date().toISOString(),
    reviewed: false,
  };

  // Add to queue, respecting limit (remove oldest if needed)
  let queue = [...persistence.reviewQueue, newItem];
  if (queue.length > persistence.reviewQueueLimit) {
    // Remove oldest unreviewed items first, then oldest reviewed
    queue.sort((a, b) => {
      if (a.reviewed !== b.reviewed) return a.reviewed ? -1 : 1;
      return new Date(a.dateAdded).getTime() - new Date(b.dateAdded).getTime();
    });
    queue = queue.slice(-persistence.reviewQueueLimit);
  }

  return { ...persistence, reviewQueue: queue };
}

export function markReviewItemDone(persistence: AppPersistence, itemId: string): AppPersistence {
  const item = persistence.reviewQueue.find(r => r.id === itemId);
  const alreadyReviewed = item?.reviewed ?? false;
  return {
    ...persistence,
    totalReviewedEver: persistence.totalReviewedEver + (alreadyReviewed ? 0 : 1),
    reviewQueue: persistence.reviewQueue.map(r =>
      r.id === itemId ? { ...r, reviewed: true } : r
    ),
  };
}

export function removeReviewItem(persistence: AppPersistence, itemId: string): AppPersistence {
  const item = persistence.reviewQueue.find(r => r.id === itemId);
  const wasUnreviewed = item && !item.reviewed;
  return {
    ...persistence,
    // Count it as reviewed when removed
    totalReviewedEver: persistence.totalReviewedEver + (wasUnreviewed ? 1 : 0),
    reviewQueue: persistence.reviewQueue.filter(r => r.id !== itemId),
  };
}

export function clearAllReviewQueue(persistence: AppPersistence): AppPersistence {
  // Count all unreviewed items as reviewed before clearing
  const unreviewedCount = persistence.reviewQueue.filter(r => !r.reviewed).length;
  return {
    ...persistence,
    totalReviewedEver: persistence.totalReviewedEver + unreviewedCount,
    reviewQueue: [],
  };
}

// Anki spaced repetition helpers
export function getAnkiCard(persistence: AppPersistence, questionId: string): AnkiCard | undefined {
  return persistence.ankiCards.find(c => c.questionId === questionId);
}

export function updateAnkiCard(
  persistence: AppPersistence,
  questionId: string,
  subjectName: string,
  topic: string,
  isCorrect: boolean
): AppPersistence {
  let card = persistence.ankiCards.find(c => c.questionId === questionId);
  const today = getTodayString();

  if (!card) {
    card = {
      questionId,
      subjectName,
      topic,
      interval: 1,
      easeFactor: 2.5,
      repetitions: 0,
      nextReviewDate: today,
      lastReviewDate: today,
      totalAttempts: 0,
      correctAttempts: 0,
      status: 'new',
    };
    persistence.ankiCards.push(card);
  }

  card.totalAttempts++;
  card.lastReviewDate = today;

  if (isCorrect) {
    card.correctAttempts++;
    card.repetitions++;
    if (card.repetitions === 1) {
      card.interval = 1;
    } else if (card.repetitions === 2) {
      card.interval = 3;
    } else {
      card.interval = Math.round(card.interval * card.easeFactor);
    }
    card.easeFactor = Math.max(1.3, card.easeFactor + 0.1);

    if (card.repetitions >= 5 && card.correctAttempts / card.totalAttempts >= 0.8) {
      card.status = 'mastered';
    } else if (card.repetitions >= 2) {
      card.status = 'review';
    } else {
      card.status = 'learning';
    }
  } else {
    card.repetitions = 0;
    card.interval = 1;
    card.easeFactor = Math.max(1.3, card.easeFactor - 0.2);
    card.status = 'learning';
  }

  const nextDate = new Date();
  nextDate.setDate(nextDate.getDate() + card.interval);
  card.nextReviewDate = nextDate.toISOString().split('T')[0];

  return { ...persistence };
}

// Get questions due for Anki review
export function getAnkiDueQuestions(
  persistence: AppPersistence,
  subjectName: string,
  allQuestionIds: string[]
): { dueForReview: string[]; newQuestions: string[] } {
  const today = getTodayString();

  const dueForReview: string[] = [];
  const reviewedIds = new Set<string>();

  for (const card of persistence.ankiCards) {
    if (card.subjectName === subjectName) {
      reviewedIds.add(card.questionId);
      if (card.status !== 'mastered' && card.nextReviewDate <= today) {
        dueForReview.push(card.questionId);
      }
    }
  }

  const newQuestions = allQuestionIds.filter(id => !reviewedIds.has(id));

  return { dueForReview, newQuestions };
}

// Topic expertise tracking
export function updateTopicExpertise(
  persistence: AppPersistence,
  topic: string,
  subject: string,
  totalInTopic: number,
  attempted: number,
  correct: number
): AppPersistence {
  let expertise = persistence.topicExpertise.find(
    e => e.topic === topic && e.subject === subject
  );

  const accuracy = attempted > 0 ? (correct / attempted) * 100 : 0;
  const level: TopicExpertise['level'] =
    accuracy >= 90 ? 'expert' :
    accuracy >= 70 ? 'advanced' :
    accuracy >= 50 ? 'intermediate' : 'beginner';

  if (!expertise) {
    expertise = {
      topic,
      subject,
      totalQuestions: totalInTopic,
      attempted,
      correct,
      accuracy,
      level,
      trend: 'stable',
      lastAttemptDate: getTodayString(),
    };
    persistence.topicExpertise.push(expertise);
  } else {
    const prevAccuracy = expertise.accuracy;
    expertise.attempted = Math.max(expertise.attempted, attempted);
    expertise.correct = Math.max(expertise.correct, correct);
    expertise.accuracy = accuracy;
    expertise.level = level;
    expertise.totalQuestions = totalInTopic;
    expertise.lastAttemptDate = getTodayString();
    expertise.trend = accuracy > prevAccuracy ? 'improving' : accuracy < prevAccuracy ? 'declining' : 'stable';
  }

  return { ...persistence };
}

// Get yesterday's date
export function getYesterdayString(): string {
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  return yesterday.toISOString().split('T')[0];
}

// Get stats for comparison
export function getDayStats(persistence: AppPersistence, date: string) {
  const entry = persistence.habitLog.find(h => h.date === date);
  if (!entry) {
    return {
      questionsAnswered: 0,
      correctAnswers: 0,
      sessionsCount: 0,
      timeSpentSeconds: 0,
      accuracy: 0,
      hourlyStats: [] as HourlyStats[],
    };
  }
  return {
    ...entry,
    accuracy: entry.questionsAnswered > 0
      ? (entry.correctAnswers / entry.questionsAnswered) * 100
      : 0,
    hourlyStats: entry.hourlyStats || [],
  };
}

// ── Rank Simulation ──

function findBand(config: RankSimConfig, projectedMarks: number) {
  const bands = config.bands || [];
  const current = bands.find(b => projectedMarks >= b.marksMin && projectedMarks <= b.marksMax) ?? null;

  // Next better band = first band whose marksMin is above projectedMarks
  const sortedBands = [...bands].sort((a, b) => a.marksMin - b.marksMin);
  const nextBetter = sortedBands.find(b => b.marksMin > projectedMarks) ?? null;
  const marksToNext = nextBetter ? nextBetter.marksMin - projectedMarks : 0;

  return { currentBand: current, nextBand: nextBetter, marksToNextBand: marksToNext };
}

/**
 * Given a test result (correct, wrong, unanswered from N questions scored +4/-1),
 * project the marks onto an exam's total-question scale and interpolate a rank.
 */
export function simulateRank(
  config: RankSimConfig,
  testCorrect: number,
  testWrong: number,
  testTotal: number,
): SimulatedRank {
  const rawMarks = testCorrect * config.marksPerCorrect - testWrong * config.negativePerWrong;
  const scaleFactor = testTotal > 0 ? config.totalQuestions / testTotal : 1;
  const projectedMarks = Math.round(rawMarks * scaleFactor);

  const sorted = [...config.dataPoints].sort((a, b) => b.marks - a.marks);
  const { currentBand, nextBand, marksToNextBand } = findBand(config, projectedMarks);

  const baseSim = { currentBand, nextBand, marksToNextBand };

  if (sorted.length === 0) {
    return { projectedMarks, estimatedRank: 0, percentile: '-', nearestAbove: null, nearestBelow: null, ...baseSim };
  }

  const topRankValue = sorted[sorted.length - 1].rank;

  const exact = sorted.find(d => d.marks === projectedMarks);
  if (exact) {
    const percentile = topRankValue > 0 ? (((topRankValue - exact.rank) / topRankValue) * 100).toFixed(1) : '-';
    return { projectedMarks, estimatedRank: exact.rank, percentile, nearestAbove: null, nearestBelow: null, ...baseSim };
  }

  const above = sorted.find(d => d.marks >= projectedMarks) ?? null;
  const below = [...sorted].reverse().find(d => d.marks <= projectedMarks) ?? null;

  if (!above) {
    const best = sorted[0];
    const estRank = Math.max(1, best.rank - 50);
    const percentile = topRankValue > 0 ? (((topRankValue - estRank) / topRankValue) * 100).toFixed(1) : '-';
    return { projectedMarks, estimatedRank: estRank, percentile, nearestAbove: null, nearestBelow: best, ...baseSim };
  }
  if (!below) {
    const worst = sorted[sorted.length - 1];
    return { projectedMarks, estimatedRank: worst.rank + 10000, percentile: '0.0', nearestAbove: worst, nearestBelow: null, ...baseSim };
  }

  if (above.marks === below.marks) {
    const percentile = topRankValue > 0 ? (((topRankValue - above.rank) / topRankValue) * 100).toFixed(1) : '-';
    return { projectedMarks, estimatedRank: above.rank, percentile, nearestAbove: above, nearestBelow: below, ...baseSim };
  }

  const t = (projectedMarks - below.marks) / (above.marks - below.marks);
  const interpolatedRank = Math.max(1, Math.round(below.rank + t * (above.rank - below.rank)));
  const percentile = topRankValue > 0 ? (((topRankValue - interpolatedRank) / topRankValue) * 100).toFixed(1) : '-';

  return { projectedMarks, estimatedRank: interpolatedRank, percentile, nearestAbove: above, nearestBelow: below, ...baseSim };
}
