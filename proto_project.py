#!/usr/bin/env python3
import json, re
with open('/Users/mac/workspace/pcids/prototype/data/project.js') as f:
    content = f.read()
match = re.search(r'window\[.app.\]=(.*)', content, re.DOTALL)
data = json.loads(match.group(1))
print(f'Project name: {data.get("name", "?")}')
print(f'Pages in project:')
for child in data.get('children', []):
    pid = child.get('_id', '')
    name = child.get('name', '?')
    ptype = child.get('type', '?')
    artboards = child.get('artboards', [])
    ab_names = [a.get('name', '?') for a in artboards]
    fname = f'{pid}.js'
    print(f'  {name} ({ptype}) id={pid} -> {fname} artboards={ab_names}')
