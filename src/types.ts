// Core question from CSV
export interface QuizQuestion {
  id: string;
  topic_name: string;
  question: string;
  option_a: string;
  option_b: string;
  option_c: string;
  option_d: string;
  correct_answer: string; // A, B, C, or D
  explanation: string;
}

// Answer record
export interface AnswerRecord {
  questionId: string;
  selected: string;
  isCorrect: boolean;
  timestamp: number;
  timeSpentMs: number; // Time spent on this question in milliseconds
  timedOut: boolean; // True if question was skipped due to timeout
}

// Test session result
export interface TestResult {
  id: string;
  subjectName: string;
  date: string;
  timestamp: number;
  topics: string[];
  totalQuestions: number;
  correctAnswers: number;
  wrongAnswers: number;
  unanswered: number;
  score: number;
  maxScore: number;
  accuracy: number;
  timeLimitSeconds: number | null;
  timeUsedSeconds: number;
  answers: AnswerRecord[];
  mode: 'test' | 'learn' | 'target';
}

// Anki-style spaced repetition data per question
export interface AnkiCard {
  questionId: string;
  subjectName: string;
  topic: string;
  interval: number; // days until next review
  easeFactor: number;
  repetitions: number;
  nextReviewDate: string; // ISO date
  lastReviewDate: string;
  totalAttempts: number;
  correctAttempts: number;
  status: 'new' | 'learning' | 'review' | 'mastered';
}

// Subject tracking
export interface SubjectData {
  name: string;
  fileName: string;
  questions: QuizQuestion[];
  topics: string[];
  dateAdded: string;
  lastAccessed: string;
  totalAttempts: number;
}

// Habit tracker entry
export interface HabitEntry {
  date: string; // YYYY-MM-DD
  questionsAnswered: number;
  correctAnswers: number;
  subjects: string[];
  sessionsCount: number;
  timeSpentSeconds: number;
  hourlyStats: HourlyStats[];
}

// Hourly breakdown
export interface HourlyStats {
  hour: number; // 0-23
  questionsAnswered: number;
  correctAnswers: number;
  timeSpentSeconds: number;
}

// Review queue item (wrong answers to review)
export interface ReviewQueueItem {
  id: string;
  questionId: string;
  subjectName: string;
  topic: string;
  question: string;
  correctAnswer: string;
  userAnswer: string;
  explanation: string;
  options: { a: string; b: string; c: string; d: string };
  dateAdded: string;
  reviewed: boolean;
}

// Daily targets
export interface DailyTargets {
  questionsTarget: number;
  reviewTarget: number; // Review queue items to clear
  correctTarget: number;
  streakTarget: number; // Days in a row
}

// Rank simulation: a single marks -> rank data point
export interface RankDataPoint {
  marks: number;
  rank: number;
}

// A marks range band with competitive outlook text
export interface RankBand {
  marksMin: number;
  marksMax: number;
  rankMin: number;
  rankMax: number;
  label: string; // e.g. "Top 25"
  outlook: string; // competitive outlook description
}

// Rank simulation config per exam
export interface RankSimConfig {
  examName: string;
  totalQuestions: number; // e.g. 200
  marksPerCorrect: number; // e.g. 4
  negativePerWrong: number; // e.g. 1 (stored as positive)
  maxMarks: number; // totalQuestions * marksPerCorrect
  dataPoints: RankDataPoint[]; // user-editable marks→rank table
  bands: RankBand[]; // competitive outlook bands
}

// Rank simulation result computed for a test
export interface SimulatedRank {
  projectedMarks: number; // scaled to exam's totalQuestions
  estimatedRank: number;
  percentile: string;
  nearestAbove: RankDataPoint | null;
  nearestBelow: RankDataPoint | null;
  currentBand: RankBand | null;
  nextBand: RankBand | null; // the better band above current
  marksToNextBand: number; // how many more marks needed
}

// Expertise tracker per topic
export interface TopicExpertise {
  topic: string;
  subject: string;
  totalQuestions: number;
  attempted: number;
  correct: number;
  accuracy: number;
  level: 'beginner' | 'intermediate' | 'advanced' | 'expert';
  trend: 'improving' | 'stable' | 'declining';
  lastAttemptDate: string;
}

// Overall app state persisted in JSON
export interface AppPersistence {
  subjects: SubjectData[];
  testResults: TestResult[];
  ankiCards: AnkiCard[];
  habitLog: HabitEntry[];
  topicExpertise: TopicExpertise[];
  reviewQueue: ReviewQueueItem[];
  dailyTargets: DailyTargets;
  rankSimConfigs: RankSimConfig[];
  streakDays: number;
  longestStreak: number;
  totalQuestionsEver: number;
  totalReviewedEver: number;
  lastActiveDate: string;
  reviewQueueLimit: number;
}

// Quiz configuration for a session
export interface QuizConfig {
  subjectName: string;
  topicFilter: string; // 'all' or specific topic
  questionOrder: 'sequential' | 'random';
  questionCount: number; // For test/learn: total questions. For target: target correct answers
  timeLimitMinutes: number; // 0 = no limit
  questionTimeoutMinutes: number; // Timeout per question (0 = no timeout, default 5)
  mode: 'test' | 'learn' | 'target'; // target = continue until X correct answers
  useSpacedRepetition: boolean; // Anki-style prioritization
}

// Active quiz state
export interface ActiveQuiz {
  config: QuizConfig;
  questions: QuizQuestion[];
  currentIndex: number;
  answers: Record<string, AnswerRecord>;
  startTime: number;
  questionStartTime: number; // When current question was shown
  timeRemainingSeconds: number | null;
  questionTimeRemainingSeconds: number | null;
  isCompleted: boolean;
  isPaused: boolean; // Paused due to timeout
  score: number;
  correctCount: number;
  wrongCount: number;
  targetCorrect: number | null; // For target mode: goal correct answers
}
