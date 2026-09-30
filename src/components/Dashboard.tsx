import { useMemo } from 'react';
import { Trophy, Target, BookOpen, TrendingUp, Clock, Award } from 'lucide-react';
import type { AppPersistence, RankSimConfig } from '../types';
import { simulateRank } from '../storage';
import RankConfigEditor from './RankConfigEditor';

interface DashboardProps {
  persistence: AppPersistence;
  onUpdateRankConfigs: (configs: RankSimConfig[]) => void;
}

export default function Dashboard({ persistence, onUpdateRankConfigs }: DashboardProps) {
  const { testResults, subjects, habitLog, ankiCards, rankSimConfigs } = persistence;

  // Memoize heavy aggregations and sorts to prevent re-calculating on every render
  const { totalTests, avgAccuracy, totalTimeMinutes, recentResults } = useMemo(() => {
    const total = testResults.length;
    const accuracy = total > 0
      ? testResults.reduce((acc, r) => acc + r.accuracy, 0) / total
      : 0;
    const time = Math.round(testResults.reduce((acc, r) => acc + r.timeUsedSeconds, 0) / 60);
    const recent = [...testResults].sort((a, b) => b.timestamp - a.timestamp).slice(0, 10);

    return { totalTests: total, avgAccuracy: accuracy, totalTimeMinutes: time, recentResults: recent };
  }, [testResults]);

  // Consolidate 4 filter passes into a single O(N) reduce pass
  const ankiStats = useMemo(() => {
    return ankiCards.reduce((acc, c) => {
      acc[c.status] = (acc[c.status] || 0) + 1;
      return acc;
    }, { mastered: 0, review: 0, learning: 0, new: 0 });
  }, [ankiCards]);

  const subjectPerformance = useMemo(() => {
    return subjects.map(s => {
      const subjectResults = testResults.filter(r => r.subjectName === s.name);
      const avg = subjectResults.length > 0
        ? subjectResults.reduce((acc, r) => acc + r.accuracy, 0) / subjectResults.length
        : 0;
      return {
        name: s.name,
        tests: subjectResults.length,
        avgAccuracy: avg,
        totalQuestions: s.questions.length,
      };
    });
  }, [subjects, testResults]);

  // Pre-calculate all rank simulations outside of the JSX tree
  const processedRankSims = useMemo(() => {
    if (!rankSimConfigs || rankSimConfigs.length === 0 || recentResults.length === 0) return [];

    return rankSimConfigs.map(config => {
      const testsWithRank = recentResults
        .filter(r => r.totalQuestions > 0)
        .map(r => ({ ...r, sim: simulateRank(config, r.correctAnswers, r.wrongAnswers, r.totalQuestions) }));

      if (testsWithRank.length === 0) return null;

      const latest = testsWithRank[0];
      const best = testsWithRank.reduce((b, t) => t.sim.estimatedRank < b.sim.estimatedRank ? t : b, testsWithRank[0]);

      return { config, testsWithRank, latest, best };
    }).filter(sim => sim !== null);
  }, [rankSimConfigs, recentResults]);

  const today = new Date().toISOString().split('T')[0];
  const todayHabit = habitLog.find(h => h.date === today);
  const totalQuestionsAnswered = persistence.totalQuestionsEver;

  return (
    <div className="max-w-6xl mx-auto p-6 animate-fade-in">
      <h1 className="text-3xl font-bold text-gray-100 mb-2">📊 Dashboard</h1>
      <p className="text-gray-400 mb-8">Your learning analytics at a glance</p>

      {/* Today's summary */}
      <div className="bg-gradient-to-r from-brand-600/20 to-purple-600/20 rounded-2xl p-6 border border-brand-500/30 mb-8">
        <h2 className="text-lg font-semibold text-brand-400 mb-4">Today's Summary</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="text-center">
            <p className="text-3xl font-bold text-gray-100">{todayHabit?.questionsAnswered || 0}</p>
            <p className="text-xs text-gray-400 mt-1">Questions Answered</p>
          </div>
          <div className="text-center">
            <p className="text-3xl font-bold text-gray-100">{todayHabit?.correctAnswers || 0}</p>
            <p className="text-xs text-gray-400 mt-1">Correct Answers</p>
          </div>
          <div className="text-center">
            <p className="text-3xl font-bold text-gray-100">{todayHabit?.sessionsCount || 0}</p>
            <p className="text-xs text-gray-400 mt-1">Sessions</p>
          </div>
          <div className="text-center">
            <p className="text-3xl font-bold text-gray-100">
              {todayHabit ? Math.round(todayHabit.timeSpentSeconds / 60) : 0}m
            </p>
            <p className="text-xs text-gray-400 mt-1">Time Spent</p>
          </div>
        </div>
      </div>

      {/* Overall Stats */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
        <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
          <Trophy size={20} className="text-warning-500 mb-2" />
          <p className="text-xl font-bold text-gray-100">{totalTests}</p>
          <p className="text-xs text-gray-400">Tests Taken</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
          <Target size={20} className="text-brand-400 mb-2" />
          <p className="text-xl font-bold text-gray-100">{avgAccuracy.toFixed(1)}%</p>
          <p className="text-xs text-gray-400">Avg Accuracy</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
          <BookOpen size={20} className="text-purple-500 mb-2" />
          <p className="text-xl font-bold text-gray-100">{totalQuestionsAnswered}</p>
          <p className="text-xs text-gray-400">Questions Done</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
          <Clock size={20} className="text-success-500 mb-2" />
          <p className="text-xl font-bold text-gray-100">{totalTimeMinutes}m</p>
          <p className="text-xs text-gray-400">Total Time</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
          <TrendingUp size={20} className="text-brand-400 mb-2" />
          <p className="text-xl font-bold text-gray-100">{persistence.streakDays}</p>
          <p className="text-xs text-gray-400">Day Streak 🔥</p>
        </div>
        <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
          <Award size={20} className="text-warning-500 mb-2" />
          <p className="text-xl font-bold text-gray-100">{persistence.longestStreak}</p>
          <p className="text-xs text-gray-400">Best Streak</p>
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-6 mb-8">
        {/* Anki Status */}
        <div className="bg-gray-800/60 rounded-xl p-6 border border-gray-700">
          <h3 className="text-lg font-semibold text-gray-200 mb-4">🧠 Spaced Repetition Status</h3>
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm text-gray-400">Mastered</span>
              <div className="flex items-center gap-2">
                <div className="w-32 bg-gray-700 rounded-full h-2">
                  <div className="bg-success-500 h-2 rounded-full" style={{ width: `${ankiCards.length > 0 ? (ankiStats.mastered / ankiCards.length) * 100 : 0}%` }} />
                </div>
                <span className="text-sm text-success-500 font-medium w-8 text-right">{ankiStats.mastered}</span>
              </div>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-gray-400">Review</span>
              <div className="flex items-center gap-2">
                <div className="w-32 bg-gray-700 rounded-full h-2">
                  <div className="bg-brand-500 h-2 rounded-full" style={{ width: `${ankiCards.length > 0 ? (ankiStats.review / ankiCards.length) * 100 : 0}%` }} />
                </div>
                <span className="text-sm text-brand-400 font-medium w-8 text-right">{ankiStats.review}</span>
              </div>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-gray-400">Learning</span>
              <div className="flex items-center gap-2">
                <div className="w-32 bg-gray-700 rounded-full h-2">
                  <div className="bg-warning-500 h-2 rounded-full" style={{ width: `${ankiCards.length > 0 ? (ankiStats.learning / ankiCards.length) * 100 : 0}%` }} />
                </div>
                <span className="text-sm text-warning-500 font-medium w-8 text-right">{ankiStats.learning}</span>
              </div>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-gray-400">New</span>
              <div className="flex items-center gap-2">
                <div className="w-32 bg-gray-700 rounded-full h-2">
                  <div className="bg-gray-500 h-2 rounded-full" style={{ width: `${ankiCards.length > 0 ? (ankiStats.new / ankiCards.length) * 100 : 0}%` }} />
                </div>
                <span className="text-sm text-gray-400 font-medium w-8 text-right">{ankiStats.new}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Subject Performance */}
        <div className="bg-gray-800/60 rounded-xl p-6 border border-gray-700">
          <h3 className="text-lg font-semibold text-gray-200 mb-4">📚 Subject Performance</h3>
          {subjectPerformance.length === 0 ? (
            <p className="text-gray-500 text-sm">No subjects loaded yet.</p>
          ) : (
            <div className="space-y-3">
              {subjectPerformance.map(sp => (
                <div key={sp.name} className="bg-gray-900/50 rounded-lg p-3">
                  <div className="flex justify-between items-center mb-1">
                    <span className="text-sm font-medium text-gray-300">{sp.name}</span>
                    <span className="text-xs text-gray-400">{sp.tests} tests</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="flex-1 bg-gray-700 rounded-full h-2">
                      <div
                        className={`h-2 rounded-full ${
                          sp.avgAccuracy >= 70 ? 'bg-success-500' :
                          sp.avgAccuracy >= 50 ? 'bg-warning-500' : 'bg-danger-500'
                        }`}
                        style={{ width: `${sp.avgAccuracy}%` }}
                      />
                    </div>
                    <span className="text-xs text-gray-400 w-12 text-right">{sp.avgAccuracy.toFixed(0)}%</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Rank Simulation / Preparedness */}
      {processedRankSims.length > 0 && (
        <div className="bg-gray-800/60 rounded-xl p-6 border border-gray-700 mb-8">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-gray-200 flex items-center gap-2">
              🏅 Preparedness — Rank Trend
            </h3>
            <RankConfigEditor configs={rankSimConfigs || []} onSave={onUpdateRankConfigs} />
          </div>

          {processedRankSims.map((simData, ci) => (
            <div key={ci} className="mb-6 last:mb-0">
              <p className="text-sm text-gray-400 mb-3">{simData.config.examName}</p>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                <div className="bg-gray-900/50 rounded-lg p-3 text-center">
                  <p className="text-xs text-gray-400">Latest Rank</p>
                  <p className="text-xl font-bold text-brand-400">{simData.latest.sim.estimatedRank.toLocaleString()}</p>
                  <p className="text-xs text-gray-500">{simData.latest.sim.projectedMarks}/{simData.config.maxMarks} marks</p>
                </div>
                <div className="bg-gray-900/50 rounded-lg p-3 text-center">
                  <p className="text-xs text-gray-400">Best Rank</p>
                  <p className="text-xl font-bold text-success-500">{simData.best.sim.estimatedRank.toLocaleString()}</p>
                  <p className="text-xs text-gray-500">{simData.best.sim.projectedMarks}/{simData.config.maxMarks} marks</p>
                </div>
                <div className="bg-gray-900/50 rounded-lg p-3 text-center">
                  <p className="text-xs text-gray-400">Latest Percentile</p>
                  <p className="text-xl font-bold text-purple-400">{simData.latest.sim.percentile}%</p>
                </div>
                <div className="bg-gray-900/50 rounded-lg p-3 text-center">
                  <p className="text-xs text-gray-400">Best Percentile</p>
                  <p className="text-xl font-bold text-warning-500">{simData.best.sim.percentile}%</p>
                </div>
              </div>

              {/* Mini rank history */}
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-gray-700 text-gray-400">
                      <th className="text-left py-2 px-2">Date</th>
                      <th className="text-center py-2 px-2">Score</th>
                      <th className="text-center py-2 px-2">Projected</th>
                      <th className="text-center py-2 px-2">Est. Rank</th>
                      <th className="text-center py-2 px-2">Percentile</th>
                    </tr>
                  </thead>
                  <tbody>
                    {simData.testsWithRank.slice(0, 8).map(t => (
                      <tr key={t.id} className="border-b border-gray-700/40">
                        <td className="py-1.5 px-2 text-gray-300">{new Date(t.timestamp).toLocaleDateString()}</td>
                        <td className="py-1.5 px-2 text-center text-gray-300">{t.correctAnswers}✓ {t.wrongAnswers}✗ / {t.totalQuestions}</td>
                        <td className="py-1.5 px-2 text-center text-gray-300">{t.sim.projectedMarks}/{simData.config.maxMarks}</td>
                        <td className="py-1.5 px-2 text-center">
                          <span className={t.sim.estimatedRank <= 5000 ? 'text-success-500' : t.sim.estimatedRank <= 20000 ? 'text-warning-500' : 'text-gray-400'}>
                            {t.sim.estimatedRank.toLocaleString()}
                          </span>
                        </td>
                        <td className="py-1.5 px-2 text-center text-gray-400">{t.sim.percentile}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Recent Results Table */}
      <div className="bg-gray-800/60 rounded-xl p-6 border border-gray-700">
        <h3 className="text-lg font-semibold text-gray-200 mb-4">📝 Recent Test Results</h3>
        {recentResults.length === 0 ? (
          <p className="text-gray-500 text-sm">No tests taken yet. Upload a CSV and start a quiz!</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-700">
                  <th className="text-left py-3 px-2 text-gray-400 font-medium">Date</th>
                  <th className="text-left py-3 px-2 text-gray-400 font-medium">Subject</th>
                  <th className="text-left py-3 px-2 text-gray-400 font-medium">Mode</th>
                  <th className="text-center py-3 px-2 text-gray-400 font-medium">Q's</th>
                  <th className="text-center py-3 px-2 text-gray-400 font-medium">Score</th>
                  <th className="text-center py-3 px-2 text-gray-400 font-medium">Accuracy</th>
                </tr>
              </thead>
              <tbody>
                {recentResults.map((r, i) => (
                  <tr key={r.id} className={`border-b border-gray-700/50 ${i % 2 === 0 ? '' : 'bg-gray-800/30'}`}>
                    <td className="py-2.5 px-2 text-gray-300">{new Date(r.timestamp).toLocaleDateString()}</td>
                    <td className="py-2.5 px-2 text-gray-300">{r.subjectName}</td>
                    <td className="py-2.5 px-2">
                      <span className={`px-2 py-0.5 rounded-full text-xs ${
                        r.mode === 'learn' ? 'bg-purple-500/20 text-purple-400' :
                        'bg-brand-500/20 text-brand-400'
                      }`}>
                        {r.mode}
                      </span>
                    </td>
                    <td className="py-2.5 px-2 text-center text-gray-300">{r.totalQuestions}</td>
                    <td className="py-2.5 px-2 text-center text-gray-300">{r.score}</td>
                    <td className="py-2.5 px-2 text-center">
                      <span className={`font-medium ${
                        r.accuracy >= 70 ? 'text-success-500' :
                        r.accuracy >= 50 ? 'text-warning-500' : 'text-danger-500'
                      }`}>
                        {r.accuracy.toFixed(1)}%
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
