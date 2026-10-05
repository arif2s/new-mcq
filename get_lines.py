with open("src/components/Sidebar.tsx", "r") as f:
    lines = f.readlines()

start = 0
for i, line in enumerate(lines):
    if "const handleProcessRemote" in line:
        start = i
        break

for i in range(start, start + 100):
    print(lines[i], end="")
