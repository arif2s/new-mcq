import { useState, useMemo, useEffect } from 'react';
import {
  TrendingUp, TrendingDown, Minus, Target, Flame, Clock,
  CheckCircle, AlertTriangle, BookOpen, Zap, Award, Settings
} from 'lucide-react';
import type { AppPersistence, DailyTargets } from '../types';
import { getTodayString, getYesterdayString, getDayStats } from '../storage';

interface HomeViewProps {
  persistence: AppPersistence;
  onNavigate: (view: string) => void;
  onUpdateTargets: (targets: DailyTargets) => void;
}

export default function HomeView({ persistence, onNavigate, onUpdateTargets }: HomeViewProps) {
  const [showTargetSettings, setShowTargetSettings] = useState(false);
  const [targets, setTargets] = useState<DailyTargets>(persistence.dailyTargets);

  // Sync local form state when settings panel is opened
  useEffect(() => {
    if (showTargetSettings) {
      setTargets(persistence.dailyTargets);
    }
  }, [showTargetSettings, persistence.dailyTargets]);

  // Memoize all heavy daily calculations and comparisons
  const stats = useMemo(() => {
    const todayStr = getTodayString();
    const yesterdayStr = getYesterdayString();

    const todayStats = getDayStats(persistence, todayStr);
    const yesterdayStats = getDayStats(persistence, yesterdayStr);

    const { reviewQueue, dailyTargets, streakDays } = persistence;
    const unreviewedCount = reviewQueue.filter(r => !r.reviewed).length;
    const reviewedToday = reviewQueue.filter(r => r.reviewed && r.dateAdded.startsWith(todayStr)).length;

    // Progress & Exceeded Calculations
    const questionsProgress = dailyTargets.questionsTarget > 0
      ? (todayStats.questionsAnswered / dailyTargets.questionsTarget) * 100
      : 100;
    const correctProgress = dailyTargets.correctTarget > 0
      ? (todayStats.correctAnswers / dailyTargets.correctTarget) * 100
      : 100;
    const reviewProgress = dailyTargets.reviewTarget > 0
      ? (reviewedToday / dailyTargets.reviewTarget) * 100
      : 100;

    const questionsTargetMet = questionsProgress >= 100;
    const correctTargetMet = correctProgress >= 100;
    const reviewTargetMet = reviewProgress >= 100;
    const allTargetsMet = questionsTargetMet && correctTargetMet && reviewTargetMet;

    const yesterdayMetQuestions = yesterdayStats.questionsAnswered >= dailyTargets.questionsTarget;
    const yesterdayMetCorrect = yesterdayStats.correctAnswers >= dailyTargets.correctTarget;

    // Comparison Helper
    const getComparison = (today: number, yesterday: number) => {
      if (yesterday === 0 && today === 0) return { trend: 'same' as const, percent: 0, text: '-' };
      if (yesterday === 0) return { trend: 'up' as const, percent: 100, text: 'New!' };
      const diff = ((today - yesterday) / yesterday) * 100;
      if (diff > 5) return { trend: 'up' as const, percent: Math.abs(diff), text: `+${Math.abs(diff).toFixed(0)}%` };
      if (diff < -5) return { trend: 'down' as const, percent: Math.abs(diff), text: `-${Math.abs(diff).toFixed(0)}%` };
      return { trend: 'same' as const, percent: 0, text: 'Same' };
    };

    // Motivation Generator
    const getMotivation = () => {
      const questionsExceeded = todayStats.questionsAnswered - dailyTargets.questionsTarget;
      if (questionsExceeded >= dailyTargets.questionsTarget) return { emoji: '🚀', text: `Incredible! You've done 2x your daily target! Consider taking a break.`, color: 'text-purple-400' };
      if (allTargetsMet && streakDays >= 30) return { emoji: '👑', text: "A month-long streak with all targets met! You're unstoppable!", color: 'text-warning-500' };
      if (streakDays >= 7 && allTargetsMet) return { emoji: '🔥', text: "You're on fire! A week-long streak and all targets crushed!", color: 'text-warning-500' };
      if (allTargetsMet) return { emoji: '🏆', text: "All daily targets completed! You're a champion! Take a well-deserved break.", color: 'text-success-500' };
      if (questionsTargetMet && !correctTargetMet) return { emoji: '📚', text: `Questions done! Focus on accuracy - ${dailyTargets.correctTarget - todayStats.correctAnswers} more correct needed.`, color: 'text-brand-400' };
      if (questionsProgress >= 75) return { emoji: '🎯', text: `Almost there! Just ${Math.max(0, dailyTargets.questionsTarget - todayStats.questionsAnswered)} more questions to hit your target!`, color: 'text-brand-400' };
      if (questionsProgress >= 50) return { emoji: '💪', text: `Halfway there! ${Math.max(0, dailyTargets.questionsTarget - todayStats.questionsAnswered)} more questions to go.`, color: 'text-brand-400' };
      if (todayStats.questionsAnswered > 0) return { emoji: '👍', text: "Great start! Keep the momentum going.", color: 'text-brand-400' };
      if (!yesterdayMetQuestions || !yesterdayMetCorrect) return { emoji: '🎯', text: "Yesterday's targets weren't met. Today is a fresh start!", color: 'text-warning-500' };
      return { emoji: '☀️', text: "Ready to learn? Start your first quiz of the day!", color: 'text-gray-400' };
    };

    // O(1) Hourly Data Generation
    const hourlyMap = new Map();
    todayStats.hourlyStats.forEach(h => hourlyMap.set(h.hour, h));

    const hourlyData = Array.from({ length: 24 }, (_, i) => {
      const hourStat = hourlyMap.get(i);
      return {
        hour: i,
        questions: hourStat?.questionsAnswered || 0,
        time: hourStat?.timeSpentSeconds || 0,
      };
    });

    return {
      todayStats,
      yesterdayStats,
      unreviewedCount,
      reviewedToday,
      questionsProgress,
      correctProgress,
      reviewProgress,
      questionsExceeded: todayStats.questionsAnswered - dailyTargets.questionsTarget,
      correctExceeded: todayStats.correctAnswers - dailyTargets.correctTarget,
      reviewExceeded: reviewedToday - dailyTargets.reviewTarget,
      questionsTargetMet,
      correctTargetMet,
      reviewTargetMet,
      allTargetsMet,
      yesterdayMetQuestions,
      yesterdayMetCorrect,
      comparisons: {
        questions: getComparison(todayStats.questionsAnswered, yesterdayStats.questionsAnswered),
        correct: getComparison(todayStats.correctAnswers, yesterdayStats.correctAnswers),
        accuracy: getComparison(todayStats.accuracy, yesterdayStats.accuracy),
        time: getComparison(todayStats.timeSpentSeconds, yesterdayStats.timeSpentSeconds)
      },
      motivation: getMotivation(),
      hourlyData,
      maxQuestions: Math.max(...hourlyData.map(h => h.questions), 1)
    };
  }, [persistence]);

  const { dailyTargets, streakDays, longestStreak } = persistence;

  const handleSaveTargets = () => {
    onUpdateTargets(targets);
    setShowTargetSettings(false);
  };

  const TrendIcon = ({ trend }: { trend: 'up' | 'down' | 'same' }) => {
    if (trend === 'up') return <TrendingUp size={14} className="text-success-500" />;
    if (trend === 'down') return <TrendingDown size={14} className="text-danger-500" />;
    return <Minus size={14} className="text-gray-500" />;
  };

  return (
    <div className="max-w-5xl mx-auto p-6 animate-fade-in">
      {/* Header with Motivation */}
      <div className="bg-gradient-to-r from-brand-600/20 via-purple-600/20 to-success-600/20 rounded-2xl p-6 border border-brand-500/30 mb-8">
        <div className="flex items-center gap-4 mb-4">
          <span className="text-4xl">{stats.motivation.emoji}</span>
          <div>
            <h1 className="text-2xl font-bold text-gray-100">
              {streakDays > 0 ? `Day ${streakDays} 🔥` : 'Welcome Back!'}
            </h1>
            <p className={`${stats.motivation.color}`}>{stats.motivation.text}</p>
          </div>
        </div>

        {/* Yesterday's Missed Targets Alert */}
        {(!stats.yesterdayMetQuestions || !stats.yesterdayMetCorrect) && stats.yesterdayStats.questionsAnswered > 0 && (
          <div className="bg-warning-500/10 border border-warning-500/30 rounded-lg p-3 mt-4">
            <p className="text-sm text-warning-400 flex items-center gap-2">
              <AlertTriangle size={16} />
              Yesterday: {stats.yesterdayStats.questionsAnswered}/{dailyTargets.questionsTarget} questions,
              {stats.yesterdayStats.correctAnswers}/{dailyTargets.correctTarget} correct.
              Let's do better today! 💪
            </p>
          </div>
        )}

        {/* Review Queue Alert */}
        {stats.unreviewedCount >= persistence.reviewQueueLimit * 0.8 && (
          <div className="bg-brand-500/10 border border-brand-500/30 rounded-lg p-3 mt-4">
            <p className="text-sm text-brand-400 flex items-center gap-2">
              <BookOpen size={16} />
              {stats.unreviewedCount} questions waiting for review!
              <button
                onClick={() => onNavigate('review')}
                className="underline hover:text-brand-300"
              >
                Start reviewing →
              </button>
            </p>
          </div>
        )}
      </div>

      {/* Daily Targets Progress */}
      <div className="bg-gray-800/60 rounded-2xl p-6 border border-gray-700 mb-8">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-200 flex items-center gap-2">
            <Target size={20} className="text-brand-400" />
            Today's Targets
          </h2>
          <button
            onClick={() => setShowTargetSettings(!showTargetSettings)}
            className="p-1.5 text-gray-400 hover:text-white hover:bg-gray-700 rounded-lg transition-colors"
          >
            <Settings size={18} />
          </button>
        </div>

        {/* Target Settings */}
        {showTargetSettings && (
          <div className="bg-gray-900/50 rounded-lg p-4 mb-4 border border-gray-700 animate-fade-in">
            <h3 className="text-sm font-medium text-gray-300 mb-3">Set Daily Targets</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <label className="text-xs text-gray-400 block mb-1">Questions</label>
                <input
                  type="number"
                  min={1}
                  max={200}
                  value={targets.questionsTarget}
                  onChange={(e) => setTargets({ ...targets, questionsTarget: Number(e.target.value) })}
                  className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-1.5 text-gray-200 text-sm"
                />
              </div>
              <div>
                <label className="text-xs text-gray-400 block mb-1">Correct Answers</label>
                <input
                  type="number"
                  min={1}
                  max={200}
                  value={targets.correctTarget}
                  onChange={(e) => setTargets({ ...targets, correctTarget: Number(e.target.value) })}
                  className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-1.5 text-gray-200 text-sm"
                />
              </div>
              <div>
                <label className="text-xs text-gray-400 block mb-1">Reviews</label>
                <input
                  type="number"
                  min={0}
                  max={50}
                  value={targets.reviewTarget}
                  onChange={(e) => setTargets({ ...targets, reviewTarget: Number(e.target.value) })}
                  className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-1.5 text-gray-200 text-sm"
                />
              </div>
              <div>
                <label className="text-xs text-gray-400 block mb-1">Streak Goal</label>
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={targets.streakTarget}
                  onChange={(e) => setTargets({ ...targets, streakTarget: Number(e.target.value) })}
                  className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-1.5 text-gray-200 text-sm"
                />
              </div>
            </div>
            <button
              onClick={handleSaveTargets}
              className="mt-3 px-4 py-1.5 bg-brand-600 text-white rounded-lg text-sm hover:bg-brand-500 transition-colors"
            >
              Save Targets
            </button>
          </div>
        )}

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {/* Questions Target */}
          <div className={`rounded-xl p-4 ${stats.questionsTargetMet ? 'bg-success-500/10 border border-success-500/30' : 'bg-gray-900/50'}`}>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-gray-400">Questions</span>
              {stats.questionsTargetMet && <CheckCircle size={14} className="text-success-500" />}
            </div>
            <p className="text-2xl font-bold text-gray-100">
              {stats.todayStats.questionsAnswered}
              <span className="text-gray-500 text-sm">/{dailyTargets.questionsTarget}</span>
            </p>
            {stats.questionsExceeded > 0 ? (
              <p className="text-xs text-success-500 mt-1">+{stats.questionsExceeded} extra! 🎉</p>
            ) : (
              <div className="w-full bg-gray-700 rounded-full h-1.5 mt-2">
                <div
                  className="h-1.5 rounded-full transition-all bg-brand-500"
                  style={{ width: `${Math.min(stats.questionsProgress, 100)}%` }}
                />
              </div>
            )}
          </div>

          {/* Correct Target */}
          <div className={`rounded-xl p-4 ${stats.correctTargetMet ? 'bg-success-500/10 border border-success-500/30' : 'bg-gray-900/50'}`}>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-gray-400">Correct</span>
              {stats.correctTargetMet && <CheckCircle size={14} className="text-success-500" />}
            </div>
            <p className="text-2xl font-bold text-gray-100">
              {stats.todayStats.correctAnswers}
              <span className="text-gray-500 text-sm">/{dailyTargets.correctTarget}</span>
            </p>
            {stats.correctExceeded > 0 ? (
              <p className="text-xs text-success-500 mt-1">+{stats.correctExceeded} extra! ✨</p>
            ) : (
              <div className="w-full bg-gray-700 rounded-full h-1.5 mt-2">
                <div
                  className="h-1.5 rounded-full transition-all bg-purple-500"
                  style={{ width: `${Math.min(stats.correctProgress, 100)}%` }}
                />
              </div>
            )}
          </div>

          {/* Review Target */}
          <div className={`rounded-xl p-4 ${stats.reviewTargetMet ? 'bg-success-500/10 border border-success-500/30' : 'bg-gray-900/50'}`}>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-gray-400">Reviews</span>
              {stats.reviewTargetMet && <CheckCircle size={14} className="text-success-500" />}
            </div>
            <p className="text-2xl font-bold text-gray-100">
              {stats.reviewedToday}
              <span className="text-gray-500 text-sm">/{dailyTargets.reviewTarget}</span>
            </p>
            {stats.reviewExceeded > 0 ? (
              <p className="text-xs text-success-500 mt-1">+{stats.reviewExceeded} extra! 📚</p>
            ) : (
              <div className="w-full bg-gray-700 rounded-full h-1.5 mt-2">
                <div
                  className="h-1.5 rounded-full transition-all bg-warning-500"
                  style={{ width: `${Math.min(stats.reviewProgress, 100)}%` }}
                />
              </div>
            )}
          </div>

          {/* Streak */}
          <div className={`rounded-xl p-4 ${streakDays >= dailyTargets.streakTarget ? 'bg-warning-500/10 border border-warning-500/30' : 'bg-gray-900/50'}`}>
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-gray-400">Streak</span>
              <Flame size={14} className="text-warning-500" />
            </div>
            <p className="text-2xl font-bold text-gray-100">
              {streakDays}
              <span className="text-gray-500 text-sm">/{dailyTargets.streakTarget}</span>
            </p>
            {streakDays >= dailyTargets.streakTarget ? (
              <p className="text-xs text-warning-500 mt-1">Goal reached! 🔥</p>
            ) : (
              <div className="w-full bg-gray-700 rounded-full h-1.5 mt-2">
                <div
                  className="h-1.5 rounded-full bg-warning-500 transition-all"
                  style={{ width: `${Math.min((streakDays / dailyTargets.streakTarget) * 100, 100)}%` }}
                />
              </div>
            )}
          </div>
        </div>

        {/* Congratulations banner when all targets met */}
        {stats.allTargetsMet && (
          <div className="mt-4 bg-gradient-to-r from-success-500/20 to-brand-500/20 border border-success-500/30 rounded-xl p-4 text-center animate-fade-in">
            <p className="text-lg font-semibold text-success-400">
              🎊 All Daily Targets Achieved! 🎊
            </p>
            <p className="text-sm text-gray-400 mt-1">
              Amazing work! You can take a break, or keep going to build an even bigger lead.
            </p>
          </div>
        )}
      </div>

      {/* Today vs Yesterday Comparison */}
      <div className="grid md:grid-cols-2 gap-6 mb-8">
        {/* Stats Comparison */}
        <div className="bg-gray-800/60 rounded-2xl p-6 border border-gray-700">
          <h2 className="text-lg font-semibold text-gray-200 mb-4 flex items-center gap-2">
            <Zap size={20} className="text-warning-500" />
            Today vs Yesterday
          </h2>
          <div className="space-y-4">
            {[
              { label: 'Questions', today: stats.todayStats.questionsAnswered, yesterday: stats.yesterdayStats.questionsAnswered, comparison: stats.comparisons.questions },
              { label: 'Correct', today: stats.todayStats.correctAnswers, yesterday: stats.yesterdayStats.correctAnswers, comparison: stats.comparisons.correct },
              { label: 'Accuracy', today: `${stats.todayStats.accuracy.toFixed(0)}%`, yesterday: `${stats.yesterdayStats.accuracy.toFixed(0)}%`, comparison: stats.comparisons.accuracy },
              { label: 'Time', today: `${Math.floor(stats.todayStats.timeSpentSeconds / 60)}m`, yesterday: `${Math.floor(stats.yesterdayStats.timeSpentSeconds / 60)}m`, comparison: stats.comparisons.time },
            ].map(item => (
              <div key={item.label} className="flex items-center justify-between">
                <span className="text-sm text-gray-400">{item.label}</span>
                <div className="flex items-center gap-4">
                  <span className="text-sm text-gray-500">{item.yesterday}</span>
                  <span className="text-gray-600">→</span>
                  <span className="text-sm text-gray-200 font-medium">{item.today}</span>
                  <div className="flex items-center gap-1 w-16">
                    <TrendIcon trend={item.comparison.trend} />
                    <span className={`text-xs ${
                      item.comparison.trend === 'up' ? 'text-success-500' :
                      item.comparison.trend === 'down' ? 'text-danger-500' : 'text-gray-500'
                    }`}>
                      {item.comparison.text}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Quick Stats */}
        <div className="bg-gray-800/60 rounded-2xl p-6 border border-gray-700">
          <h2 className="text-lg font-semibold text-gray-200 mb-4 flex items-center gap-2">
            <Award size={20} className="text-brand-400" />
            Your Records
          </h2>
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-gray-900/50 rounded-lg p-3">
              <p className="text-xs text-gray-400 mb-1">Best Streak</p>
              <p className="text-xl font-bold text-warning-500">{longestStreak} days</p>
            </div>
            <div className="bg-gray-900/50 rounded-lg p-3">
              <p className="text-xs text-gray-400 mb-1">Total Questions</p>
              <p className="text-xl font-bold text-brand-400">{persistence.totalQuestionsEver}</p>
            </div>
            <div className="bg-gray-900/50 rounded-lg p-3">
              <p className="text-xs text-gray-400 mb-1">Subjects</p>
              <p className="text-xl font-bold text-purple-400">{persistence.subjects.length}</p>
            </div>
            <div className="bg-gray-900/50 rounded-lg p-3">
              <p className="text-xs text-gray-400 mb-1">To Review</p>
              <p className="text-xl font-bold text-warning-500">{stats.unreviewedCount}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Hourly Activity Chart */}
      <div className="bg-gray-800/60 rounded-2xl p-6 border border-gray-700 mb-8">
        <h2 className="text-lg font-semibold text-gray-200 mb-4 flex items-center gap-2">
          <Clock size={20} className="text-brand-400" />
          Today's Activity by Hour
        </h2>

        {stats.todayStats.questionsAnswered === 0 ? (
          <div className="text-center py-8 text-gray-500">
            <Clock size={32} className="mx-auto mb-2 opacity-50" />
            <p>No activity yet today. Start a quiz to see your hourly stats!</p>
          </div>
        ) : (
          <>
            <div className="flex items-end gap-1 h-32 mb-2">
              {stats.hourlyData.map((h, i) => (
                <div
                  key={i}
                  className="flex-1 flex flex-col items-center group relative"
                >
                  <div
                    className={`w-full rounded-t transition-all ${
                      h.questions > 0 ? 'bg-brand-500 hover:bg-brand-400' : 'bg-gray-700'
                    }`}
                    style={{ height: `${(h.questions / stats.maxQuestions) * 100}%`, minHeight: h.questions > 0 ? '4px' : '2px' }}
                  />
                  {h.questions > 0 && (
                    <div className="absolute bottom-full mb-2 bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap z-10">
                      <p className="text-gray-200">{h.questions} Q</p>
                      <p className="text-gray-400">{Math.floor(h.time / 60)}m</p>
                    </div>
                  )}
                </div>
              ))}
            </div>
            <div className="flex justify-between text-xs text-gray-500">
              <span>12am</span>
              <span>6am</span>
              <span>12pm</span>
              <span>6pm</span>
              <span>12am</span>
            </div>
            <div className="flex items-center justify-center gap-6 mt-4 text-sm">
              <div className="flex items-center gap-2">
                <div className="w-3 h-3 bg-brand-500 rounded" />
                <span className="text-gray-400">Questions answered</span>
              </div>
            </div>
          </>
        )}
      </div>

      {/* Quick Actions */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <button
          onClick={() => onNavigate('dashboard')}
          className="bg-gray-800/60 rounded-xl p-4 border border-gray-700 hover:border-brand-500/50 transition-all text-left group"
        >
          <Zap size={24} className="text-brand-400 mb-2 group-hover:scale-110 transition-transform" />
          <p className="font-medium text-gray-200">Dashboard</p>
          <p className="text-xs text-gray-500">View analytics</p>
        </button>
        <button
          onClick={() => onNavigate('review')}
          className="bg-gray-800/60 rounded-xl p-4 border border-gray-700 hover:border-warning-500/50 transition-all text-left group relative"
        >
          <BookOpen size={24} className="text-warning-500 mb-2 group-hover:scale-110 transition-transform" />
          <p className="font-medium text-gray-200">Review Queue</p>
          <p className="text-xs text-gray-500">{stats.unreviewedCount} to review</p>
          {stats.unreviewedCount > 0 && (
            <span className="absolute top-3 right-3 w-2 h-2 bg-warning-500 rounded-full" />
          )}
        </button>
        <button
          onClick={() => onNavigate('habits')}
          className="bg-gray-800/60 rounded-xl p-4 border border-gray-700 hover:border-success-500/50 transition-all text-left group"
        >
          <Flame size={24} className="text-success-500 mb-2 group-hover:scale-110 transition-transform" />
          <p className="font-medium text-gray-200">Habits</p>
          <p className="text-xs text-gray-500">{streakDays} day streak</p>
        </button>
        <button
          onClick={() => onNavigate('expertise')}
          className="bg-gray-800/60 rounded-xl p-4 border border-gray-700 hover:border-purple-500/50 transition-all text-left group"
        >
          <Target size={24} className="text-purple-400 mb-2 group-hover:scale-110 transition-transform" />
          <p className="font-medium text-gray-200">Expertise</p>
          <p className="text-xs text-gray-500">Track mastery</p>
        </button>
      </div>
    </div>
  );
}
