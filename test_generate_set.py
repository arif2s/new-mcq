import requests

try:
    res = requests.post("http://localhost:8000/api/mcq/generate_set", json={
        "subjects": [],
        "topics": [],
        "keyword": "",
        "count": 5
    })
    print(res.status_code)
    print(res.json())
except Exception as e:
    print(e)
