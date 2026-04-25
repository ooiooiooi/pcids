#!/usr/bin/env python3
import json, re
for fname in ['nkP4eEc0N.js', '4FyJSe-ez.js']:
    path = f'/Users/mac/workspace/pcids/prototype/data/pages/{fname}'
    with open(path) as f:
        content = f.read()
    match = re.search(r'window\[[\'"]([^\'"]*)[\'"]\]\s*=\s*(\[.*)', content, re.DOTALL)
    json_str = match.group(2)
    bc = 0
    end = len(json_str)
    for i, c in enumerate(json_str):
        if c == '[': bc += 1
        elif c == ']':
            bc -= 1
            if bc == 0: end = i + 1; break
    data = json.loads(json_str[:end])
    print(f"\n=== {fname} ({len(data)} artboards) ===")
    for ab in data:
        name = ab.get('name', '?')
        print(f"  Artboard: {name} ({len(ab.get('components',[]))} components)")
        # Show all text-bearing components recursively
        def show(comp, depth=0):
            indent = "    " * (depth + 1)
            for field in ('text', 'content', 'html', 'title', 'label', 'placeholder'):
                if field in comp:
                    val = comp[field]
                    if isinstance(val, str) and val.strip():
                        cleaned = re.sub(r'<[^>]+>', '', val).strip()[:80]
                        print(f"{indent}[{comp.get('type','?')}] {field}={repr(cleaned)}")
            for ck in ('components', 'children', 'items', 'elements', 'subItems', 'nodes', 'slots'):
                if ck in comp and isinstance(comp[ck], list):
                    for child in comp[ck]:
                        if isinstance(child, dict):
                            show(child, depth+1)
        for comp in ab.get('components', []):
            show(comp)
