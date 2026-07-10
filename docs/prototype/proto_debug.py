#!/usr/bin/env python3
"""Debug: show all raw text from a specific artboard."""
import json, re, sys

def load_page_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    match = re.search(r'window\[[\'"]([^\'"]*)[\'"]\]\s*=\s*(\[.*)', content, re.DOTALL)
    if match:
        json_str = match.group(2)
        bc = 0
        end = len(json_str)
        for i, c in enumerate(json_str):
            if c == '[': bc += 1
            elif c == ']':
                bc -= 1
                if bc == 0: end = i + 1; break
        return json.loads(json_str[:end])
    return []

def show_all_texts(comp, depth=0):
    results = []
    ct = comp.get('type', '')
    cn = comp.get('name', '')
    for field in ('text', 'content', 'html', 'title', 'label', 'placeholder', 'description', 'hint'):
        if field in comp:
            val = comp[field]
            if isinstance(val, str) and val.strip():
                results.append((depth, ct, cn, field, val))
    for ck in ('components', 'children', 'items', 'elements', 'subItems', 'nodes', 'slots'):
        if ck in comp and isinstance(comp[ck], list):
            for child in comp[ck]:
                if isinstance(child, dict):
                    results.extend(show_all_texts(child, depth+1))
    return results

# Check the empty pages
files_to_check = [
    '/Users/mac/workspace/pcids/prototype/data/pages/nkP4eEc0N.js',  # operationlog
    '/Users/mac/workspace/pcids/prototype/data/pages/6BDq9DJlYI.js',  # workbench
    '/Users/mac/workspace/pcids/prototype/data/pages/8lnPMQDrO.js',   # repository
    '/Users/mac/workspace/pcids/prototype/data/pages/P68_iZJVC.js',   # user
    '/Users/mac/workspace/pcids/prototype/data/pages/lTJCcJGwC.js',   # injection
    '/Users/mac/workspace/pcids/prototype/data/pages/wfBf9kfTk.js',   # burning
]

for filepath in files_to_check:
    data = load_page_file(filepath)
    fname = filepath.split('/')[-1]
    print(f"\n{'='*60}")
    print(f"FILE: {fname}")
    print(f"{'='*60}")
    for ab in data:
        name = ab.get('name', '?')
        texts = show_all_texts(ab)
        print(f"\n  Artboard: {name} ({len(texts)} text-bearing components)")
        for depth, ct, cn, field, val in texts:
            indent = "    " * (depth + 1)
            cleaned = val
            if field in ('text', 'content', 'html'):
                cleaned = re.sub(r'<[^>]+>', '', val).strip()
                cleaned = cleaned.replace('&quot;', '"').replace('&nbsp;', ' ')
            print(f"{indent}[{ct}] {field}={repr(cleaned[:100])}")
