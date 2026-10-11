with open('agents/tema_q/pipeline.py', 'r') as f:
    lines = f.readlines()

new_lines = []
skip = False
for i, line in enumerate(lines):
    if skip:
        skip = False
        continue
    if "html_points += f\"<li>{point_text} {badge}</li>" in line:
        new_lines.append('        html_points += f"<li>{point_text} {badge}</li>\\n"\n')
        skip = True
    elif "html_content = f\\\"\\\"\\\"<!DOCTYPE html>" in line:
        new_lines.append('    html_content = f"""<!DOCTYPE html>\n')
    elif "</html>\\n\\\"\\\"\\\"" in line:
        new_lines.append('</html>\n"""\n')
    else:
        new_lines.append(line)

with open('agents/tema_q/pipeline.py', 'w') as f:
    f.writelines(new_lines)
