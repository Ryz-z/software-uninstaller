"""拼音检索支持（离线数据表，无第三方依赖）。

把中文名转换成可用于搜索的拼音串：
- 全拼：计算器 -> jisuanqi
- 首字母：计算器 -> jsq
- 多音字会生成有限个变体（最多 4 个），例如 音乐 -> yinyue / yinle
"""

from typing import Dict, List

from .pinyin_data import CHARS, PINYIN

_MAP: Dict[str, List[str]] = {}
_MAX_VARIANTS = 4


def _table() -> Dict[str, List[str]]:
    global _MAP
    if not _MAP:
        pys = PINYIN.split(" ")
        _MAP = {c: pys[i].split("/") for i, c in enumerate(CHARS)}
    return _MAP


def readings(char: str) -> List[str]:
    """返回单个汉字的拼音读音列表（非汉字返回空列表）。"""
    if len(char) != 1:
        return []
    return _table().get(char, [])


def _combine(candidates: List[List[str]], limit: int) -> List[str]:
    """把逐字的候选读音组合成串，限制变体数量。"""
    out: List[str] = [""]
    for group in candidates:
        nxt: List[str] = []
        for prefix in out:
            for g in group:
                nxt.append(prefix + g)
                if len(nxt) >= limit:
                    break
            if len(nxt) >= limit:
                break
        out = nxt[:limit]
        if not out:
            break
    return out


def pinyin_variants(text: str, limit: int = _MAX_VARIANTS) -> List[str]:
    """生成全拼变体列表（含非汉字原样拼接，全部小写）。"""
    if not text:
        return []
    groups: List[List[str]] = []
    has_han = False
    for ch in text:
        rs = readings(ch)
        if rs:
            has_han = True
            groups.append(rs)
        else:
            groups.append([ch.lower()])
    if not has_han:
        return []
    return _combine(groups, limit)


def initials_variants(text: str, limit: int = _MAX_VARIANTS) -> List[str]:
    """生成首字母变体列表，如 计算器 -> jsq。"""
    if not text:
        return []
    groups: List[List[str]] = []
    has_han = False
    for ch in text:
        rs = readings(ch)
        if rs:
            has_han = True
            groups.append([r[0] for r in rs] or [ch.lower()])
        else:
            groups.append([ch.lower()])
    if not has_han:
        return []
    return _combine(groups, limit)


def search_keys(text: str) -> List[str]:
    """返回一个字符串可用于检索的关键词列表（原串 + 全拼 + 首字母）。"""
    keys: List[str] = []
    if not text:
        return keys
    low = text.lower()
    keys.append(low)
    keys.extend(pinyin_variants(text))
    keys.extend(initials_variants(text))
    # 去重保序
    seen = set()
    uniq = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq
