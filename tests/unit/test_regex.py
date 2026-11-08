import re
text = """Here is your JSON:
```json
[
  {"point": "test", "source": "test", "page": 1}
]
```
Hope this helps!
"""

match = re.search(r'\[.*\]', text, re.DOTALL)
if match:
    print("MATCHED:", match.group(0))
else:
    print("NO MATCH")
