"""纯文本文件的健壮读取：自动识别编码，避免 GBK/GB2312 中文文件被当 UTF-8 解码而乱码。

解码顺序：BOM → 严格 UTF-8 → 严格 GB18030 → charset-normalizer 探测 → UTF-8 replace。

为什么 GB18030 排在探测库之前：本项目面向中国大陆，非 UTF-8 的旧文本几乎都是 GBK/GB2312，
而 GB18030 是它们的严格超集、能正确还原；charset-normalizer 对**短样本**易误判（实测会把
短 GBK 文本判成别的 CJK 编码），故只作为 GB18030 解码失败时的兜底（处理 Big5/Shift-JIS 等少数情况）。

最后一步用 errors="replace"（无法解码处显示 �）而非 errors="ignore"（静默丢字节）——
丢字节会让中文整段消失且无任何痕迹，replace 至少让问题可见、其余内容可读。
"""
from __future__ import annotations

from pathlib import Path
from typing import Union


def decode_bytes(data: bytes) -> str:
    """把字节按最可能的编码解码为字符串（不 strip）。"""
    if not data:
        return ""

    # 1) UTF-8 BOM 快速路径
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")

    # 2) 严格 UTF-8：最常见，且自校验（GBK 字节序列几乎必然解码失败 → 落到下一步）
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # 3) 严格 GB18030：GBK/GB2312/GB18030 超集，覆盖简体中文绝大多数旧文件（大陆场景首选兜底）
    try:
        return data.decode("gb18030")
    except UnicodeDecodeError:
        pass

    # 4) charset-normalizer 探测：处理 GB18030 也解不了的少数情况（Big5/Shift-JIS 等）
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best is not None:
            return str(best)
    except Exception:
        pass

    # 5) 兜底：用 � 标出无法解码处，不静默丢字节
    return data.decode("utf-8", errors="replace")


def read_text_smart(path: Union[str, Path]) -> str:
    """读取纯文本文件并自动识别编码，返回已 strip 的文本。

    用于读取**用户上传的文本内容**（知识库文档、清洗输入等），替代
    `path.read_text(encoding="utf-8", errors="ignore")`——后者对 GBK 中文会大量丢字符。
    （注：项目自有的 UTF-8 JSON / skill md 不必走本函数。）
    """
    return decode_bytes(Path(path).read_bytes()).strip()
