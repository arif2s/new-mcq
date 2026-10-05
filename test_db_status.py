import requests

try:
    res = requests.get("http://localhost:8000/api/mcq/db_status")
    print(res.json())
except Exception as e:
    print(e)
