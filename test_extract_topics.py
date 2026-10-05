import requests

try:
    res = requests.post("http://localhost:8000/api/queue/extract_topics")
    print(res.status_code)
    print(res.json())
except Exception as e:
    print(e)
