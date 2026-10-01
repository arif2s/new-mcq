import { useMemo } from 'react';
import { Brain, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import type { AppPersistence, TopicExpertise } from '../types';

interface ExpertiseMapProps {
  persistence: AppPersistence;
}

// Extract static configuration outside the component to prevent recreation on render
const LEVEL_CONFIG: Record<string, { color: string; bg: string; border: string; emoji: string }> = {
  beginner: { color: 'text-danger-500', bg: 'bg-danger-500/10', border: 'border-danger-500/30', emoji: '🌱' },
  intermediate: { color: 'text-warning-500', bg: 'bg-warning-500/10', border: 'border-warning-500/30', emoji: '📗' },
  advanced: { color: 'text-brand-400', bg: 'bg-brand-500/10', border: 'border-brand-500/30', emoji: '⭐' },
  expert: { color: 'text-success-500', bg: 'bg-success-500/10', border: 'border-success-500/30', emoji: '👑' },
};

export default function ExpertiseMap({ persistence }: ExpertiseMapProps) {
  const { topicExpertise, subjects } = persistence;

  // Memoize all heavy array processing into a single pass
  const { subjectGroups, levelCounts, recommendations } = useMemo(() => {
    const groups: Record<string, TopicExpertise[]> = {};
    const counts = { beginner: 0, intermediate: 0, advanced: 0, expert: 0 };
    const recs: TopicExpertise[] = [];

    for (const exp of Object.values(topicExpertise)) {
      // 1. Group by subject
      if (!groups[exp.subject]) groups[exp.subject] = [];
      groups[exp.subject].push(exp);

      // 2. Count levels
      if (exp.level in counts) {
        counts[exp.level as keyof typeof counts]++;
      }

      // 3. Collect recommendations
      if (exp.level === 'beginner' || exp.level === 'intermediate') {
        recs.push(exp);
      }
    }

    // Pre-sort grouped topics by accuracy descending
    for (const subject in groups) {
      groups[subject].sort((a, b) => b.accuracy - a.accuracy);
    }

    // Pre-sort recommendations by accuracy ascending and limit to top 5
    recs.sort((a, b) => a.accuracy - b.accuracy);

    return {
      subjectGroups: groups,
      levelCounts: counts,
      recommendations: recs.slice(0, 5)
    };
  }, [topicExpertise]);

  const totalTopics = Object.keys(topicExpertise).length;

  return (
    <div className="max-w-6xl mx-auto p-6 animate-fade-in">
      <h1 className="text-3xl font-bold text-gray-100 mb-2 flex items-center gap-3">
        <Brain size={32} className="text-purple-500" />
        Expertise Map
      </h1>
      <p className="text-gray-400 mb-8">Track your mastery level across all topics and subjects</p>

      {totalTopics === 0 ? (
        <div className="text-center py-20">
          <Brain size={64} className="mx-auto text-gray-600 mb-4" />
          <h2 className="text-xl text-gray-400 mb-2">No expertise data yet</h2>
          <p className="text-gray-500">Complete quizzes to start building your expertise map!</p>
        </div>
      ) : (
        <>
          {/* Overall Distribution */}
          <div className="bg-gray-800/60 rounded-xl p-6 border border-gray-700 mb-8">
            <h3 className="text-lg font-semibold text-gray-200 mb-4">📊 Expertise Distribution</h3>
            <div className="grid grid-cols-4 gap-4 mb-6">
              {(Object.entries(levelCounts) as [string, number][]).map(([level, count]) => {
                const config = LEVEL_CONFIG[level];
                const pct = totalTopics > 0 ? (count / totalTopics) * 100 : 0;
                return (
                  <div key={level} className={`${config.bg} ${config.border} border rounded-xl p-4 text-center`}>
                    <span className="text-2xl">{config.emoji}</span>
                    <p className={`text-2xl font-bold mt-1 ${config.color}`}>{count}</p>
                    <p className="text-xs text-gray-400 capitalize">{level}</p>
                    <p className="text-xs text-gray-500">{pct.toFixed(0)}%</p>
                  </div>
                );
              })}
            </div>

            {/* Distribution bar */}
            <div className="w-full h-6 rounded-full overflow-hidden flex">
              <div className="bg-danger-500 transition-all" style={{ width: `${(levelCounts.beginner / totalTopics) * 100}%` }} />
              <div className="bg-warning-500 transition-all" style={{ width: `${(levelCounts.intermediate / totalTopics) * 100}%` }} />
              <div className="bg-brand-500 transition-all" style={{ width: `${(levelCounts.advanced / totalTopics) * 100}%` }} />
              <div className="bg-success-500 transition-all" style={{ width: `${(levelCounts.expert / totalTopics) * 100}%` }} />
            </div>
          </div>

          {/* By Subject */}
          {Object.entries(subjectGroups).map(([subject, topics]) => {
            const subjectInfo = subjects.find(s => s.name === subject);
            const avgAccuracy = topics.reduce((acc, t) => acc + t.accuracy, 0) / topics.length;

            return (
              <div key={subject} className="bg-gray-800/60 rounded-xl p-6 border border-gray-700 mb-6">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-lg font-semibold text-gray-200">
                    📚 {subject}
                  </h3>
                  <div className="flex items-center gap-3 text-sm">
                    <span className="text-gray-400">
                      {subjectInfo?.questions.length || 0} total questions
                    </span>
                    <span className={`font-medium ${
                      avgAccuracy >= 70 ? 'text-success-500' :
                      avgAccuracy >= 50 ? 'text-warning-500' : 'text-danger-500'
                    }`}>
                      Avg: {avgAccuracy.toFixed(1)}%
                    </span>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                  {topics.map(exp => {
                    const config = LEVEL_CONFIG[exp.level] || LEVEL_CONFIG.beginner;
                    return (
                      <div
                        key={exp.topic}
                        className={`${config.bg} ${config.border} border rounded-lg p-4 transition-all hover:scale-[1.02]`}
                      >
                        <div className="flex items-start justify-between mb-2">
                          <div>
                            <p className="text-sm font-medium text-gray-200">{exp.topic}</p>
                            <p className={`text-xs capitalize ${config.color}`}>
                              {config.emoji} {exp.level}
                            </p>
                          </div>
                          <div className="flex items-center gap-1">
                            {exp.trend === 'improving' && <TrendingUp size={14} className="text-success-500" />}
                            {exp.trend === 'declining' && <TrendingDown size={14} className="text-danger-500" />}
                            {exp.trend === 'stable' && <Minus size={14} className="text-gray-500" />}
                          </div>
                        </div>

                        <div className="flex items-center gap-2 mb-2">
                          <div className="flex-1 bg-gray-700/50 rounded-full h-2">
                            <div
                              className={`h-2 rounded-full ${
                                exp.accuracy >= 70 ? 'bg-success-500' :
                                exp.accuracy >= 50 ? 'bg-warning-500' : 'bg-danger-500'
                              }`}
                              style={{ width: `${exp.accuracy}%` }}
                            />
                          </div>
                          <span className="text-xs text-gray-400 w-10 text-right">{exp.accuracy.toFixed(0)}%</span>
                        </div>

                        <div className="flex justify-between text-xs text-gray-500">
                          <span>{exp.attempted}/{exp.totalQuestions} attempted</span>
                          <span>{exp.correct} correct</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}

          {/* Recommendations */}
          <div className="bg-gradient-to-r from-purple-600/20 to-brand-600/20 rounded-xl p-6 border border-purple-500/30">
            <h3 className="text-lg font-semibold text-gray-200 mb-4">🎯 Focus Recommendations</h3>
            <div className="space-y-3">
              {recommendations.map(exp => (
                <div key={`${exp.subject}-${exp.topic}`} className="flex items-center justify-between bg-gray-900/50 rounded-lg p-3">
                  <div>
                    <span className="text-sm text-gray-300">{exp.topic}</span>
                    <span className="text-xs text-gray-500 ml-2">({exp.subject})</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-xs px-2 py-0.5 rounded-full ${LEVEL_CONFIG[exp.level].bg} ${LEVEL_CONFIG[exp.level].color}`}>
                      {exp.accuracy.toFixed(0)}%
                    </span>
                    <span className="text-xs text-gray-500">→ Practice more</span>
                  </div>
                </div>
              ))}
              {recommendations.length === 0 && (
                <p className="text-gray-500 text-sm">All topics are at advanced level or above! 🎉</p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
