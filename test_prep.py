import requests

try:
    res = requests.post("http://localhost:8000/api/queue/extract_topics", json={
        "questions": [{'id': 'q_1_5dbf0121', 'topic_name': 'Python Basics', 'question': 'Which keyword is used to create a function?', 'option_a': 'function', 'option_b': 'def', 'option_c': 'func', 'option_d': 'define', 'correct_answer': 'B', 'explanation': 'The "def" keyword is used to define functions in Python.'}]
    })
    print(res.status_code)
    print(res.json())
except Exception as e:
    print(e)
