import { useState } from 'react';
import { Plus, Trash2, Save, X, Settings, ChevronDown } from 'lucide-react';
import type { RankSimConfig, RankDataPoint } from '../types';

interface RankConfigEditorProps {
  configs: RankSimConfig[];
  onSave: (configs: RankSimConfig[]) => void;
}

export default function RankConfigEditor({ configs, onSave }: RankConfigEditorProps) {
  const [editing, setEditing] = useState(false);
  const [localConfigs, setLocalConfigs] = useState<RankSimConfig[]>(() => JSON.parse(JSON.stringify(configs)));
  const [activeIdx, setActiveIdx] = useState(0);

  const handleOpen = () => {
    setLocalConfigs(JSON.parse(JSON.stringify(configs)));
    setEditing(true);
  };

  const handleSave = () => {
    // Clean: recalculate maxMarks, sort data points
    const cleaned = localConfigs.map(c => ({
      ...c,
      maxMarks: c.totalQuestions * c.marksPerCorrect,
      dataPoints: [...c.dataPoints].sort((a, b) => b.marks - a.marks),
    }));
    onSave(cleaned);
    setEditing(false);
  };

  const handleAddConfig = () => {
    setLocalConfigs(prev => [
      ...prev,
      {
        examName: `Exam ${prev.length + 1}`,
        totalQuestions: 200,
        marksPerCorrect: 4,
        negativePerWrong: 1,
        maxMarks: 800,
        dataPoints: [],
        bands: [],
      },
    ]);
    setActiveIdx(localConfigs.length);
  };

  const handleRemoveConfig = (idx: number) => {
    if (localConfigs.length <= 1) return;
    setLocalConfigs(prev => prev.filter((_, i) => i !== idx));
    setActiveIdx(Math.max(0, activeIdx - 1));
  };

  const handleUpdateField = (idx: number, field: keyof RankSimConfig, value: string | number) => {
    setLocalConfigs(prev => prev.map((c, i) => i === idx ? { ...c, [field]: value } : c));
  };

  const handleAddDataPoint = (configIdx: number) => {
    setLocalConfigs(prev => prev.map((c, i) =>
      i === configIdx ? { ...c, dataPoints: [...c.dataPoints, { marks: 0, rank: 0 }] } : c
    ));
  };

  const handleUpdateDataPoint = (configIdx: number, dpIdx: number, field: keyof RankDataPoint, value: number) => {
    setLocalConfigs(prev => prev.map((c, i) =>
      i === configIdx ? {
        ...c,
        dataPoints: c.dataPoints.map((dp, j) => j === dpIdx ? { ...dp, [field]: value } : dp)
      } : c
    ));
  };

  const handleRemoveDataPoint = (configIdx: number, dpIdx: number) => {
    setLocalConfigs(prev => prev.map((c, i) =>
      i === configIdx ? { ...c, dataPoints: c.dataPoints.filter((_, j) => j !== dpIdx) } : c
    ));
  };

  if (!editing) {
    return (
      <button
        onClick={handleOpen}
        className="flex items-center gap-2 px-3 py-1.5 bg-gray-800 text-gray-400 rounded-lg text-sm hover:bg-gray-700 hover:text-gray-200 transition-colors"
      >
        <Settings size={14} /> Edit Rank Data
      </button>
    );
  }

  const active = localConfigs[activeIdx];

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 bg-black/70 z-50" onClick={() => setEditing(false)} />

      {/* Editor Modal */}
      <div className="fixed inset-4 md:inset-10 lg:inset-16 bg-gray-900 border border-gray-700 rounded-2xl z-50 overflow-hidden flex flex-col animate-fade-in">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-700 bg-gray-800/50">
          <h2 className="text-lg font-bold text-gray-100">Rank Simulation Editor</h2>
          <div className="flex items-center gap-2">
            <button onClick={handleSave} className="flex items-center gap-1 px-4 py-2 bg-brand-600 text-white rounded-lg text-sm hover:bg-brand-500">
              <Save size={14} /> Save
            </button>
            <button onClick={() => setEditing(false)} className="p-2 text-gray-400 hover:text-white">
              <X size={20} />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          <div className="max-w-3xl mx-auto space-y-6">

            {/* Exam selector tabs */}
            <div className="flex items-center gap-2 flex-wrap">
              {localConfigs.map((c, i) => (
                <button
                  key={i}
                  onClick={() => setActiveIdx(i)}
                  className={`px-3 py-1.5 rounded-lg text-sm transition-all ${
                    i === activeIdx ? 'bg-brand-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
                  }`}
                >
                  {c.examName || `Exam ${i + 1}`}
                </button>
              ))}
              <button onClick={handleAddConfig} className="px-3 py-1.5 bg-gray-800 text-gray-400 rounded-lg text-sm hover:bg-gray-700 flex items-center gap-1">
                <Plus size={14} /> Add Exam
              </button>
            </div>

            {active && (
              <>
                {/* Exam settings */}
                <div className="bg-gray-800/50 rounded-xl p-5 border border-gray-700">
                  <h3 className="text-sm font-semibold text-gray-300 mb-4">Exam Settings</h3>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div>
                      <label className="text-xs text-gray-400 block mb-1">Exam Name</label>
                      <input
                        type="text"
                        value={active.examName}
                        onChange={(e) => handleUpdateField(activeIdx, 'examName', e.target.value)}
                        className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-400 block mb-1">Total Questions</label>
                      <input
                        type="number"
                        value={active.totalQuestions}
                        onChange={(e) => handleUpdateField(activeIdx, 'totalQuestions', Number(e.target.value))}
                        className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-400 block mb-1">Marks per Correct (+)</label>
                      <input
                        type="number"
                        value={active.marksPerCorrect}
                        onChange={(e) => handleUpdateField(activeIdx, 'marksPerCorrect', Number(e.target.value))}
                        className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-gray-400 block mb-1">Negative per Wrong (-)</label>
                      <input
                        type="number"
                        value={active.negativePerWrong}
                        onChange={(e) => handleUpdateField(activeIdx, 'negativePerWrong', Number(e.target.value))}
                        className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200"
                      />
                    </div>
                  </div>
                  <p className="text-xs text-gray-500 mt-3">Max Marks: {active.totalQuestions * active.marksPerCorrect}</p>

                  {localConfigs.length > 1 && (
                    <button
                      onClick={() => handleRemoveConfig(activeIdx)}
                      className="mt-3 text-xs text-danger-500 hover:text-danger-400 flex items-center gap-1"
                    >
                      <Trash2 size={12} /> Remove this exam
                    </button>
                  )}
                </div>

                {/* Data points table */}
                <div className="bg-gray-800/50 rounded-xl p-5 border border-gray-700">
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-sm font-semibold text-gray-300 flex items-center gap-2">
                      <ChevronDown size={14} />
                      Marks → Rank Table ({active.dataPoints.length} entries)
                    </h3>
                    <button
                      onClick={() => handleAddDataPoint(activeIdx)}
                      className="flex items-center gap-1 px-3 py-1 bg-brand-600/20 text-brand-400 rounded-lg text-xs hover:bg-brand-600/30"
                    >
                      <Plus size={12} /> Add Row
                    </button>
                  </div>

                  <p className="text-xs text-gray-500 mb-4">
                    Enter real marks and corresponding rank data. The app interpolates between these points to estimate your rank.
                  </p>

                  {active.dataPoints.length === 0 ? (
                    <p className="text-gray-500 text-sm text-center py-4">
                      No data points yet. Add marks → rank pairs from your exam results.
                    </p>
                  ) : (
                    <div className="space-y-2 max-h-80 overflow-y-auto">
                      <div className="grid grid-cols-[1fr_1fr_auto] gap-3 text-xs text-gray-400 font-medium px-1 mb-1">
                        <span>Marks</span>
                        <span>Rank</span>
                        <span className="w-8" />
                      </div>
                      {[...active.dataPoints]
                        .map((dp, origIdx) => ({ dp, origIdx }))
                        .sort((a, b) => b.dp.marks - a.dp.marks)
                        .map(({ dp, origIdx }) => (
                          <div key={origIdx} className="grid grid-cols-[1fr_1fr_auto] gap-3 items-center">
                            <input
                              type="number"
                              value={dp.marks}
                              onChange={(e) => handleUpdateDataPoint(activeIdx, origIdx, 'marks', Number(e.target.value))}
                              className="bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200"
                            />
                            <input
                              type="number"
                              value={dp.rank}
                              onChange={(e) => handleUpdateDataPoint(activeIdx, origIdx, 'rank', Number(e.target.value))}
                              className="bg-gray-900 border border-gray-600 rounded px-3 py-1.5 text-sm text-gray-200"
                            />
                            <button
                              onClick={() => handleRemoveDataPoint(activeIdx, origIdx)}
                              className="p-1.5 text-gray-500 hover:text-danger-500"
                            >
                              <Trash2 size={14} />
                            </button>
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
