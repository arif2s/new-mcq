/**
 * Native PDF Parsing QA Engine
 * Creates a split-screen DOM layout: Original PDF vs Parsed Markdown
 */

document.addEventListener("DOMContentLoaded", () => {
    // Scoped Mount Point to prevent destroying the React SPA layout
    let mountPoint = document.getElementById("qa-mount-point");

    // If not found, gracefully fallback by creating a new scoped container
    // instead of appending to body directly which might push UI out of view
    if (!mountPoint) {
        mountPoint = document.createElement("div");
        mountPoint.id = "qa-mount-point"; // Fixed ID to match search condition

        // Ensure the fallback qa-mount-point is appended to a Right Inspector pane if it exists
        // The blueprint defines a 450px Right Inspector Pane.
        const rightPane = document.getElementById("right-inspector-pane") || document.body;
        rightPane.appendChild(mountPoint);
    } else {
        // Clear previous instances if hot-reloading
        mountPoint.innerHTML = '';
    }

    // Use standard CSS flexbox/grid for native layout. Side-by-side = row.
    const container = document.createElement("div");
    container.style.display = "flex";
    container.style.flexDirection = "row"; // Side-by-side
    container.style.height = "100%";
    container.style.width = "100%";

    // Left: Original PDF
    const leftPane = document.createElement("div");
    leftPane.style.flex = "1";
    leftPane.style.borderRight = "2px solid #ccc";
    leftPane.style.padding = "20px";
    leftPane.style.overflowY = "auto";
    leftPane.innerHTML = `
        <h2>Original PDF (Raw Text/Canvas)</h2>
        <textarea id="qa-original-text" style="width:100%; height:150px; font-family:monospace; padding:10px; box-sizing:border-box;" placeholder="Paste original text here..."></textarea>
    `;

    // Right: Parsed Markdown
    const rightPane = document.createElement("div");
    rightPane.style.flex = "1";
    rightPane.style.padding = "20px";
    rightPane.style.overflowY = "auto";
    rightPane.style.position = "relative";
    rightPane.innerHTML = `
        <h2>Parsed Markdown</h2>
        <textarea id="qa-parsed-text" style="width:100%; height:150px; font-family:monospace; padding:10px; box-sizing:border-box;" placeholder="Paste parsed text here..."></textarea>
        <button id="qa-btn-metrics" style="margin-top:10px; padding:10px 20px; font-size:16px; cursor:pointer;">Calculate QA Metrics</button>
        <div id="qa-metrics-output" style="margin-top:20px; padding:10px; background-color:#f8f9fa; border:1px solid #ddd; min-height:80px;"></div>
    `;

    container.appendChild(leftPane);
    container.appendChild(rightPane);
    mountPoint.appendChild(container);

    // native CSS highlights (via selection API or standard span tags)
    // To implement "layout shifts" we add a basic highlight class
    const style = document.createElement("style");
    style.textContent = `
        .highlight-shift { background-color: #ffeeba; border: 1px solid #ffc107; }
    `;
    document.head.appendChild(style);

    document.getElementById("qa-btn-metrics").addEventListener("click", async () => {
        const origText = document.getElementById("qa-original-text").value;
        const parsedText = document.getElementById("qa-parsed-text").value;

        try {
            const response = await fetch('/api/notes/qa_metrics', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    original_text: origText,
                    parsed_text: parsedText
                })
            });
            const data = await response.json();

            const outDiv = document.getElementById("qa-metrics-output");
            outDiv.innerHTML = `
                <strong>Metrics:</strong><br/>
                Char Ratio: ${data.char_ratio}<br/>
                Token Ratio: ${data.token_ratio}<br/>
                Levenshtein Dist: <span class="highlight-shift">${data.levenshtein_distance}</span>
            `;
        } catch (e) {
            console.error("QA Metrics Error:", e);
            document.getElementById("qa-metrics-output").innerHTML = `<span style="color:red">Error: ${e.message}</span>`;
        }
    });
});
