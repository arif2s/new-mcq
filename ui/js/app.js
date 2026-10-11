document.addEventListener('DOMContentLoaded', () => {
    const extractBtn = document.getElementById('extract-btn');
    const mcqTextEl = document.getElementById('mcq-text');
    const useZimEl = document.getElementById('use-zim-refinement');
    const useTantivyEl = document.getElementById('use-tantivy-refinement');
    const topicsContainer = document.getElementById('topics-container');

    extractBtn.addEventListener('click', async () => {
        const mcqText = mcqTextEl.value.trim();
        const useZimRefinement = useZimEl.checked;
        const useTantivyRefinement = useTantivyEl.checked;

        if (!mcqText) {
            alert('Please enter some MCQ text.');
            return;
        }

        // Show loading state
        extractBtn.disabled = true;
        extractBtn.textContent = 'Extracting...';
        topicsContainer.innerHTML = '';

        try {
            const response = await fetch('/api/mcq/topics/extract', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    mcq_text: mcqText,
                    use_zim_refinement: useZimRefinement,
                    use_tantivy_refinement: useTantivyRefinement
                })
            });

            if (!response.ok) {
                throw new Error(`Server error: ${response.statusText}`);
            }

            const data = await response.json();
            const topics = data.topics || [];

            renderTopics(topics);
        } catch (error) {
            console.error('Error extracting topics:', error);
            alert('Failed to extract topics. Please check the console for details.');
        } finally {
            extractBtn.disabled = false;
            extractBtn.textContent = 'Extract Entities';
        }
    });

    function renderTopics(topics) {
        if (topics.length === 0) {
            const emptyMsg = document.createElement('div');
            emptyMsg.textContent = 'No entities found.';
            emptyMsg.style.color = '#666';
            topicsContainer.appendChild(emptyMsg);
            return;
        }

        topics.forEach(topic => {
            const chip = document.createElement('div');
            chip.className = 'topic-chip';
            chip.textContent = topic;

            // Add click interaction for validation
            chip.addEventListener('click', () => {
                alert(`Selected topic: ${topic}`);
            });

            topicsContainer.appendChild(chip);
        });
    }
});