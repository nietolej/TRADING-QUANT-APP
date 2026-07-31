"""
Fix indentation of the 'if error in results:' block in strategy_analyzer_page.py
The block was previously inside 'with client:' which has been removed.
All lines in the block need to be dedented by 4 spaces.
"""
import sys

filepath = r'd:\Users\USUARIO\Documents\PROYECTOS IDE\TRADING QUANT APP\web_gui\pages\strategy_analyzer_page.py'

with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find where the malformed block starts: line with 'if .error. in results:' at indent=16
# and the body at indent=24 (should be indent=20)
# Find where it ends: the 'except Exception as e:' at indent=12

start_idx = None
end_idx = None

for i, line in enumerate(lines):
    stripped = line.rstrip()
    indent = len(stripped) - len(stripped.lstrip())
    content = stripped.lstrip()
    
    # Find start: the 'if error' line at indent=16 that is followed by over-indented content
    if indent == 16 and content.startswith("if 'error' in results:") and start_idx is None:
        start_idx = i + 1  # The NEXT line needs dedenting (the body of the if)
        print(f"Found 'if error' at line {i+1} (index {i})")
    
    # Find end: 'except Exception as e:' at indent=12 after the block
    if indent == 12 and content.startswith("except Exception as e:") and start_idx is not None:
        end_idx = i  # exclusive (don't include the except line)
        print(f"Found 'except' at line {i+1} (index {i})")
        break

if start_idx is None or end_idx is None:
    print(f"ERROR: Could not find block boundaries! start_idx={start_idx}, end_idx={end_idx}")
    
    # Debug: show context around line 1714
    for i in range(1712, min(1720, len(lines))):
        stripped = lines[i].rstrip()
        indent = len(stripped) - len(stripped.lstrip())
        print(f"L{i+1:4d} i={indent:2d}: {stripped[:80]}")
    sys.exit(1)

print(f"Block to fix: lines {start_idx+1} to {end_idx} (1-indexed, {end_idx - start_idx} lines)")

# Show first few lines before fixing
print("\nBefore fix (first 5 lines of block):")
for i in range(start_idx, min(start_idx + 5, end_idx)):
    stripped = lines[i].rstrip()
    indent = len(stripped) - len(stripped.lstrip())
    print(f"L{i+1:4d} i={indent:2d}: {stripped[:80]}")

# Fix: dedent each line in the block by 4 spaces (remove leading 4 spaces)
# But only if the line actually has 4 leading spaces to remove
fixed_lines = list(lines)
changes = 0
for i in range(start_idx, end_idx):
    line = fixed_lines[i]
    if line.startswith('    ') and not line.strip() == '':  # has 4 spaces and is not empty
        fixed_lines[i] = line[4:]  # remove first 4 spaces
        changes += 1
    elif line.strip() == '' or line == '\r\n' or line == '\n':
        pass  # empty line, leave as is

print(f"\nFixed {changes} lines")

# Show first few lines after fixing
print("\nAfter fix (first 5 lines of block):")
for i in range(start_idx, min(start_idx + 5, end_idx)):
    stripped = fixed_lines[i].rstrip()
    indent = len(stripped) - len(stripped.lstrip())
    print(f"L{i+1:4d} i={indent:2d}: {stripped[:80]}")

# Write back
with open(filepath, 'w', encoding='utf-8') as f:
    f.writelines(fixed_lines)

print("\nFile written successfully!")

# Verify the file is valid Python
try:
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    import ast
    ast.parse(content)
    print("Python syntax: OK")
except SyntaxError as e:
    print(f"Python syntax ERROR: {e}")
