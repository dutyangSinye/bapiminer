# -*- coding: utf-8 -*-
"""Ed25519 签名 / 验签（纯标准库，零第三方依赖）。

为什么不用 cryptography / PyNaCl：
    中控是「挂载单文件进容器、零依赖」，矿机端是 PyInstaller onefile。多引一个
    三方库就多一条装机/打包的失败路径（Windows 上编译 wheel、离线矿机装不上等）。
    这里只需要「签一次 / 验一次」，纯 Python 的实现虽然慢（几十毫秒级），
    但在发布与升级这种低频路径上完全够用。

参考：RFC 8032（Ed25519）；本文件是 TweetNaCl 对应 Python 参考实现的整理版，
按其原始 public domain 授权使用。测试向量见 tests/test_signing.py（RFC 8032 §7.1）。

用法：
    seed = new_seed()                 # 32 字节私钥种子（务必离线保存）
    pk   = public_key(seed)           # 32 字节公钥（内置进矿机端）
    sig  = sign(msg, seed)            # 64 字节签名
    ok   = verify(msg, sig, pk)       # -> bool，任何非法输入都返回 False
"""

import hashlib
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 曲线参数（RFC 8032 §5.1）
_P = 2 ** 255 - 19                 # 域特征 q
_L = 2 ** 252 + 27742317777372353535851937790883648493   # 子群阶
_D = -121665 * pow(121666, _P - 2, _P) % _P               # 曲线常数 d
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)                      # sqrt(-1)

_POINT_BYTES = 32
_SIG_BYTES = 64


# ---------------------------------------------------------------- 基础运算


def _sha512(data):
    return hashlib.sha512(data).digest()


def _bit(data, index):
    return (data[index // 8] >> (index % 8)) & 1


def _recover_x(y):
    """由 y 恢复 x（取偶的那个根，符号由调用方按最高位翻转）。"""
    if y >= _P:
        raise ValueError("y 非规范编码")
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = x * _SQRT_M1 % _P
    if x & 1:
        x = _P - x
    return x


def _encode_point(point):
    x, y = point
    return (y | ((x & 1) << 255)).to_bytes(_POINT_BYTES, "little")


def _decode_point(data):
    """解码点并校验在曲线上；非法编码抛 ValueError。"""
    if len(data) != _POINT_BYTES:
        raise ValueError("点编码长度错误")
    value = int.from_bytes(data, "little")
    y = value & ((1 << 255) - 1)
    x = _recover_x(y)
    if (x & 1) != (value >> 255) & 1:
        x = _P - x
    if not _is_on_curve((x, y)):
        raise ValueError("点不在曲线上")
    return (x, y)


def _is_on_curve(point):
    x, y = point
    xx, yy = x * x % _P, y * y % _P
    return (-xx + yy - 1 - _D * xx * yy) % _P == 0


def _add(p1, p2):
    """仿射坐标加法（参考实现的算法，慢但短）。"""
    x1, y1 = p1
    x2, y2 = p2
    common = _D * x1 * x2 * y1 * y2 % _P
    x3 = (x1 * y2 + x2 * y1) * pow(1 + common, _P - 2, _P) % _P
    y3 = (y1 * y2 + x1 * x2) * pow(1 - common, _P - 2, _P) % _P
    return (x3 % _P, y3 % _P)


def _mul(point, scalar):
    """标量乘（double-and-add，避免深递归）。"""
    result = (0, 1)                     # 单位元
    while scalar:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _base_point():
    y = 4 * pow(5, _P - 2, _P) % _P
    x = _recover_x(y)
    if x & 1:
        x = _P - x
    return (x % _P, y % _P)


_B = _base_point()


def _clamp(h):
    """RFC 8032 §5.1.5：取哈希前 32 字节并夹紧。"""
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a


def _hash_to_int(data):
    return int.from_bytes(_sha512(data)[:64], "little")


# ---------------------------------------------------------------- 公共 API


def new_seed():
    """生成 32 字节私钥种子。"""
    return os.urandom(32)


def public_key(seed):
    """由私钥种子导出 32 字节公钥。"""
    if len(seed) != 32:
        raise ValueError("私钥种子必须是 32 字节")
    a = _clamp(_sha512(seed))
    return _encode_point(_mul(_B, a))


def sign(message, seed):
    """对 message（bytes）签名，返回 64 字节签名。"""
    if len(seed) != 32:
        raise ValueError("私钥种子必须是 32 字节")
    h = _sha512(seed)
    a = _clamp(h)
    prefix = h[32:]
    pk = _encode_point(_mul(_B, a))
    r = _hash_to_int(prefix + message) % _L
    big_r = _encode_point(_mul(_B, r))
    k = _hash_to_int(big_r + pk + message) % _L
    s = (r + k * a) % _L
    return big_r + s.to_bytes(_POINT_BYTES, "little")


def verify(message, signature, pk):
    """验签。任何格式错误 / 不合法输入都返回 False，不抛异常。"""
    try:
        if len(signature) != _SIG_BYTES or len(pk) != _POINT_BYTES:
            return False
        big_r = _decode_point(signature[:_POINT_BYTES])
        a = _decode_point(pk)
        s = int.from_bytes(signature[_POINT_BYTES:], "little")
        # RFC 8032 §5.1.7：S 必须 < L，否则存在可延展签名
        if s >= _L:
            return False
        k = _hash_to_int(signature[:_POINT_BYTES] + pk + message) % _L
        return _mul(_B, s) == _add(big_r, _mul(a, k))
    except Exception:  # noqa: BLE001 —— 非法点/长度等一律视为验签失败
        return False


# ---------------------------------------------------------------- 十六进制便捷封装


def public_key_hex(seed):
    return public_key(seed).hex()


def sign_hex(message, seed):
    return sign(message, seed).hex()


def verify_hex(message, signature_hex, pk_hex):
    try:
        signature = bytes.fromhex(signature_hex)
        pk = bytes.fromhex(pk_hex)
    except (TypeError, ValueError):
        return False
    return verify(message, signature, pk)


if __name__ == "__main__":
    # 自检：RFC 8032 §7.1 TEST 1
    _sk = bytes.fromhex(
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    _pk = public_key_hex(_sk)
    _sig = sign_hex(b"", _sk)
    print("pk :", _pk)
    print("sig:", _sig)
    print("ok :", verify_hex(b"", _sig, _pk))
