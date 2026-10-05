import re

with open('src/components/Sidebar.tsx', 'r') as f:
    content = f.read()

# Add imports for TopicEditor
import_str = """
import TopicEditor from './TopicEditor';
"""
content = re.sub(r'(import { saveSession } from \'../api\';)', r'\1\n' + import_str, content)

# Add state for extracted topics in the Sidebar component
state_str = """
  const [extractedTopicsData, setExtractedTopicsData] = useState<any[] | null>(null);
  const [dbExists, setDbExists] = useState<boolean>(false);
"""
content = content.replace('  const [dbExists, setDbExists] = useState<boolean>(false);', state_str)


# Modify handleProcessRemote
old_process = """
    try {
      const res = await fetch('/api/mcq/generate_set', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          subjects: selectedRemoteSubjects,
          topics: selectedRemoteTopics,
          keyword,
          count: questionCount
        })
      });
      const data = await res.json();
      if (data.status === 'success' && data.questions.length > 0) {
        const sessionId = `prep-${Date.now()}`;
        const dummyResult = {
          id: sessionId,
          subjectName: 'Offline Prep: ' + (selectedRemoteSubjects.length ? selectedRemoteSubjects.join(', ') : 'All'),
          date: new Date().toISOString(),
          timestamp: Date.now(),
          topics: selectedRemoteTopics.length ? selectedRemoteTopics : ['Various'],
          totalQuestions: data.questions.length,
          correctAnswers: 0,
          wrongAnswers: 0,
          unanswered: data.questions.length,
          score: 0,
          maxScore: data.questions.length,
          accuracy: 0,
          mode: 'offline_prep',
          timeLimitSeconds: 0,
          timeUsedSeconds: 0,
          answers: []
        };
        await saveSession(dummyResult as any, data.questions);

        const prepRes = await fetch('/api/queue/prepare', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({questions: data.questions})
        });
        if (prepRes.ok) {
          alert('Questions queued for preparation and added to Knowledge Hub!');
        }
      } else {
         alert('No matching questions to process.');
      }
    } catch (e) {
      console.error(e);
    }
"""

new_process = """
    try {
      const res = await fetch('/api/mcq/generate_set', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          subjects: selectedRemoteSubjects,
          topics: selectedRemoteTopics,
          keyword,
          count: questionCount
        })
      });
      const data = await res.json();
      if (data.status === 'success' && data.questions.length > 0) {
        // Step 2: Extract topics before queuing
        const extractRes = await fetch('/api/queue/extract_topics', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ questions: data.questions })
        });
        const extractData = await extractRes.json();

        if (extractData.status === 'success') {
          // Temporarily hold data for the Topic Editor modal to render
          setExtractedTopicsData(extractData.questions);
        } else {
          alert('Failed to extract topics.');
        }

      } else {
         alert('No matching questions to process.');
      }
    } catch (e) {
      console.error(e);
    }
"""
content = content.replace(old_process, new_process)

# Add handler for confirm
confirm_handler = """
  const handleConfirmTopics = async (finalQuestions: any[]) => {
    setExtractedTopicsData(null);
    try {
      const sessionId = `prep-${Date.now()}`;

      // We pass the updated finalQuestions back into the saveSession flow so they include curated topics
      const dummyResult = {
        id: sessionId,
        subjectName: 'Offline Prep: ' + (selectedRemoteSubjects.length ? selectedRemoteSubjects.join(', ') : 'All'),
        date: new Date().toISOString(),
        timestamp: Date.now(),
        topics: selectedRemoteTopics.length ? selectedRemoteTopics : ['Various'],
        totalQuestions: finalQuestions.length,
        correctAnswers: 0,
        wrongAnswers: 0,
        unanswered: finalQuestions.length,
        score: 0,
        maxScore: finalQuestions.length,
        accuracy: 0,
        mode: 'offline_prep',
        timeLimitSeconds: 0,
        timeUsedSeconds: 0,
        answers: []
      };

      // Update original questions with curated topics, wait, saveSession takes generic array.
      // But saveSession in API uses the frontend fields to generate. Actually saveSession sends it as is.
      await saveSession(dummyResult as any, finalQuestions);

      const prepRes = await fetch('/api/queue/prepare', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({questions: finalQuestions})
      });
      if (prepRes.ok) {
        alert('Questions queued for preparation and added to Knowledge Hub!');
      }
    } catch (e) {
      console.error(e);
    }
  };
"""

content = content.replace('  const handleDownloadSample = () => {', confirm_handler + '\n  const handleDownloadSample = () => {')

# Render TopicEditor in the JSX
# Add it near the end, just before the closing </div>
render_modal = """
      {/* Topic Editor Modal */}
      {extractedTopicsData && (
        <TopicEditor
          questions={extractedTopicsData}
          onConfirm={handleConfirmTopics}
          onCancel={() => setExtractedTopicsData(null)}
        />
      )}
    </div>
"""
content = content.rsplit('    </div>', 1)[0] + render_modal

with open('src/components/Sidebar.tsx', 'w') as f:
    f.write(content)
