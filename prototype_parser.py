#!/usr/bin/env python3
"""
Parse MockPlus prototype page data files to extract detailed UI specifications.

MockPlus stores UI as individual text components with HTML in 'text' fields.
No structured data (options, fields, columns) - everything inferred from text patterns.
Many modal dialogs are stored as images (no extractable text).
"""

import os
import json
import re
from collections import defaultdict, OrderedDict

PAGES_DIR = '/Users/mac/workspace/pcids/prototype/data/pages'
OUTPUT_FILE = '/Users/mac/workspace/pcids/prototype_specs.md'

FILE_PAGE_MAP = {
    '4FyJSe-ez.js': 'loginlog',      # 登录日志
    '6BDq9DJlYI.js': 'workbench',    # 工作台
    '6rt6Ww6i_.js': 'product',       # 产品管理
    '890CrRDSF.js': 'operationlog',  # 操作日志
    '8lnPMQDrO.js': 'repository',    # 制品仓库
    'P68_iZJVC.js': 'user',          # 用户管理
    'SjAuaSm3B.js': 'protocol',      # 通信协议验证
    '_i2amO9RA.js': 'role',          # 角色管理
    'cqFZNFsaF.js': 'record',        # 履历记录
    'kpMVjBmAg.js': 'script',        # 脚本管理
    'lTJCcJGwC.js': 'injection',     # 异常注入
    'meZr8FalO.js': 'burner',        # 烧录器管理
    'nMG3IEhFm.js': 'repository_sync', # 制品仓库(1)
    'nkP4eEc0N.js': 'login',         # 登录
    'wfBf9kfTk.js': 'burning',       # 烧录安装管理
}

PAGE_DISPLAY = {
    'login': 'Login (登录)',
    'loginlog': 'Login Log (登录日志)',
    'operationlog': 'Operation Log (操作日志)',
    'workbench': 'Workbench (工作台)',
    'repository': 'Repository (制品仓库)',
    'repository_sync': 'Repository Sync (仓库同步)',
    'product': 'Product (产品管理)',
    'burner': 'Burner (烧录器管理)',
    'burning': 'Burning (烧录安装管理)',
    'script': 'Script (脚本管理)',
    'record': 'Record (履历记录)',
    'injection': 'Injection (异常注入)',
    'protocol': 'Protocol (通信协议验证)',
    'user': 'User (用户管理)',
    'role': 'Role (角色管理)',
}

CALENDAR_DAYS = set(str(i) for i in range(1, 32))
CALENDAR_WEEK = {'一', '二', '三', '四', '五', '六', '日'}

# Known column header patterns (short Chinese phrases that are field names)
HEADER_PATTERNS = [
    '时间', '用户', '地址', '状态', '操作', '类型', '名称', '序号', '编号',
    '结果', '内容', '模块', '账号', '创建', '修改', 'SN', '端口', '物理',
    '权限', '项目', '产品', '烧录器', '脚本', '记录', '协议', '角色',
    '备注', '描述', '信息', '密码', '版本', '路径', '责任人', '执行人',
    '执行人员', '测试对象', '异常类型', '执行状态', '加入时间', '用户账号',
    '用户组', '芯片类型', '板卡名称', '板卡序列号', '板卡图片', '烧录器名称',
    '软件名称', '软件及版本', '烧录安装目标', '版本一致性报告', '发布',
    '日志类型', '登录时间', '操作时间', '登录地址', '操作模块', '操作内容',
    '创建时间', '修改时间', '修改人',
]

NAV_MENU = {
    '工作台', '制品仓库', '产品管理', '烧录器管理', '脚本管理',
    '烧录安装管理', '履历记录', '异常注入', '通信协议验证',
    '系统管理', '用户管理', '角色管理', '登录日志', '操作日志', 'IDE管理',
}

BUTTON_TEXTS = {
    '确定', '取消', '删除', '编辑', '新增', '添加', '搜索', '重置', '清空',
    '保存', '确认', '导入', '导出', '刷新', '关闭', '返回', '提交',
    '登录', '登出', '退出', '同步', '创建', '详情', '启用', '禁用',
    '测试', '执行', '停止', '开始', '完成', '申请', '批准', '拒绝',
    '邀请', '移除', '配置', '上传', '下载', '查看', '复制',
}

STATUS_TEXTS = {
    '成功', '失败', '启用', '禁用', '空闲', '占用', '离线',
    '在线', '通过', '未通过', '进行中', '已完成', '已取消', '已失败',
    '已批准', '已拒绝', '待处理', '待审批', '执行中', '暂停',
}


def strip_html(s):
    if not s: return ''
    s = s.replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>')
    s = s.replace('&amp;', '&').replace('&nbsp;', ' ')
    return re.sub(r'<[^>]+>', '', s).strip()


def is_noise(t):
    t = t.strip()
    if not t or len(t) == 0: return True
    if t in CALENDAR_DAYS or t in CALENDAR_WEEK: return True
    if len(t) <= 2 and t in ('·', '—', '-', '~', '至', ' ') : return True
    return False


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


def extract_all(comp):
    """Recursively extract (text, comp_type, comp_name, source_field) from component tree."""
    results = []
    ct = comp.get('type', '')
    cn = comp.get('name', '')

    for field in ('text', 'content', 'html', 'title', 'label', 'description', 'hint'):
        if field in comp:
            val = comp[field]
            if isinstance(val, str) and val.strip():
                cleaned = strip_html(val) if field in ('text', 'content', 'html', 'title', 'description') else val.strip()
                if cleaned:
                    results.append((cleaned, ct, cn, field))

    if 'placeholder' in comp:
        ph = comp['placeholder']
        if isinstance(ph, str) and ph.strip():
            results.append((ph.strip(), ct, cn, 'placeholder'))

    # Structured data
    for key, cat in [('options', 'option'), ('menuItems', 'menu_item'),
                      ('tabs', 'tab'), ('filters', 'filter_label')]:
        if key in comp and isinstance(comp[key], list):
            for item in comp[key]:
                if isinstance(item, dict):
                    txt = item.get('label', item.get('text', item.get('title', item.get('value', ''))))
                elif isinstance(item, str):
                    txt = item
                else: continue
                if txt and str(txt).strip():
                    results.append((str(txt).strip(), ct, cn, cat))

    if 'columns' in comp and isinstance(comp['columns'], list):
        for col in comp['columns']:
            if isinstance(col, dict):
                t = col.get('title', col.get('label', ''))
                if t and str(t).strip():
                    results.append((str(t).strip(), ct, cn, 'column_header'))
                for ek in ('enum', 'filterEnum'):
                    if ek in col and isinstance(col[ek], list):
                        for e in col[ek]:
                            if isinstance(e, dict):
                                et = e.get('text', e.get('label', ''))
                            else: et = e
                            if et and str(et).strip():
                                results.append((str(et).strip(), ct, cn, 'column_enum'))

    if 'fields' in comp and isinstance(comp['fields'], list):
        for f in comp['fields']:
            if isinstance(f, dict):
                fl = f.get('label', f.get('title', ''))
                if fl and str(fl).strip():
                    results.append((str(fl).strip(), ct, cn, 'field_label'))
                fp = f.get('placeholder', f.get('hint', ''))
                if fp and str(fp).strip():
                    results.append((str(fp).strip(), ct, cn, 'field_placeholder'))
                if 'options' in f:
                    for o in f['options']:
                        if isinstance(o, dict): ot = o.get('label', o.get('text', ''))
                        elif isinstance(o, str): ot = o
                        else: continue
                        if ot and str(ot).strip():
                            results.append((str(ot).strip(), ct, cn, 'field_option'))

    if 'filters' in comp and isinstance(comp['filters'], list):
        for flt in comp['filters']:
            if isinstance(flt, dict):
                if 'options' in flt:
                    for o in flt['options']:
                        if isinstance(o, dict): ot = o.get('label', o.get('text', ''))
                        elif isinstance(o, str): ot = o
                        else: continue
                        if ot and str(ot).strip():
                            results.append((str(ot).strip(), ct, cn, 'filter_option'))

    # Recurse
    for ck in ('components', 'children', 'items', 'elements', 'subItems', 'nodes', 'slots'):
        if ck in comp and isinstance(comp[ck], list):
            for child in comp[ck]:
                if isinstance(child, dict):
                    results.extend(extract_all(child))
    return results


def categorize(text, comp_type, source_field):
    """Categorize text. Returns (category, display_text) or None."""
    t = text.strip()
    if is_noise(t):
        return None

    # Structured source fields
    if source_field == 'placeholder': return ('placeholder', t)
    if source_field == 'option': return ('option', t)
    if source_field == 'column_header': return ('column_header', t)
    if source_field == 'column_enum': return ('column_enum', t)
    if source_field == 'field_label': return ('field_label', t)
    if source_field == 'field_placeholder': return ('field_placeholder', t)
    if source_field == 'field_option': return ('field_option', t)
    if source_field == 'tab': return ('tab', t)
    if source_field == 'menu_item': return ('menu_item', t)
    if source_field == 'filter_label': return ('filter_label', t)
    if source_field == 'filter_option': return ('filter_option', t)
    if source_field == 'hint': return ('hint', t)
    if source_field == 'description': return ('description', t)

    # Component type based
    if comp_type in ('button', 'link', 'buttonGroup', 'linkButton', 'textButton'):
        return ('button', t)

    # Content-based classification
    if source_field in ('text', 'content', 'html'):
        # Button-like
        if t in BUTTON_TEXTS:
            return ('button', t)

        # Navigation menu items
        if t in NAV_MENU:
            return ('nav_menu', t)

        # Date/timestamp
        if re.match(r'^\d{4}[-/]\d{2}[-/]\d{2}', t):
            return ('table_data', t)

        # IP address
        if re.match(r'^\d+\.\d+\.\d+\.\d+', t):
            return ('table_data', t)

        # Email
        if re.match(r'^[\w.+-]+@[\w.-]+\.\w+$', t):
            return ('table_data', t)

        # Serial number / alphanumeric ID (8+ chars)
        if re.match(r'^[A-Za-z0-9\-_]{8,}$', t):
            return ('table_data', t)

        # Status
        if t in STATUS_TEXTS:
            return ('status', t)

        # Pagination
        if re.match(r'^共\s+\d+\s+条', t):
            return ('pagination', t)

        # Column header detection: short phrases (≤10 chars) containing header keywords
        if len(t) <= 12:
            for hp in HEADER_PATTERNS:
                if t == hp or (t.startswith(hp) and len(t) <= len(hp) + 2):
                    return ('column_header', t)

        # Title-like (short, no punctuation except Chinese)
        if len(t) <= 15 and not any(c in t for c in ('。', '，', '、', '！', '？', '；', ':', '.')):
            if source_field == 'title':
                return ('title', t)

        # Default: other text
        return ('text', t)

    if source_field == 'title':
        return ('title', t)
    if source_field == 'label':
        return ('label', t)

    return ('text', t)


def parse_artboard(artboard, filename):
    artboard_name = artboard.get('name', '')
    comp_count = len(artboard.get('components', []))

    all_texts = []
    for comp in artboard.get('components', []):
        all_texts.extend(extract_all(comp))

    categories = defaultdict(list)
    seen = set()
    for text, comp_type, comp_name, source_field in all_texts:
        result = categorize(text, comp_type, source_field)
        if result is None:
            continue
        cat, clean_text = result
        key = (cat, clean_text)
        if key in seen:
            continue
        seen.add(key)
        categories[cat].append((clean_text, comp_type, comp_name))

    return {
        'filename': filename,
        'artboard_name': artboard_name,
        'component_count': comp_count,
        'categories': categories,
    }


def write_output(all_pages, output_file):
    lines = []
    lines.append('# MockPlus Prototype UI Specifications')
    lines.append('')
    lines.append('Extracted from MockPlus prototype data files at `prototype/data/pages/`.')
    lines.append('')
    lines.append('> **Note:** MockPlus stores UI as individual text components with HTML markup.')
    lines.append('> Table column headers, buttons, and labels are inferred from component types')
    lines.append('> and text content patterns. Some modal dialogs are stored as images with no')
    lines.append('> extractable text content.')
    lines.append('')

    pages_by_role = defaultdict(list)
    for p in all_pages:
        pages_by_role[p['role']].append(p)

    role_order = [
        'login', 'loginlog', 'operationlog',
        'workbench',
        'repository', 'repository_sync',
        'product',
        'burner',
        'script',
        'burning',
        'record',
        'injection',
        'protocol',
        'user',
        'role',
    ]

    for role in role_order:
        if role not in pages_by_role:
            continue
        role_pages = pages_by_role[role]
        display = PAGE_DISPLAY.get(role, role.upper())
        lines.append('---')
        lines.append('')
        lines.append(f'## {display}')
        lines.append('')

        for page in role_pages:
            cat = page['categories']
            ab_name = page['artboard_name']

            lines.append(f'### {ab_name}')
            lines.append('')
            lines.append(f'*Source: `{page["filename"]}` | {page["component_count"]} components*')
            lines.append('')

            # --- Output sections in priority order ---

            if cat.get('nav_menu'):
                lines.append('#### Navigation / Sidebar Menu')
                lines.append('')
                for t, _, _ in cat['nav_menu']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('column_header'):
                lines.append('#### Table Column Headers')
                lines.append('')
                for t, _, _ in cat['column_header']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('filter_label'):
                lines.append('#### Search / Filter Labels')
                lines.append('')
                for t, _, _ in cat['filter_label']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('filter_option'):
                lines.append('#### Filter Options')
                lines.append('')
                for t, _, _ in cat['filter_option']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('column_enum'):
                lines.append('#### Column Filter Values')
                lines.append('')
                for t, _, _ in cat['column_enum']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('button'):
                lines.append('#### Buttons / Action Links')
                lines.append('')
                for t, _, _ in cat['button']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('tab'):
                lines.append('#### Tab Labels')
                lines.append('')
                for t, _, _ in cat['tab']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('field_label'):
                lines.append('#### Form / Modal Field Labels')
                lines.append('')
                for t, _, _ in cat['field_label']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('field_placeholder'):
                lines.append('#### Field Placeholders')
                lines.append('')
                for t, _, _ in cat['field_placeholder']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('field_option'):
                lines.append('#### Form Dropdown / Select Options')
                lines.append('')
                for t, _, _ in cat['field_option']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('placeholder'):
                lines.append('#### Input Placeholders')
                lines.append('')
                for t, _, _ in cat['placeholder']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('option'):
                lines.append('#### Dropdown / Select Options')
                lines.append('')
                for t, _, _ in cat['option']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('menu_item'):
                lines.append('#### Menu Items')
                lines.append('')
                for t, _, _ in cat['menu_item']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('title'):
                lines.append('#### Titles')
                lines.append('')
                for t, _, _ in cat['title']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('label'):
                lines.append('#### Labels')
                lines.append('')
                for t, _, _ in cat['label']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('hint') or cat.get('description'):
                lines.append('#### Hints / Descriptions')
                lines.append('')
                for t, _, _ in cat.get('hint', []):
                    lines.append(f'- `{t}`')
                for t, _, _ in cat.get('description', []):
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('status'):
                lines.append('#### Status Values')
                lines.append('')
                for t, _, _ in cat['status']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('pagination'):
                lines.append('#### Pagination')
                lines.append('')
                for t, _, _ in cat['pagination']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('table_data'):
                lines.append('#### Table Data Samples')
                lines.append('')
                for t, _, _ in cat['table_data']:
                    lines.append(f'- `{t}`')
                lines.append('')

            if cat.get('text'):
                lines.append('#### Other Text')
                lines.append('')
                for t, _, _ in cat['text']:
                    lines.append(f'- `{t}`')
                lines.append('')

            # Note for pages with no extractable text
            total_items = sum(len(v) for v in cat.values())
            if total_items == 0:
                lines.append('> **Note:** This artboard has no extractable text content.')
                lines.append('> The UI appears to be stored as an image/SVG in MockPlus.')
                lines.append('')

            lines.append('')

    # Summary
    lines.append('---')
    lines.append('')
    lines.append('## Summary')
    lines.append('')
    lines.append(f'Total pages/artboards parsed: {len(all_pages)}')
    lines.append('')
    lines.append('### Project: 程控安装部署系统 (Programmed Installation and Deployment System)')
    lines.append('')
    lines.append('| Page Role | Artboards |')
    lines.append('|-----------|-----------|')
    for role in role_order:
        if role not in pages_by_role:
            continue
        rp = pages_by_role[role]
        dn = PAGE_DISPLAY.get(role, role.upper())
        ans = ', '.join(p['artboard_name'] for p in rp)
        lines.append(f'| {dn} ({len(rp)}) | {ans} |')
    lines.append('')
    lines.append('### Notes')
    lines.append('')
    lines.append('**Empty folders (no pages):**')
    lines.append('- `资产管理` (Asset Management) - folder only, no pages')
    lines.append('- `系统管理` (System Management) - folder only, no pages')
    lines.append('')
    lines.append('**Missing pages:**')
    lines.append('- No dedicated IDE management page in prototype')
    lines.append('')
    lines.append('**Image-only artboards (no extractable text):**')
    lines.append('- Login page (登录): Login form stored as image')
    for role in role_order:
        if role not in pages_by_role:
            continue
        for p in pages_by_role[role]:
            total = sum(len(v) for v in p['categories'].values())
            if total == 0:
                lines.append(f'- {PAGE_DISPLAY.get(p["role"], p["role"])}: {p["artboard_name"]} (`{p["filename"]}`)')
    lines.append('')

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return len(all_pages)


def main():
    all_pages = []
    js_files = sorted([f for f in os.listdir(PAGES_DIR) if f.endswith('.js')])
    print(f"Found {len(js_files)} JS files")

    for filename in js_files:
        filepath = os.path.join(PAGES_DIR, filename)
        role = FILE_PAGE_MAP.get(filename, 'unknown')
        try:
            data = load_page_file(filepath)
            if not data:
                print(f"  SKIP: {filename}")
                continue
            for artboard in data:
                page = parse_artboard(artboard, filename)
                page['role'] = role
                all_pages.append(page)
                tc = sum(len(v) for v in page['categories'].values())
                print(f"  OK: {filename} -> {artboard.get('name','?')} [{tc} items]")
        except Exception as e:
            print(f"  ERROR: {filename}: {e}")

    count = write_output(all_pages, OUTPUT_FILE)
    print(f"\nParsed {count} artboards -> {OUTPUT_FILE}")

    # Summary
    print("\nCategory counts by role:")
    by_role = defaultdict(list)
    for p in all_pages:
        by_role[p['role']].append(p)
    for role in sorted(by_role.keys()):
        cat_totals = defaultdict(int)
        for p in by_role[role]:
            for c, items in p['categories'].items():
                cat_totals[c] += len(items)
        dn = PAGE_DISPLAY.get(role, role)
        summary = ', '.join(f'{k}={v}' for k, v in sorted(cat_totals.items()) if v > 0)
        print(f"  {dn}: {summary}")


if __name__ == '__main__':
    main()
