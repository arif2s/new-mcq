import requests
import json
import uvicorn
import multiprocessing
import time
from server.app import app

def run_server():
    uvicorn.run(app, host="127.0.0.1", port=8085, log_level="error")

if __name__ == '__main__':
    p = multiprocessing.Process(target=run_server)
    p.start()
    time.sleep(3)
    try:
        response = requests.post(
            "http://127.0.0.1:8085/api/mcq/topics/extract",
            json={
                "mcq_text": "The patient has dyspnea but no asthma. They were prescribed amoxicillin.",
                "use_zim_refinement": False,
                "use_tantivy_refinement": False
            }
        )
        print("Status code:", response.status_code)
        print("Response:", response.json())
    finally:
        p.terminate()
        p.join()
