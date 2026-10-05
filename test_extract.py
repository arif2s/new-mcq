import requests
import json

res = requests.post("http://localhost:8000/api/queue/extract_topics", json={
    "questions": [
        {
            "question": "What is 2+2?",
            "option_a": "3",
            "option_b": "4",
            "option_c": "5",
            "option_d": "6",
            "correct_answer": "B",
            "explanation": "Math",
            "topic_name": "Math"
        }
    ]
})
print(res.json())
