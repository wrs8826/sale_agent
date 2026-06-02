"""API key 加密：Fernet 对称加密 + 本地密钥文件。

存储约定：
    密文以 `enc:` 前缀写入 YAML，明文则直接存。
    本模块的 encrypt/decrypt 对外只接受 / 返回明文，自动处理 `enc:` 前缀。

若运行环境未安装 `cryptography`，则降级为明文存储并打印一次警告。
密钥文件位置：agent_service/.secret_key（应加入 .gitignore）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from . import PACKAGE_ROOT

SECRET_KEY_PATH = PACKAGE_ROOT / ".secret_key"
ENC_PREFIX = "enc:"

try:
    from cryptography.fernet import Fernet, InvalidToken
    _CRYPTO_OK = True
except Exception:  # pragma: no cover
    Fernet = None  # type: ignore
    InvalidToken = Exception  # type: ignore
    _CRYPTO_OK = False

_warned_no_crypto = False
_fernet_cache: Optional["Fernet"] = None


def _warn_once_no_crypto() -> None:
    global _warned_no_crypto
    if not _warned_no_crypto:
        print(
            "[security] 未检测到 cryptography 库，API key 将以明文存储。"
            " 建议执行: pip install cryptography"
        )
        _warned_no_crypto = True


def _get_fernet() -> Optional["Fernet"]:
    global _fernet_cache
    if not _CRYPTO_OK:
        return None
    if _fernet_cache is not None:
        return _fernet_cache

    if SECRET_KEY_PATH.exists():
        key = SECRET_KEY_PATH.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        SECRET_KEY_PATH.write_bytes(key)
        try:
            os.chmod(SECRET_KEY_PATH, 0o600)
        except Exception:
            pass
    _fernet_cache = Fernet(key)
    return _fernet_cache


def encrypt(plaintext: str) -> str:
    """加密明文。空字符串原样返回。失败回退明文。"""
    if not plaintext:
        return ""
    f = _get_fernet()
    if f is None:
        _warn_once_no_crypto()
        return plaintext
    token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt(value: Optional[str]) -> str:
    """解密。None / 空 → ""；非 enc: 前缀视为旧明文原样返回。"""
    if not value:
        return ""
    if not value.startswith(ENC_PREFIX):
        return value  # 兼容历史明文
    f = _get_fernet()
    if f is None:
        _warn_once_no_crypto()
        return ""  # 缺密钥时无法解密
    try:
        return f.decrypt(value[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        print("[security] 解密失败（密钥不匹配），返回空字符串")
        return ""


def mask(plaintext: str) -> str:
    """前端展示用：sk-****abcd，太短则全部打码。"""
    if not plaintext:
        return ""
    n = len(plaintext)
    if n <= 8:
        return "*" * n
    return f"{plaintext[:3]}{'*' * 6}{plaintext[-4:]}"
