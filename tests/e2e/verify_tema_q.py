import asyncio
from agents.tema_q.pipeline import run_tema_q_synthesis
import os

async def main():
    search_results = {
        "pdf": [
            {"title": "Test 1", "file_path": "test_parser.pdf", "page": 1},
            {"title": "Test 2", "file_path": "test_parser2.pdf", "page": 1}
        ]
    }

    # Run test synthesis, simulating LM
    res = await run_tema_q_synthesis("Test Topic Discrepancy", search_results, skip_lm=False)

    print("Done")

if __name__ == "__main__":
    # We will just verify it writes to storage/reports
    # Wait, LM_STUDIO might not be available. We should check if it handles it.
    pass
