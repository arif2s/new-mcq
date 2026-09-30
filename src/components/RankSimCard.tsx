import { Trophy, TrendingUp, ArrowUp, ArrowDown, ChevronRight } from 'lucide-react';
import type { RankSimConfig, SimulatedRank } from '../types';

interface RankSimCardProps {
  config: RankSimConfig;
  sim: SimulatedRank;
  testQuestions: number;
  correct: number;
  wrong: number;
  compact?: boolean; // For dashboard, skip breakdown details
}

export default function RankSimCard({ config, sim, testQuestions, correct, wrong, compact }: RankSimCardProps) {
  const rawMarks = correct * config.marksPerCorrect - wrong * config.negativePerWrong;
  const unanswered = testQuestions - correct - wrong;

  const rankColor =
    sim.estimatedRank <= 500 ? 'text-success-500' :
    sim.estimatedRank <= 3500 ? 'text-brand-400' :
    sim.estimatedRank <= 13000 ? 'text-warning-500' :
    'text-danger-500';

  const percentileNum = parseFloat(sim.percentile);
  const percentileColor =
    percentileNum >= 99 ? 'text-success-500' :
    percentileNum >= 95 ? 'text-brand-400' :
    percentileNum >= 80 ? 'text-warning-500' :
    'text-gray-400';

  const bandColor = (rankMax: number) =>
    rankMax <= 110 ? 'border-success-500/40 bg-success-500/10' :
    rankMax <= 1300 ? 'border-brand-500/40 bg-brand-500/10' :
    rankMax <= 7000 ? 'border-warning-500/40 bg-warning-500/10' :
    rankMax <= 20000 ? 'border-purple-500/40 bg-purple-500/10' :
    'border-gray-600 bg-gray-800/50';

  return (
    <div className="bg-gray-800/60 rounded-xl border border-gray-700 p-6">
      <h3 className="text-lg font-semibold text-gray-200 mb-1 flex items-center gap-2">
        <Trophy size={20} className="text-warning-500" />
        Rank Simulation
      </h3>
      <p className="text-xs text-gray-500 mb-4">{config.examName}</p>

      {/* Main rank display */}
      <div className="grid grid-cols-3 gap-4 mb-5">
        <div className="bg-gray-900/60 rounded-lg p-4 text-center">
          <p className="text-xs text-gray-400 mb-1">Projected Marks</p>
          <p className="text-2xl font-black text-gray-100">
            {sim.projectedMarks}
            <span className="text-gray-500 text-sm font-normal">/{config.maxMarks}</span>
          </p>
        </div>
        <div className="bg-gray-900/60 rounded-lg p-4 text-center">
          <p className="text-xs text-gray-400 mb-1">Estimated Rank</p>
          <p className={`text-2xl font-black ${rankColor}`}>
            {sim.estimatedRank.toLocaleString()}
          </p>
        </div>
        <div className="bg-gray-900/60 rounded-lg p-4 text-center">
          <p className="text-xs text-gray-400 mb-1">Percentile</p>
          <p className={`text-2xl font-black ${percentileColor}`}>
            {sim.percentile}%
          </p>
        </div>
      </div>

      {/* Current Band — Competitive Outlook */}
      {sim.currentBand && (
        <div className={`rounded-xl border p-4 mb-4 ${bandColor(sim.currentBand.rankMax)}`}>
          <div className="flex items-start justify-between mb-2">
            <div>
              <p className="text-xs text-gray-400">Current Standing</p>
              <p className="text-base font-bold text-gray-100">
                AIR {sim.currentBand.label}
                <span className="text-gray-500 text-xs font-normal ml-2">
                  ({sim.currentBand.marksMin}–{sim.currentBand.marksMax} marks)
                </span>
              </p>
            </div>
            <span className="px-2 py-0.5 bg-gray-900/60 rounded text-xs text-gray-300">
              Rank {sim.currentBand.rankMin.toLocaleString()}–{sim.currentBand.rankMax.toLocaleString()}
            </span>
          </div>
          <p className="text-sm text-gray-300">{sim.currentBand.outlook}</p>
        </div>
      )}

      {/* Next Band — Motivation */}
      {sim.nextBand && sim.marksToNextBand > 0 && (
        <div className="bg-brand-500/10 border border-brand-500/30 rounded-xl p-4 mb-4">
          <div className="flex items-center gap-2 mb-2">
            <ChevronRight size={16} className="text-brand-400" />
            <p className="text-sm font-semibold text-brand-400">
              +{sim.marksToNextBand} marks to reach AIR {sim.nextBand.label}
            </p>
          </div>
          <p className="text-xs text-gray-400">{sim.nextBand.outlook}</p>
          {/* How many more correct answers needed */}
          <p className="text-xs text-gray-500 mt-2">
            ≈ {Math.ceil(sim.marksToNextBand / config.marksPerCorrect)} more correct answers
            {wrong > 0 && ` or fix ${Math.ceil(sim.marksToNextBand / (config.marksPerCorrect + config.negativePerWrong))} wrong→correct`}
          </p>
        </div>
      )}

      {/* Full band table (only in non-compact mode) */}
      {!compact && config.bands && config.bands.length > 0 && (
        <div className="mb-4">
          <p className="text-xs text-gray-400 mb-2 font-medium">All Bands</p>
          <div className="space-y-1 max-h-64 overflow-y-auto">
            {[...config.bands].sort((a, b) => b.marksMin - a.marksMin).map((band, i) => {
              const isCurrent = sim.currentBand && band.marksMin === sim.currentBand.marksMin;
              return (
                <div
                  key={i}
                  className={`flex items-center gap-3 px-3 py-2 rounded-lg text-xs transition-all ${
                    isCurrent
                      ? 'bg-brand-500/20 border border-brand-500/40'
                      : 'bg-gray-900/30 hover:bg-gray-900/50'
                  }`}
                >
                  <span className={`w-20 font-mono font-semibold ${isCurrent ? 'text-brand-400' : 'text-gray-300'}`}>
                    {band.marksMin}–{band.marksMax}
                  </span>
                  <span className={`w-20 ${isCurrent ? 'text-brand-400' : 'text-gray-500'}`}>
                    {band.label}
                  </span>
                  <span className={`flex-1 ${isCurrent ? 'text-gray-200' : 'text-gray-500'}`}>
                    {band.outlook}
                  </span>
                  {isCurrent && <span className="text-brand-400 font-bold">← You</span>}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Score Breakdown (non-compact) */}
      {!compact && (
        <div className="bg-gray-900/40 rounded-lg p-4 mb-4">
          <p className="text-xs text-gray-400 mb-3">Score Breakdown (your {testQuestions}Q test → projected to {config.totalQuestions}Q)</p>
          <div className="grid grid-cols-4 gap-3 text-center text-sm">
            <div>
              <p className="text-gray-500 text-xs">Raw</p>
              <p className="font-semibold text-gray-200">{rawMarks}</p>
            </div>
            <div>
              <p className="text-success-500 text-xs">Correct</p>
              <p className="font-semibold text-gray-200">{correct} <span className="text-success-700 text-xs">(+{correct * config.marksPerCorrect})</span></p>
            </div>
            <div>
              <p className="text-danger-500 text-xs">Wrong</p>
              <p className="font-semibold text-gray-200">{wrong} <span className="text-danger-700 text-xs">(-{wrong * config.negativePerWrong})</span></p>
            </div>
            <div>
              <p className="text-gray-500 text-xs">Unanswered</p>
              <p className="font-semibold text-gray-200">{unanswered}</p>
            </div>
          </div>
        </div>
      )}

      {/* Nearest brackets */}
      {(sim.nearestAbove || sim.nearestBelow) && (
        <div className="flex gap-3 text-xs">
          {sim.nearestBelow && (
            <div className="flex-1 bg-danger-500/10 border border-danger-500/20 rounded-lg p-2 flex items-center gap-2">
              <ArrowDown size={14} className="text-danger-500 flex-shrink-0" />
              <span className="text-gray-400">{sim.nearestBelow.marks} marks → Rank {sim.nearestBelow.rank.toLocaleString()}</span>
            </div>
          )}
          {sim.nearestAbove && (
            <div className="flex-1 bg-success-500/10 border border-success-500/20 rounded-lg p-2 flex items-center gap-2">
              <ArrowUp size={14} className="text-success-500 flex-shrink-0" />
              <span className="text-gray-400">{sim.nearestAbove.marks} marks → Rank {sim.nearestAbove.rank.toLocaleString()}</span>
            </div>
          )}
        </div>
      )}

      {/* Motivation */}
      {!compact && (
        <div className="mt-4 text-center">
          {sim.estimatedRank <= 500 && (
            <p className="text-success-500 text-sm font-medium flex items-center justify-center gap-1">
              <TrendingUp size={14} /> Outstanding! Top-tier — dream speciality is within reach 🏆
            </p>
          )}
          {sim.estimatedRank > 500 && sim.estimatedRank <= 3500 && (
            <p className="text-brand-400 text-sm font-medium">
              Strong performance! Clinical branches at top colleges are in range 💪
            </p>
          )}
          {sim.estimatedRank > 3500 && sim.estimatedRank <= 13000 && (
            <p className="text-warning-500 text-sm font-medium">
              Good standing — focused revision on weak topics can push you higher 📈
            </p>
          )}
          {sim.estimatedRank > 13000 && sim.estimatedRank <= 28000 && (
            <p className="text-purple-400 text-sm font-medium">
              Solid base — consistent practice will open more options 🎯
            </p>
          )}
          {sim.estimatedRank > 28000 && (
            <p className="text-gray-400 text-sm">
              Every session moves you forward. Focus on high-yield topics! 📚
            </p>
          )}
        </div>
      )}
    </div>
  );
}
