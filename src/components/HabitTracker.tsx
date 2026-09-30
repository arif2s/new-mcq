import { useMemo } from 'react';
import { Flame, Calendar, TrendingUp, BookOpen, Target, Award } from 'lucide-react';
import type { AppPersistence, HabitEntry } from '../types';

interface HabitTrackerProps {
  persistence: AppPersistence;
}

export default function HabitTracker({ persistence }: HabitTrackerProps) {
  const { habitLog, streakDays, longestStreak, testResults, totalQuestionsEver } = persistence;

  const {
    weeks,
    weeklyQuestions,
    activeDaysThisWeek,
    monthlyQuestions,
    activeDaysThisMonth,
    bestDay,
    todayCount,
    monthLabels,
    recentActivity
  } = useMemo(() => {
    // 1. Create O(1) lookup map to eliminate O(N) nested searches
    const habitMap = new Map<string, HabitEntry>();
    for (const h of habitLog) {
      habitMap.set(h.date, h);
    }

    const today = new Date();
    const todayStr = today.toISOString().split('T')[0];
    const currentTodayCount = habitMap.get(todayStr)?.questionsAnswered || 0;

    const calendarData: { date: string; count: number; day: number }[] = [];

    // 2. Prevent excessive object allocation by stepping a single Date instance
    const iterDate = new Date(today);
    iterDate.setDate(iterDate.getDate() - 364);

    for (let i = 0; i <= 364; i++) {
      const dateStr = iterDate.toISOString().split('T')[0];
      const habit = habitMap.get(dateStr);
      calendarData.push({
        date: dateStr,
        count: habit?.questionsAnswered || 0,
        day: iterDate.getDay(),
      });
      iterDate.setDate(iterDate.getDate() + 1);
    }

    // Group into weeks
    const calculatedWeeks: typeof calendarData[] = [];
    let currentWeek: typeof calendarData = [];
    for (const day of calendarData) {
      currentWeek.push(day);
      if (day.day === 6) {
        calculatedWeeks.push(currentWeek);
        currentWeek = [];
      }
    }
    if (currentWeek.length > 0) calculatedWeeks.push(currentWeek);

    // Slice stats
    const last7Days = calendarData.slice(-7);
    const wQuestions = last7Days.reduce((acc, d) => acc + d.count, 0);
    const wActive = last7Days.filter(d => d.count > 0).length;

    const last30Days = calendarData.slice(-30);
    const mQuestions = last30Days.reduce((acc, d) => acc + d.count, 0);
    const mActive = last30Days.filter(d => d.count > 0).length;

    const best = habitLog.length > 0
      ? habitLog.reduce((b, h) => h.questionsAnswered > b.questionsAnswered ? h : b, habitLog[0])
      : null;

    // Fast month labels iteration
    const mLabels: string[] = [];
    const labelDate = new Date(today);
    labelDate.setMonth(labelDate.getMonth() - 11);
    for (let i = 0; i < 12; i++) {
      mLabels.push(labelDate.toLocaleString('default', { month: 'short' }));
      labelDate.setMonth(labelDate.getMonth() + 1);
    }

    // Pre-sort recent activity
    const recent = [...habitLog].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 14);

    return {
      weeks: calculatedWeeks,
      weeklyQuestions: wQuestions,
      activeDaysThisWeek: wActive,
      monthlyQuestions: mQuestions,
      activeDaysThisMonth: mActive,
      bestDay: best,
      todayCount: currentTodayCount,
      monthLabels: mLabels,
      recentActivity: recent
    };
  }, [habitLog]);

  const getIntensity = (count: number): string => {
    if (count === 0) return 'bg-gray-800';
    if (count <= 5) return 'bg-success-700/50';
    if (count <= 15) return 'bg-success-600/60';
    if (count <= 30) return 'bg-success-500/80';
    return 'bg-success-500';
  };

  const dailyGoal = 10;
  const goalProgress = Math.min((todayCount / dailyGoal) * 100, 100);

  return (
    <div className="max-w-6xl mx-auto p-6 animate-fade-in">
      <h1 className="text-3xl font-bold text-gray-100 mb-2">📅 Habit Tracker</h1>
      <p className="text-gray-400 mb-8">Track your daily learning consistency</p>

      {/* Motivation Banner */}
      <div className="bg-gradient-to-r from-brand-600/20 via-purple-600/20 to-success-600/20 rounded-2xl p-6 border border-brand-500/30 mb-8">
        <div className="flex items-center gap-4 mb-4">
          <div className="w-16 h-16 bg-warning-500/20 rounded-full flex items-center justify-center">
            <Flame size={32} className="text-warning-500" />
          </div>
          <div>
            <h2 className="text-2xl font-bold text-gray-100">
              {streakDays > 0 ? `${streakDays} Day Streak! 🔥` : 'Start Your Streak Today! 💪'}
            </h2>
            <p className="text-gray-400">
              {streakDays >= 30 ? 'Incredible consistency! You\'re a learning machine!' :
               streakDays >= 7 ? 'Amazing! Keep the momentum going!' :
               streakDays >= 3 ? 'Great start! Consistency is key!' :
               'Every journey starts with a single step.'}
            </p>
          </div>
        </div>

        {/* Daily Goal Progress */}
        <div className="bg-gray-900/40 rounded-xl p-4">
          <div className="flex justify-between items-center mb-2">
            <span className="text-sm text-gray-300">Today's Goal: {todayCount}/{dailyGoal} questions</span>
            <span className="text-sm text-brand-400 font-medium">{goalProgress.toFixed(0)}%</span>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-3">
            <div
              className={`h-3 rounded-full transition-all duration-1000 ${
                goalProgress >= 100 ? 'bg-success-500' : 'bg-brand-500'
              }`}
              style={{ width: `${goalProgress}%` }}
            />
          </div>
          {goalProgress >= 100 && (
            <p className="text-success-500 text-sm mt-2 font-medium">🎉 Daily goal achieved!</p>
          )}
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4 mb-8">
        <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
          <Flame size={18} className="text-warning-500 mb-2" />
          <p className="text-xl font-bold text-gray-100">{streakDays}</p>
          <p className="text-xs text-gray-400">Current Streak</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
          <Award size={18} className="text-warning-500 mb-2" />
          <p className="text-xl font-bold text-gray-100">{longestStreak}</p>
          <p className="text-xs text-gray-400">Best Streak</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
          <BookOpen size={18} className="text-brand-400 mb-2" />
          <p className="text-xl font-bold text-gray-100">{weeklyQuestions}</p>
          <p className="text-xs text-gray-400">This Week</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
          <Calendar size={18} className="text-purple-500 mb-2" />
          <p className="text-xl font-bold text-gray-100">{activeDaysThisWeek}/7</p>
          <p className="text-xs text-gray-400">Active Days (Week)</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
          <TrendingUp size={18} className="text-success-500 mb-2" />
          <p className="text-xl font-bold text-gray-100">{monthlyQuestions}</p>
          <p className="text-xs text-gray-400">This Month</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
          <Target size={18} className="text-brand-400 mb-2" />
          <p className="text-xl font-bold text-gray-100">{activeDaysThisMonth}/30</p>
          <p className="text-xs text-gray-400">Active Days (Month)</p>
        </div>
      </div>

      {/* GitHub-style Contribution Calendar */}
      <div className="bg-gray-800/60 rounded-xl p-6 border border-gray-700 mb-8">
        <h3 className="text-lg font-semibold text-gray-200 mb-4">📊 Activity Heatmap (Last 52 Weeks)</h3>

        {/* Month labels */}
        <div className="flex mb-2 ml-10">
          {monthLabels.map((m, i) => (
            <span key={i} className="text-xs text-gray-500 flex-1">{m}</span>
          ))}
        </div>

        <div className="flex gap-0.5 overflow-x-auto">
          {/* Day labels */}
          <div className="flex flex-col gap-0.5 mr-1 text-xs text-gray-500">
            <span className="h-3 leading-3">S</span>
            <span className="h-3 leading-3">M</span>
            <span className="h-3 leading-3">T</span>
            <span className="h-3 leading-3">W</span>
            <span className="h-3 leading-3">T</span>
            <span className="h-3 leading-3">F</span>
            <span className="h-3 leading-3">S</span>
          </div>

          {/* Weeks */}
          {weeks.map((week, wi) => (
            <div key={wi} className="flex flex-col gap-0.5">
              {/* Pad the first week if it doesn't start on Sunday */}
              {wi === 0 && week[0] && Array.from({ length: week[0].day }).map((_, pi) => (
                <div key={`pad-${pi}`} className="w-3 h-3" />
              ))}
              {week.map((day, di) => (
                <div
                  key={di}
                  className={`w-3 h-3 rounded-[2px] habit-cell ${getIntensity(day.count)}`}
                  title={`${day.date}: ${day.count} questions`}
                />
              ))}
            </div>
          ))}
        </div>

        {/* Legend */}
        <div className="flex items-center gap-2 mt-4 text-xs text-gray-500">
          <span>Less</span>
          <div className="w-3 h-3 rounded-[2px] bg-gray-800" />
          <div className="w-3 h-3 rounded-[2px] bg-success-700/50" />
          <div className="w-3 h-3 rounded-[2px] bg-success-600/60" />
          <div className="w-3 h-3 rounded-[2px] bg-success-500/80" />
          <div className="w-3 h-3 rounded-[2px] bg-success-500" />
          <span>More</span>
        </div>
      </div>

      {/* Recent Activity */}
      <div className="bg-gray-800/60 rounded-xl p-6 border border-gray-700 mb-8">
        <h3 className="text-lg font-semibold text-gray-200 mb-4">📝 Recent Activity</h3>
        {recentActivity.length === 0 ? (
          <p className="text-gray-500 text-sm">No activity recorded yet. Start a quiz to begin tracking!</p>
        ) : (
          <div className="space-y-2 max-h-64 overflow-y-auto">
            {recentActivity.map(h => (
              <div key={h.date} className="flex items-center justify-between bg-gray-900/50 rounded-lg p-3">
                <div>
                  <span className="text-sm text-gray-300 font-medium">{h.date}</span>
                  <span className="text-xs text-gray-500 ml-2">
                    {h.subjects.join(', ')}
                  </span>
                </div>
                <div className="flex items-center gap-4 text-sm">
                  <span className="text-brand-400">{h.questionsAnswered} Q</span>
                  <span className="text-success-500">{h.correctAnswers} ✓</span>
                  <span className="text-gray-400">{h.sessionsCount} sessions</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Milestones & Achievements */}
      <div className="bg-gray-800/60 rounded-xl p-6 border border-gray-700">
        <h3 className="text-lg font-semibold text-gray-200 mb-4">🏆 Milestones</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            { label: 'First Quiz', target: 1, current: testResults.length, icon: '🌱' },
            { label: '10 Quizzes', target: 10, current: testResults.length, icon: '📚' },
            { label: '100 Questions', target: 100, current: totalQuestionsEver, icon: '💯' },
            { label: '500 Questions', target: 500, current: totalQuestionsEver, icon: '🚀' },
            { label: '7 Day Streak', target: 7, current: longestStreak, icon: '🔥' },
            { label: '30 Day Streak', target: 30, current: longestStreak, icon: '⭐' },
            { label: 'Best Day', target: 1, current: bestDay ? 1 : 0, icon: bestDay ? `${bestDay.questionsAnswered}Q` : '🎯' },
            { label: '1000 Questions', target: 1000, current: totalQuestionsEver, icon: '👑' },
          ].map(milestone => {
            const achieved = milestone.current >= milestone.target;
            return (
              <div
                key={milestone.label}
                className={`p-4 rounded-xl border text-center transition-all ${
                  achieved
                    ? 'bg-success-500/10 border-success-500/30'
                    : 'bg-gray-900/50 border-gray-700 opacity-60'
                }`}
              >
                <span className="text-2xl">{achieved ? milestone.icon : '🔒'}</span>
                <p className={`text-sm font-medium mt-2 ${achieved ? 'text-success-500' : 'text-gray-500'}`}>
                  {milestone.label}
                </p>
                {!achieved && (
                  <p className="text-xs text-gray-600 mt-1">
                    {milestone.current}/{milestone.target}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );

}
