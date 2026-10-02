// Shared Literal Types for strict compiler safety
export type OptionKey = 'A' | 'B' | 'C' | 'D';
export type QuizMode = 'test' | 'learn' | 'target';
export type ExpertiseLevel = 'beginner' | 'intermediate' | 'advanced' | 'expert';
export type ExpertiseTrend = 'improving' | 'stable' | 'declining';
export type AnkiStatus = 'new' | 'learning' | 'review' | 'mastered';

// Semantic Aliases for improved readability and intent
export type DateString = string; // Format: YYYY-MM-DD
export type ISOString = string;  // Format: Full ISO 8601 Timestamp

// Core question from CSV
export interface QuizQuestion {
  readonly id: string;
  topic_name: string;
  question: string;
  option_a: string;
  option_b: string;
  option_c: string;
  option_d: string;
  correct_answer: OptionKey;
  explanation: string;
}

// Answer record
export interface AnswerRecord {
  readonly questionId: string;
  selected: OptionKey | ''; // Empty string accounts for timeouts
  isCorrect: boolean;
  readonly timestamp: number;
  timeSpentMs: number;
  timedOut: boolean;
}

// Test session result
export interface TestResult {
  readonly id: string;
  subjectName: string;
  readonly date: ISOString;
  readonly timestamp: number;
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
  mode: QuizMode;
}

// Spaced repetition data per question
export interface AnkiCard {
  readonly questionId: string;
  subjectName: string;
  topic: string;
  interval: number;
  easeFactor: number;
  repetitions: number;
  nextReviewDate: DateString;
  lastReviewDate: DateString;
  totalAttempts: number;
  correctAttempts: number;
  status: AnkiStatus;
}

// Subject tracking
export interface SubjectData {
  name: string;
  fileName: string;
  questions: QuizQuestion[];
  topics: string[];
  dateAdded: ISOString;
  lastAccessed: ISOString;
  totalAttempts: number;
}

// Habit tracker entry
export interface HabitEntry {
  readonly date: DateString;
  questionsAnswered: number;
  correctAnswers: number;
  subjects: string[];
  sessionsCount: number;
  timeSpentSeconds: number;
  hourlyStats: HourlyStats[];
}

// Hourly breakdown
export interface HourlyStats {
  readonly hour: number; // 0-23
  questionsAnswered: number;
  correctAnswers: number;
  timeSpentSeconds: number;
}

// Review queue item
export interface ReviewQueueItem {
  readonly id: string;
  readonly questionId: string;
  subjectName: string;
  topic: string;
  question: string;
  correctAnswer: OptionKey;
  userAnswer: OptionKey | '';
  explanation: string;
  options: Record<Lowercase<OptionKey>, string>;
  readonly dateAdded: ISOString;
  readonly addedTimestamp: number;
  reviewed: boolean;
}

// Daily targets
export interface DailyTargets {
  questionsTarget: number;
  reviewTarget: number;
  correctTarget: number;
  streakTarget: number;
}

// Rank simulation data point
export interface RankDataPoint {
  readonly marks: number;
  readonly rank: number;
}

// Marks range band
export interface RankBand {
  readonly marksMin: number;
  readonly marksMax: number;
  readonly rankMin: number;
  readonly rankMax: number;
  readonly label: string;
  readonly outlook: string;
}

// Rank simulation config per exam
export interface RankSimConfig {
  examName: string;
  totalQuestions: number;
  marksPerCorrect: number;
  negativePerWrong: number;
  maxMarks: number;
  dataPoints: RankDataPoint[];
  bands: RankBand[];
}

// Rank simulation result
export interface SimulatedRank {
  projectedMarks: number;
  estimatedRank: number;
  percentile: string;
  nearestAbove: RankDataPoint | null;
  nearestBelow: RankDataPoint | null;
  currentBand: RankBand | null;
  nextBand: RankBand | null;
  marksToNextBand: number;
}

// Expertise tracker per topic
export interface TopicExpertise {
  topic: string;
  subject: string;
  totalQuestions: number;
  attempted: number;
  correct: number;
  accuracy: number;
  level: ExpertiseLevel;
  trend: ExpertiseTrend;
  lastAttemptDate: DateString;
}

// Overall app state persisted in JSON
export interface AppPersistence {
  subjects: SubjectData[];
  testResults: TestResult[];
  ankiCards: AnkiCard[];
  habitLog: HabitEntry[];
  topicExpertise: Record<string, TopicExpertise>;
  reviewQueue: ReviewQueueItem[];
  dailyTargets: DailyTargets;
  rankSimConfigs: RankSimConfig[];
  streakDays: number;
  longestStreak: number;
  totalQuestionsEver: number;
  totalReviewedEver: number;
  lastActiveDate: DateString;
  reviewQueueLimit: number;
  processingQueueLimit: number;
}

// Quiz configuration for a session
export interface QuizConfig {
  subjectName: string;
  topicFilter: string;
  questionOrder: 'sequential' | 'random';
  questionCount: number;
  timeLimitMinutes: number;
  questionTimeoutMinutes: number;
  mode: QuizMode;
  useSpacedRepetition: boolean;
}

// Active quiz state
export interface ActiveQuiz {
  config: QuizConfig;
  questions: QuizQuestion[];
  currentIndex: number;
  answers: Record<string, AnswerRecord>;
  readonly startTime: number;
  questionStartTime: number;
  timeRemainingSeconds: number | null;
  questionTimeRemainingSeconds: number | null;
  isCompleted: boolean;
  isPaused: boolean;
  score: number;
  correctCount: number;
  wrongCount: number;
  targetCorrect: number | null;
}
