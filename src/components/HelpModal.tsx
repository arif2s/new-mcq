import { X } from 'lucide-react';

interface HelpModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function HelpModal({ isOpen, onClose }: HelpModalProps) {
  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/70 z-50 animate-fade-in"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="fixed inset-4 md:inset-10 lg:inset-20 bg-gray-900 border border-gray-700 rounded-2xl z-50 overflow-hidden flex flex-col animate-fade-in">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-700 bg-gray-800/50">
          <h2 className="text-xl font-bold text-gray-100 flex items-center gap-2">
            📚 Help & Documentation
          </h2>
          <button
            onClick={onClose}
            className="p-2 text-gray-400 hover:text-white hover:bg-gray-700 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          <div className="max-w-3xl mx-auto space-y-8">

            {/* Auto-save notice */}
            <div className="bg-success-500/10 border border-success-500/30 rounded-xl p-4">
              <h3 className="text-success-500 font-semibold mb-2 flex items-center gap-2">
                💾 Auto-Save Enabled
              </h3>
              <p className="text-gray-300 text-sm">
                All your progress is <strong>automatically saved</strong> to your browser's local storage.
                You don't need to manually save anything! Your data persists between sessions.
              </p>
              <p className="text-gray-400 text-xs mt-2">
                The Export/Import JSON feature is only needed if you want to backup your data or transfer it to another browser/device.
              </p>
            </div>

            {/* Quiz Modes */}
            <section>
              <h3 className="text-lg font-semibold text-brand-400 mb-3 flex items-center gap-2">
                🎮 Quiz Modes
              </h3>
              <div className="grid md:grid-cols-3 gap-4">
                <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                  <h4 className="font-semibold text-gray-200 mb-2">📝 Test Mode</h4>
                  <ul className="text-sm text-gray-400 space-y-1">
                    <li>• Shows ✓/✗ briefly after each answer</li>
                    <li>• Auto-advances to next question (2 sec)</li>
                    <li>• All explanations shown at the end</li>
                    <li>• Great for timed practice</li>
                  </ul>
                </div>
                <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                  <h4 className="font-semibold text-gray-200 mb-2">📖 Learn Mode</h4>
                  <ul className="text-sm text-gray-400 space-y-1">
                    <li>• Shows explanation after each answer</li>
                    <li>• Manual navigation (Next button)</li>
                    <li>• Take your time to understand</li>
                    <li>• Ideal for studying new material</li>
                  </ul>
                </div>
                <div className="bg-gray-800/50 rounded-lg p-4 border border-purple-500/30">
                  <h4 className="font-semibold text-purple-400 mb-2">🎯 Target Mode</h4>
                  <ul className="text-sm text-gray-400 space-y-1">
                    <li>• Set a target (e.g., 10 correct)</li>
                    <li>• Quiz continues until you reach it</li>
                    <li>• Wrong answers don't end the quiz</li>
                    <li>• Great for mastery practice</li>
                  </ul>
                </div>
              </div>
            </section>

            {/* Timing Features */}
            <section>
              <h3 className="text-lg font-semibold text-brand-400 mb-3 flex items-center gap-2">
                ⏱️ Timing & Focus Features
              </h3>
              <div className="space-y-3">
                <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                  <h4 className="font-semibold text-gray-200 text-sm">Per-Question Timing</h4>
                  <p className="text-xs text-gray-400 mt-1">
                    Each question's response time is tracked and shown in results. See average, fastest, and slowest times.
                  </p>
                </div>
                <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                  <h4 className="font-semibold text-gray-200 text-sm">Question Timeout</h4>
                  <p className="text-xs text-gray-400 mt-1">
                    If no answer is given within the timeout (default 5 min), the quiz pauses and asks if you need a break.
                    Timed-out questions are excluded from timing averages.
                  </p>
                </div>
                <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                  <h4 className="font-semibold text-gray-200 text-sm">Distraction Analysis</h4>
                  <p className="text-xs text-gray-400 mt-1">
                    After completing a quiz, we analyze your response patterns to detect when you might have started getting
                    distracted (e.g., response time increased significantly after question 50). Helps identify optimal session length.
                  </p>
                </div>
              </div>
            </section>

            {/* Spaced Repetition */}
            <section>
              <h3 className="text-lg font-semibold text-brand-400 mb-3 flex items-center gap-2">
                🧠 Spaced Repetition (Anki-style)
              </h3>
              <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                <p className="text-sm text-gray-300 mb-3">
                  When enabled, the app uses the <strong>SM-2 algorithm</strong> to intelligently prioritize questions:
                </p>
                <ul className="text-sm text-gray-400 space-y-2">
                  <li><span className="text-danger-400">1.</span> Questions you got <strong>wrong</strong> appear more frequently</li>
                  <li><span className="text-warning-400">2.</span> Questions you answer <strong>slowly</strong> get more review</li>
                  <li><span className="text-success-400">3.</span> Questions you know well appear <strong>less often</strong> over time</li>
                  <li><span className="text-brand-400">4.</span> New unseen questions are <strong>introduced gradually</strong></li>
                </ul>
                <p className="text-xs text-gray-500 mt-3">
                  Works with both Test and Learn modes. Toggle it in the sidebar configuration.
                </p>
              </div>
            </section>

            {/* Configuration Options */}
            <section>
              <h3 className="text-lg font-semibold text-brand-400 mb-3 flex items-center gap-2">
                ⚙️ Configuration Options
              </h3>
              <div className="space-y-3">
                <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                  <h4 className="font-semibold text-gray-200 text-sm">Subject</h4>
                  <p className="text-xs text-gray-400 mt-1">
                    Each uploaded CSV file becomes a separate subject with its own progress tracking.
                  </p>
                </div>
                <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                  <h4 className="font-semibold text-gray-200 text-sm">Topic Filter</h4>
                  <p className="text-xs text-gray-400 mt-1">
                    Focus on specific topics from your quiz, or select "All Topics" to include everything.
                  </p>
                </div>
                <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                  <h4 className="font-semibold text-gray-200 text-sm">Question Order</h4>
                  <p className="text-xs text-gray-400 mt-1">
                    <strong>Sequential:</strong> Questions in CSV order. <strong>Random:</strong> Shuffled order for better recall testing.
                  </p>
                </div>
                <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                  <h4 className="font-semibold text-gray-200 text-sm">Question Count & Time Limit</h4>
                  <p className="text-xs text-gray-400 mt-1">
                    Set how many questions per session and optional time limit. When time runs out, quiz auto-submits.
                  </p>
                </div>
              </div>
            </section>

            {/* CSV Format */}
            <section>
              <h3 className="text-lg font-semibold text-brand-400 mb-3 flex items-center gap-2">
                📄 CSV Format
              </h3>
              <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                <p className="text-sm text-gray-300 mb-3">Your CSV file should have these columns:</p>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-gray-600">
                        <th className="text-left py-2 px-2 text-brand-400">Column</th>
                        <th className="text-left py-2 px-2 text-brand-400">Description</th>
                      </tr>
                    </thead>
                    <tbody className="text-gray-400">
                      <tr className="border-b border-gray-700"><td className="py-2 px-2 font-mono">topic_name</td><td className="py-2 px-2">Category/topic for the question</td></tr>
                      <tr className="border-b border-gray-700"><td className="py-2 px-2 font-mono">question</td><td className="py-2 px-2">The question text</td></tr>
                      <tr className="border-b border-gray-700"><td className="py-2 px-2 font-mono">option_a</td><td className="py-2 px-2">First option</td></tr>
                      <tr className="border-b border-gray-700"><td className="py-2 px-2 font-mono">option_b</td><td className="py-2 px-2">Second option</td></tr>
                      <tr className="border-b border-gray-700"><td className="py-2 px-2 font-mono">option_c</td><td className="py-2 px-2">Third option</td></tr>
                      <tr className="border-b border-gray-700"><td className="py-2 px-2 font-mono">option_d</td><td className="py-2 px-2">Fourth option</td></tr>
                      <tr className="border-b border-gray-700"><td className="py-2 px-2 font-mono">correct_answer</td><td className="py-2 px-2">A, B, C, or D</td></tr>
                      <tr><td className="py-2 px-2 font-mono">explanation</td><td className="py-2 px-2">Explanation (supports HTML formatting!)</td></tr>
                    </tbody>
                  </table>
                </div>
                <p className="text-xs text-gray-500 mt-3">
                  💡 The explanation column supports HTML tags like &lt;b&gt;, &lt;i&gt;, &lt;code&gt;, &lt;ul&gt;, &lt;table&gt;, etc.
                </p>
              </div>
            </section>

            {/* Features Overview */}
            <section>
              <h3 className="text-lg font-semibold text-brand-400 mb-3 flex items-center gap-2">
                ✨ Features
              </h3>
              <div className="grid md:grid-cols-2 gap-3">
                {[
                  { icon: '📋', title: 'Review Queue', desc: 'Wrong answers collected for focused review' },
                  { icon: '🎯', title: 'Daily Targets', desc: 'Set goals for questions, correct answers, reviews' },
                  { icon: '📊', title: 'Dashboard', desc: 'View performance analytics and test history' },
                  { icon: '📅', title: 'Habit Tracker', desc: 'GitHub-style heatmap of daily practice' },
                  { icon: '🧠', title: 'Expertise Map', desc: 'See mastery level across all topics' },
                  { icon: '📈', title: 'Hourly Activity', desc: 'See when you study best during the day' },
                  { icon: '🔥', title: 'Streak & Motivation', desc: 'Compare today vs yesterday, get motivated' },
                  { icon: '⏱️', title: 'Distraction Detection', desc: 'Analyzes when you lose focus' },
                ].map(f => (
                  <div key={f.title} className="bg-gray-800/50 rounded-lg p-3 border border-gray-700 flex items-start gap-3">
                    <span className="text-xl">{f.icon}</span>
                    <div>
                      <h4 className="font-medium text-gray-200 text-sm">{f.title}</h4>
                      <p className="text-xs text-gray-500">{f.desc}</p>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            {/* Review Queue */}
            <section>
              <h3 className="text-lg font-semibold text-brand-400 mb-3 flex items-center gap-2">
                📋 Review Queue
              </h3>
              <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                <p className="text-sm text-gray-300 mb-3">
                  When you get a question wrong, it's automatically added to your Review Queue for later study.
                </p>
                <ul className="text-sm text-gray-400 space-y-2">
                  <li>• <strong className="text-gray-200">Click the checkmark</strong> to mark an item as reviewed</li>
                  <li>• <strong className="text-gray-200">Expand items</strong> to see full explanations and options</li>
                  <li>• <strong className="text-gray-200">Queue limit</strong> prevents buildup (default 50, adjustable)</li>
                  <li>• <strong className="text-gray-200">Alerts</strong> remind you when queue is getting full</li>
                </ul>
              </div>
            </section>

            {/* Daily Targets */}
            <section>
              <h3 className="text-lg font-semibold text-brand-400 mb-3 flex items-center gap-2">
                🎯 Daily Targets
              </h3>
              <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                <p className="text-sm text-gray-300 mb-3">
                  Set personal daily goals to stay motivated. Configure in the Home page.
                </p>
                <ul className="text-sm text-gray-400 space-y-1">
                  <li>• <strong className="text-gray-200">Questions Target:</strong> Total questions to answer</li>
                  <li>• <strong className="text-gray-200">Correct Target:</strong> Number of correct answers</li>
                  <li>• <strong className="text-gray-200">Review Target:</strong> Review queue items to clear</li>
                  <li>• <strong className="text-gray-200">Streak Goal:</strong> Days to maintain your streak</li>
                </ul>
                <p className="text-xs text-gray-500 mt-3">
                  💡 If you miss targets, the app will show motivational reminders the next day!
                </p>
              </div>
            </section>

            {/* Rank Simulation */}
            <section>
              <h3 className="text-lg font-semibold text-brand-400 mb-3 flex items-center gap-2">
                🏅 Rank Simulation
              </h3>
              <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                <p className="text-sm text-gray-300 mb-3">
                  Estimate your exam rank based on your quiz performance using real marks-to-rank data.
                </p>
                <ul className="text-sm text-gray-400 space-y-2">
                  <li>• <strong className="text-gray-200">Projected Marks:</strong> Your score scaled to the exam's question count (e.g., 50Q test → 200Q exam)</li>
                  <li>• <strong className="text-gray-200">Estimated Rank:</strong> Interpolated from your marks→rank data table</li>
                  <li>• <strong className="text-gray-200">Edit Data:</strong> Click "Edit Rank Data" in Dashboard to add real cutoff data like 700→2000, 650→5000, etc.</li>
                  <li>• <strong className="text-gray-200">Multiple Exams:</strong> Configure different exams with different scoring schemes</li>
                </ul>
                <p className="text-xs text-gray-500 mt-3">
                  💡 Comes pre-loaded with sample data for a 200Q (+4/−1) exam. Update with your real exam cutoffs for accurate predictions!
                </p>
              </div>
            </section>

            {/* Data Management */}
            <section>
              <h3 className="text-lg font-semibold text-brand-400 mb-3 flex items-center gap-2">
                💾 Data Management
              </h3>
              <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-700">
                <ul className="text-sm text-gray-400 space-y-2">
                  <li><strong className="text-gray-200">Auto-Save:</strong> All progress saves automatically to browser localStorage</li>
                  <li><strong className="text-gray-200">Export JSON:</strong> Download a backup of all your data</li>
                  <li><strong className="text-gray-200">Import JSON:</strong> Restore data from a backup or transfer between devices</li>
                </ul>
                <p className="text-xs text-warning-500 mt-3">
                  ⚠️ Clearing browser data will erase your progress. Use Export to backup regularly!
                </p>
              </div>
            </section>

          </div>
        </div>
      </div>
    </>
  );
}
