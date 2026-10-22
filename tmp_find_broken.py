import re
with open('jasper_v2.py') as f:
    c = f.read()

# Find print(f" lines that don't close properly
lines = c.split('\n')
for i, line in enumerate(lines):
    stripped = line.strip()
    # Look for print(f starting a string that looks unclosed
    if 'print(f"' in stripped or "print(f'" in stripped:
        # Count if the f-string is properly closed on this line
        opens = stripped.count('print(f"') + stripped.count("print(f'")
        # Simple heuristic: if line has print(f" but doesn't end with ) or " somewhere
        if opens > 0:
            # Check if it's a complete print statement
            if not (stripped.endswith(')') or stripped.endswith('")') or 
                    re.search(r'print\(f"[^"]*"\)$', stripped)):
                print(f"Line {i+1}: {stripped[:120]}")
