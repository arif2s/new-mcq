document.addEventListener('DOMContentLoaded', () => {
    // Search and Modal
    const globalSearchInput = document.getElementById('global-search');
    const missingDbModal = document.getElementById('missing-db-modal');
    const closeModalBtn = document.getElementById('close-modal-btn');
    const goToSettingsBtn = document.getElementById('go-to-settings-btn');

    if (globalSearchInput) {
        globalSearchInput.addEventListener('keydown', async (e) => {
            if (e.key === 'Enter') {
                const query = globalSearchInput.value.trim();
                if (!query) return;

                // Check if Tantivy index exists
                try {
                    const response = await fetch('/api/search/diagnostics/tantivy');
                    const data = await response.json();

                    if (!data.exists) {
                        if (missingDbModal) missingDbModal.style.display = 'flex';
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

                        const inspectorContent = document.getElementById('inspector-content');
                        if (inspectorContent) {
                            inspectorContent.innerHTML = `<strong>Search Result:</strong><br/>${searchData.top_hit ? searchData.top_hit.title : 'None found'}`;
                        } else {
                            alert(`Search successful. Top hit: ${searchData.top_hit ? searchData.top_hit.title : 'None'}`);
                        }
                    }
                } catch (err) {
                    console.error("Error checking tantivy index:", err);
                }
            }
        });
    }

    if (closeModalBtn && missingDbModal) {
        closeModalBtn.addEventListener('click', () => {
            missingDbModal.style.display = 'none';
        });
    }

    if (goToSettingsBtn && missingDbModal) {
        goToSettingsBtn.addEventListener('click', () => {
            missingDbModal.style.display = 'none';
            // Trigger settings nav btn click
            const navSettingsBtn = document.querySelector(`.nav-btn[data-target="settings-view"]`);
            if (navSettingsBtn) navSettingsBtn.click();
        });
    }

    // Build Database
    const buildDbBtn = document.getElementById('build-db-btn');
    const buildProgressContainer = document.getElementById('build-progress-container');
    const buildProgress = document.getElementById('build-progress');
    const buildStatus = document.getElementById('build-status');

    if (buildDbBtn) {
        buildDbBtn.addEventListener('click', async () => {
            buildDbBtn.disabled = true;
            if (buildProgressContainer) buildProgressContainer.style.display = 'block';
            if (buildProgress) {
                buildProgress.style.width = '10%'; // indeterminate start
                // Add indeterminate animation class
                buildProgress.classList.add('progress-indeterminate');
            }
            if (buildStatus) {
                buildStatus.style.display = 'block';
                buildStatus.textContent = 'Initiating build...';
            }

            try {
                // FIXED: Changed from /api/sources/run_indexing to /api/sources/index
                const response = await fetch('/api/sources/index', {
                    method: 'POST'
                });
                const data = await response.json();

                if (data.status === 'started') {
                    if (buildStatus) buildStatus.textContent = 'Build running... check backend logs for exact progress.';

                    // Simulate completion
                    setTimeout(() => {
                        if (buildProgress) {
                            buildProgress.classList.remove('progress-indeterminate');
                            buildProgress.style.width = '100%';
                        }
                        if (buildStatus) buildStatus.textContent = 'Build request completed (indexing may still be running in background).';
                        buildDbBtn.disabled = false;
                        if (window.refreshDiagnostics) window.refreshDiagnostics();
                    }, 5000);
                } else {
                    if (buildStatus) buildStatus.textContent = 'Failed to start build.';
                    buildDbBtn.disabled = false;
                    if (buildProgress) buildProgress.classList.remove('progress-indeterminate');
                }
            } catch (err) {
                console.error("Error building DB:", err);
                if (buildStatus) buildStatus.textContent = 'Error starting build.';
                buildDbBtn.disabled = false;
                if (buildProgress) buildProgress.classList.remove('progress-indeterminate');
            }
        });
    }

    // Diagnostics
    const diagLmStudio = document.getElementById('diag-lm-studio');
    const diagDbRows = document.getElementById('diag-db-rows');
    const diagSumatra = document.getElementById('diag-sumatra');
    const refreshDiagBtn = document.getElementById('refresh-diag-btn');

    async function refreshDiagnostics() {
        if (diagLmStudio) {
            diagLmStudio.textContent = 'Checking...';
            diagLmStudio.className = 'badge bg-warning';
        }
        if (diagDbRows) {
            diagDbRows.textContent = 'Checking...';
            diagDbRows.className = 'badge bg-warning';
        }
        if (diagSumatra) {
            diagSumatra.textContent = 'Checking...';
            diagSumatra.className = 'badge bg-warning';
        }

        try {
            const [lmResp, dbResp, sumatraResp] = await Promise.all([
                fetch('/api/search/diagnostics/lm_studio').catch(() => ({ok:false})),
                fetch('/api/search/diagnostics/db').catch(() => ({ok:false})),
                fetch('/api/search/diagnostics/sumatrapdf').catch(() => ({ok:false}))
            ]);

            if (lmResp.ok) {
                const lmData = await lmResp.json();
                if (diagLmStudio) {
                    diagLmStudio.textContent = lmData.status === 'ok' ? 'Connected' : 'Disconnected / Error';
                    diagLmStudio.className = lmData.status === 'ok' ? 'badge bg-ok' : 'badge bg-warning';
                }
            }

            if (dbResp.ok) {
                const dbData = await dbResp.json();
                if (diagDbRows) {
                    diagDbRows.textContent = dbData.status === 'ok' ? `${dbData.row_count} rows in key tables` : 'Error fetching counts';
                    diagDbRows.className = dbData.status === 'ok' ? 'badge bg-ok' : 'badge bg-warning';
                }
            }

            if (sumatraResp.ok) {
                const sumatraData = await sumatraResp.json();
                if (diagSumatra) {
                    diagSumatra.textContent = sumatraData.available ? 'Available' : 'Not Found';
                    diagSumatra.className = sumatraData.available ? 'badge bg-ok' : 'badge bg-warning';
                }
            }
        } catch (err) {
            console.error("Error fetching diagnostics:", err);
        }
    }

    // Export function globally so app.js can call it on tab switch
    window.refreshDiagnostics = refreshDiagnostics;

    if (refreshDiagBtn) {
        refreshDiagBtn.addEventListener('click', refreshDiagnostics);
    }
});
