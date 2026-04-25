#!/usr/bin/env python3
"""Comprehensive inspection of all prototype pages, writing structured data to file."""
import json, re, os

PAGES_DIR = '/Users/mac/workspace/pcids/prototype/data/pages'
OUTPUT = '/Users/mac/workspace/pcids/proto_detailed.txt'

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

def find_structured(comp, depth=0):
    results = []
    ct = comp.get('type', '')
    opts = comp.get('options', [])
    cols = comp.get('columns', [])
    fields = comp.get('fields', [])
    tabs = comp.get('tabs', [])
    menu = comp.get('menuItems', [])
    filters = comp.get('filters', [])
    ph = comp.get('placeholder', '')
    tit = comp.get('title', '')
    lab = comp.get('label', '')
    desc = comp.get('description', '')
    hint = comp.get('hint', '')
    html = comp.get('html', '')
    name = comp.get('name', '')
    has_structured = opts or cols or fields or tabs or menu or filters or ph or tit or lab or desc or hint or html
    if has_structured:
        results.append((depth, ct, name, opts, cols, fields, tabs, menu, filters, ph, tit, lab, desc, hint, html))
    for ck in ('components', 'children', 'items', 'elements', 'subItems', 'nodes', 'slots'):
        if ck in comp and isinstance(comp[ck], list):
            for child in comp[ck]:
                if isinstance(child, dict):
                    results.extend(find_structured(child, depth+1))
    return results

def format_opts(opts):
    result = []
    for o in opts[:30]:
        if isinstance(o, dict):
            result.append(o.get('label', o.get('text', o.get('value', '?'))))
        else:
            result.append(str(o))
    return result

def format_fields(fields):
    result = []
    for f in fields[:30]:
        if isinstance(f, dict):
            entry = {
                'label': f.get('label', f.get('title', '')),
                'type': f.get('type', ''),
                'placeholder': f.get('placeholder', f.get('hint', '')),
                'hint': f.get('hint', ''),
                'required': f.get('required', False),
            }
            if 'options' in f:
                entry['options'] = format_opts(f['options'])
            result.append(entry)
    return result

def format_menu(menu):
    result = []
    for m in menu[:30]:
        if isinstance(m, dict):
            result.append(m.get('label', m.get('text', m.get('title', '?'))))
        else:
            result.append(str(m))
    return result

def format_tabs(tabs):
    result = []
    for t in tabs[:15]:
        if isinstance(t, dict):
            result.append(t.get('label', t.get('title', t.get('name', '?'))))
        else:
            result.append(str(t))
    return result

def format_filters(filters):
    result = []
    for flt in filters:
        if isinstance(flt, dict):
            entry = {'label': flt.get('label', flt.get('title', ''))}
            if 'options' in flt:
                entry['options'] = format_opts(flt['options'])
            result.append(entry)
    return result

all_files = sorted([f for f in os.listdir(PAGES_DIR) if f.endswith('.js')])

with open(OUTPUT, 'w', encoding='utf-8') as out:
    for filename in all_files:
        filepath = os.path.join(PAGES_DIR, filename)
        data = load_page_file(filepath)
        if not data:
            out.write(f"\n{'='*60}\n")
            out.write(f"SKIP: {filename} (empty)\n")
            out.write(f"{'='*60}\n\n")
            continue

        out.write(f"\n{'='*60}\n")
        out.write(f"FILE: {filename}\n")
        out.write(f"{'='*60}\n\n")

        for ab in data:
            name = ab.get('name', '?')
            comps = ab.get('components', [])
            out.write(f"--- Artboard: {name} ({len(comps)} top-level components) ---\n\n")

            structured = find_structured(ab)
            if structured:
                for depth, ct, cname, opts, cols, fields, tabs, menu, filters, ph, tit, lab, desc, hint, html in structured:
                    indent = "  " * depth
                    out.write(f"{indent}[{ct}] name={cname}\n")
                    if ph: out.write(f"{indent}  placeholder: {ph}\n")
                    if tit: out.write(f"{indent}  title: {tit}\n")
                    if lab: out.write(f"{indent}  label: {lab}\n")
                    if desc: out.write(f"{indent}  description: {desc}\n")
                    if hint: out.write(f"{indent}  hint: {hint}\n")
                    if html: out.write(f"{indent}  html: {html[:150]}\n")
                    if opts:
                        out.write(f"{indent}  options ({len(opts)}):\n")
                        for o in format_opts(opts):
                            out.write(f"{indent}    - {o}\n")
                    if cols:
                        out.write(f"{indent}  columns ({len(cols)}):\n")
                        for c in cols:
                            if isinstance(c, dict):
                                entry = f"{indent}    title: {c.get('title', '?')}"
                                extras = []
                                if 'enum' in c: extras.append(f"enum={c['enum']}")
                                if 'filterEnum' in c: extras.append(f"filterEnum={c['filterEnum']}")
                                if 'filters' in c: extras.append(f"filters={c['filters']}")
                                if extras: entry += " " + " ".join(extras)
                                out.write(entry + "\n")
                    if fields:
                        out.write(f"{indent}  fields ({len(fields)}):\n")
                        for f in format_fields(fields):
                            out.write(f"{indent}    {f}\n")
                    if tabs:
                        out.write(f"{indent}  tabs ({len(tabs)}):\n")
                        for t in format_tabs(tabs):
                            out.write(f"{indent}    - {t}\n")
                    if menu:
                        out.write(f"{indent}  menuItems ({len(menu)}):\n")
                        for m in format_menu(menu):
                            out.write(f"{indent}    - {m}\n")
                    if filters:
                        out.write(f"{indent}  filters ({len(filters)}):\n")
                        for fl in format_filters(filters):
                            out.write(f"{indent}    {fl}\n")
                    out.write("\n")
            else:
                out.write("  (no structured data found - text components only)\n")
            out.write("\n")

print(f"Done! Output written to {OUTPUT}")
print(f"Size: {os.path.getsize(OUTPUT):,} bytes")
