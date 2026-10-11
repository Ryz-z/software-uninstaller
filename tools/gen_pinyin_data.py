#!/usr/bin/python3
"""一次性工具：把《通用规范汉字表》拼音数据转成离线模块 core/pinyin_data.py。

数据来源：https://raw.githubusercontent.com/mozillazg/pinyin-data/master/kTGHZ2013.txt
每行格式：U+3447: zhòu  # 㑇

用法：python3 tools/gen_pinyin_data.py <源文件> <输出文件>
"""

import re
import sys
import unicodedata

LINE = re.compile(r"^U\+([0-9A-Fa-f]+):\s*([^\s#]+)\s+#\s*(\S)")


def strip_tone(py: str) -> str:
    """去掉声调符号，ü 归一化为 u。"""
    py = py.replace("ü", "u").replace("ǖ", "u").replace("ǘ", "u")
    py = py.replace("ǚ", "u").replace("ǜ", "u")
    decomposed = unicodedata.normalize("NFD", py)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else "pinyin_raw.txt"
    dst = sys.argv[2] if len(sys.argv) > 2 else "src/core/pinyin_data.py"

    table: dict = {}
    with open(src, encoding="utf-8") as fh:
        for line in fh:
            m = LINE.match(line)
            if not m:
                continue
            char = m.group(3)
            # 同一行内多音字用逗号分隔：U+4E50: lè,yuè  # 乐
            pys = [strip_tone(p) for p in m.group(2).lower().split(",")]
            pys = [p for p in pys if p]
            if not pys:
                continue
            existing = table.get(char)
            if existing is None:
                table[char] = pys[:3]
            else:
                for p in pys:
                    if p not in existing and len(existing) < 3:
                        existing.append(p)

    chars = "".join(sorted(table.keys()))
    pys = " ".join("/".join(table[c]) for c in chars)

    with open(dst, "w", encoding="utf-8") as fh:
        fh.write('"""离线汉字拼音表（自动生成，勿手改）。\n\n')
        fh.write("数据来源：《通用规范汉字表》2013（kTGHZ2013），共 %d 字。\n" % len(chars))
        fh.write('生成工具：tools/gen_pinyin_data.py\n"""\n\n')
        fh.write("# 汉字表（与 _PINYIN 一一对应）\nCHARS = %r\n\n" % chars)
        fh.write("# 拼音表（空格分隔；多音字用 / 分隔）\nPINYIN = %r\n" % pys)

    print(f"已生成 {dst}：{len(chars)} 字，{len(pys)} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
