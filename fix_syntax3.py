with open('agents/tema_q/pipeline.py', 'r') as f:
    content = f.read()

content = content.replace('</html>\n\\"\\"\\"', '</html>\n"""')

with open('agents/tema_q/pipeline.py', 'w') as f:
    f.write(content)
