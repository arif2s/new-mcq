with open("src/components/Sidebar.tsx", "r") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "TopicEditor" in line:
        print("------- LINE", i)
        start = max(0, i - 10)
        for j in range(start, i + 10):
            print(lines[j], end="")
        print("\n\n")
