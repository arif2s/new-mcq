document.addEventListener('DOMContentLoaded', () => {
    // Navigation
    const navHomeBtn = document.getElementById('nav-home-btn');
    const navSettingsBtn = document.getElementById('nav-settings-btn');
    const homeView = document.getElementById('home-view');
    const settingsView = document.getElementById('settings-view');

    navHomeBtn.addEventListener('click', () => {
        homeView.style.display = 'block';
        settingsView.style.display = 'none';
    });

    navSettingsBtn.addEventListener('click', () => {
        homeView.style.display = 'none';
        settingsView.style.display = 'block';
        refreshDiagnostics();
    });

    // Search and Modal
    const globalSearchInput = document.getElementById('global-search');
    const missingDbModal = document.getElementById('missing-db-modal');
    const closeModalBtn = document.getElementById('close-modal-btn');
    const goToSettingsBtn = document.getElementById('go-to-settings-btn');

    globalSearchInput.addEventListener('keydown', async (e) => {
        if (e.key === 'Enter') {
            const query = globalSearchInput.value.trim();
            if (!query) return;

            // Check if Tantivy index exists
            try {
                const response = await fetch('/api/search/diagnostics/tantivy');
                const data = await response.json();

                if (!data.exists) {
                    missingDbModal.style.display = 'flex';
                } else {
                    // Perform search
                    const searchResp = await fetch('/api/search', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({ query: query })
                    });
                    const searchData = await searchResp.json();
                    console.log('Search Results:', searchData);
                    alert(`Search successful. Top hit: ${searchData.top_hit ? searchData.top_hit.title : 'None'}`);
                }
            } catch (err) {
                console.error("Error checking tantivy index:", err);
            }
        }
    });

    closeModalBtn.addEventListener('click', () => {
        missingDbModal.style.display = 'none';
    });

    goToSettingsBtn.addEventListener('click', () => {
        missingDbModal.style.display = 'none';
        navSettingsBtn.click();
    });

    // Build Database
    const buildDbBtn = document.getElementById('build-db-btn');
    const buildProgress = document.getElementById('build-progress');
    const buildStatus = document.getElementById('build-status');

    buildDbBtn.addEventListener('click', async () => {
        buildDbBtn.disabled = true;
        buildProgress.style.display = 'block';
        buildProgress.removeAttribute('value'); // indeterminate state initially
        buildStatus.style.display = 'block';
        buildStatus.textContent = 'Initiating build...';

        try {
            const response = await fetch('/api/sources/run_indexing', {
                method: 'POST'
            });
            const data = await response.json();

            if (data.status === 'started') {
                buildStatus.textContent = 'Build running... check backend logs for exact progress.';
                // If there's a websocket for progress, we could connect to it here.
                // For now, simulate completion after some time or let user refresh diagnostics.
                setTimeout(() => {
                    buildProgress.value = 100;
                    buildStatus.textContent = 'Build request completed (indexing may still be running in background).';
                    buildDbBtn.disabled = false;
                    refreshDiagnostics();
                }, 5000);
            } else {
                buildStatus.textContent = 'Failed to start build.';
                buildDbBtn.disabled = false;
            }
        } catch (err) {
            console.error("Error building DB:", err);
            buildStatus.textContent = 'Error starting build.';
            buildDbBtn.disabled = false;
        }
    });

    // Diagnostics
    const diagLmStudio = document.getElementById('diag-lm-studio');
    const diagDbRows = document.getElementById('diag-db-rows');
    const diagSumatra = document.getElementById('diag-sumatra');
    const refreshDiagBtn = document.getElementById('refresh-diag-btn');

    async function refreshDiagnostics() {
        diagLmStudio.textContent = 'Checking...';
        diagDbRows.textContent = 'Checking...';
        diagSumatra.textContent = 'Checking...';

        try {
            const [lmResp, dbResp, sumatraResp] = await Promise.all([
                fetch('/api/search/diagnostics/lm_studio'),
                fetch('/api/search/diagnostics/db'),
                fetch('/api/search/diagnostics/sumatrapdf')
            ]);

            const lmData = await lmResp.json();
            diagLmStudio.textContent = lmData.status === 'ok' ? 'Connected' : 'Disconnected / Error';

            const dbData = await dbResp.json();
            diagDbRows.textContent = dbData.status === 'ok' ? `${dbData.row_count} rows in key tables` : 'Error fetching counts';

            const sumatraData = await sumatraResp.json();
            diagSumatra.textContent = sumatraData.available ? 'Available' : 'Not Found';
        } catch (err) {
            console.error("Error fetching diagnostics:", err);
        }
    }

    refreshDiagBtn.addEventListener('click', refreshDiagnostics);
});
