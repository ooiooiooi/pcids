from __future__ import annotations

import json
from typing import Any, Iterable, Optional


MOJIBAKE_HINTS = (
    "锟",
    "�",
    "閸",
    "閺",
    "閻",
    "闁",
    "娑",
    "婢",
    "鐎",
    "瀵",
    "鍏",
    "涓",
    "澶",
    "寮",
    "鎿",
    "绯",
    "鐑",
    "璇",
    "銆",
)


KNOWN_TEXT_REPAIRS: dict[str, str] = {
    "澶嶄綅杩愯": "复位运行",
    "涓嶅鐞?": "不处理",
    "浠呭浣?": "仅复位",
    "鍏ㄧ墖鎿﹂櫎": "全片擦除",
    "鎵囧尯鎿﹂櫎": "扇区擦除",
    "榛樿鑷姩鎿﹂櫎": "默认自动擦除",
    "鎺ュ彛绫诲瀷": "接口类型",
    "鎿﹂櫎鏂瑰紡": "擦除方式",
    "瀹屾垚鍚庡姩浣?": "完成后动作",
    "鎵ц鎿嶄綔": "执行操作",
    "鐩爣SD鍗′綅缃?": "目标SD卡位置",
    "寮€鍙戞澘": "开发板",
    "璇勪及鏉?": "评估板",
    "鏉垮崱": "板卡",
    "鏍稿績鏉?": "核心板",
    "涓嬭浇鍣?": "下载器",
    "涓嬭浇绾?": "下载线",
    "鐑у綍鍣?": "烧录器",
    "绯诲垪": "系列",
    "浠跨湡鍣?": "仿真器",
    "璋冭瘯鍣?": "调试器",
    "SD鍗℃枃浠跺啓鍏?": "SD卡文件写入",
    "SD鍗?": "SD卡",
    "鑷姩寮瑰嚭SD鍗?": "自动弹出SD卡",
    "鏄?": "是",
    "鍚?": "否",
    "SRAM涓嬭浇": "SRAM下载",
    "Flash鍥哄寲": "Flash固化",
    "鍏ュ彛": "入口",
    "寮傚父": "异常",
    "澶囩敤": "备用",
    "瀵嗛泦": "密集",
    "鍒涘缓": "创建",
    "鏁版嵁搴?": "数据库",
    "鐢ㄦ埛": "用户",
    "瑙掕壊": "角色",
    "鎴愬姛": "成功",
    "澶辫触": "失败",
    "寰呮墽琛?": "待执行",
    "缁堟": "终止",
    "杩愯": "运行",
    "宸插惎鐢?": "已启用",
    "宸茬鐢?": "已禁用",
    "鏈彁渚?": "未提供",
    "鏈壘鍒?": "未找到",
    "璇峰畨瑁?": "请安装",
    "璇烽厤缃?": "请配置",
    "璇锋鏌?": "请检查",
    "璇烽噸璇?": "请重试",
}


def _has_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _has_mojibake_hint(text: str) -> bool:
    if not text:
        return False
    if any(bad in text for bad in KNOWN_TEXT_REPAIRS):
        return True
    hint_count = sum(text.count(hint) for hint in MOJIBAKE_HINTS)
    return hint_count >= 1


def _quality_score(text: str) -> int:
    score = 0
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            score += 3
        elif char.isascii() and (char.isalnum() or char in " _-./:\\()[]{}#,%+|"):
            score += 1
        elif char in "，。！？：；、“”‘’（）《》【】":
            score += 2
        if char in "�锟":
            score -= 8
    for hint in MOJIBAKE_HINTS:
        if hint in text:
            score -= 2 * text.count(hint)
    for bad in KNOWN_TEXT_REPAIRS:
        if bad in text:
            score -= 12
    return score


def _decode_candidate(text: str, encode_codec: str, decode_codec: str) -> Optional[str]:
    try:
        return text.encode(encode_codec, errors="strict").decode(decode_codec, errors="strict")
    except Exception:
        return None


def _encoding_repair_candidates(text: str) -> list[str]:
    candidates = [text]
    for encode_codec, decode_codec in (
        ("gbk", "utf-8"),
        ("gb18030", "utf-8"),
        ("latin1", "utf-8"),
        ("cp1252", "utf-8"),
        ("latin1", "gb18030"),
        ("cp1252", "gb18030"),
    ):
        repaired = _decode_candidate(text, encode_codec, decode_codec)
        if repaired and repaired not in candidates:
            candidates.append(repaired)
    return candidates


def _dictionary_repair(text: str) -> str:
    repaired = text
    for bad, good in KNOWN_TEXT_REPAIRS.items():
        repaired = repaired.replace(bad, good)
    return repaired


def normalize_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip("\ufeff")
    if not text or not _has_mojibake_hint(text):
        return text

    candidates = _encoding_repair_candidates(text)
    candidates.extend(_dictionary_repair(candidate) for candidate in list(candidates))

    best = max(candidates, key=_quality_score)
    if best != text and (_quality_score(best) > _quality_score(text) or _has_chinese(best)):
        return best
    return _dictionary_repair(text)


def normalize_text_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: normalize_text_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_text_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(normalize_text_payload(item) for item in value)
    return normalize_text(value)


def parse_json_object(raw: Any) -> dict:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return normalize_text_payload(raw)
    if isinstance(raw, (list, tuple)):
        return {}
    if not isinstance(raw, str):
        try:
            raw = str(raw)
        except Exception:
            return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return normalize_text_payload(raw)
    if not isinstance(parsed, dict):
        return {}
    return normalize_text_payload(parsed)


def repair_db_text(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, dict):
        return {key: repair_db_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [repair_db_text(item) for item in value]
    if isinstance(value, tuple):
        return tuple(repair_db_text(item) for item in value)
    return value


def repair_db_rows(rows: Iterable[dict], columns: Iterable[str]) -> int:
    fixed = 0
    for row in rows:
        for col in columns:
            original = row.get(col)
            if not isinstance(original, str):
                continue
            repaired = normalize_text(original)
            if repaired != original:
                row[col] = repaired
                fixed += 1
    return fixed
