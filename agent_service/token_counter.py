"""精确 token 计数（Phase 3）。

用项目 `token/` 目录下的官方 DeepSeek 分词器（HuggingFace `AutoTokenizer`）计数。
首次调用懒加载并进程内缓存；分词器/transformers 不可用时降级到粗估
（CJK≈1 token，ASCII≈0.75），保证永不因计数失败而中断对话。

对外只暴露 `count_tokens(text)`；会话层的 `estimate_tokens` 已委托到这里。
"""
from __future__ import annotations

from typing import Optional

_tokenizer = None          # 缓存的分词器实例
_load_failed = False       # 加载失败后不再重试，直接走粗估


def _get_tokenizer():
    global _tokenizer, _load_failed
    if _tokenizer is not None or _load_failed:
        return _tokenizer
    try:
        from agent_service import TOKEN_DIR
        # 直接用 tokenizer.json 加载快速分词器：避免 AutoTokenizer 误退化成慢速
        # LlamaTokenizer（无 sentencepiece .model，会丢中文、计数失真）
        from transformers import PreTrainedTokenizerFast
        _tokenizer = PreTrainedTokenizerFast(tokenizer_file=str(TOKEN_DIR / "tokenizer.json"))
        print("[token_counter] DeepSeek 快速分词器加载成功")
    except Exception as exc:
        print(f"[token_counter] 分词器加载失败，降级粗估: {exc}")
        _load_failed = True
        _tokenizer = None
    return _tokenizer


def _crude(text: str) -> int:
    """粗估：中文 1 字≈1 token，其余≈0.75，外加少量角色开销。"""
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    return cjk + int((len(text) - cjk) * 0.75) + 4


def count_tokens(text: str) -> int:
    """返回 text 的 token 数。分词器可用时精确，否则粗估。"""
    if not text:
        return 0
    tk = _get_tokenizer()
    if tk is None:
        return _crude(text)
    try:
        return len(tk.encode(text))
    except Exception:
        return _crude(text)
