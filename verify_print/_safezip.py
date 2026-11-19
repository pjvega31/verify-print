"""Bounded reads from 3MF (zip) archives.

3MF projects are often downloaded from model sites, so treat them as untrusted:
never read an archive member whole into memory without a size limit (zip bombs),
and do not trust the size in the zip header alone.
"""
from __future__ import annotations

import zipfile

# Settings / metadata members are kilobytes in real projects; 64 MB is far above that.
MAX_MEMBER_BYTES = 64 * 1024 * 1024
_CHUNK = 1024 * 1024


class MemberTooLarge(ValueError):
    """An archive member is larger than the allowed limit."""


def read_member(zf: zipfile.ZipFile, name: str, limit: int = MAX_MEMBER_BYTES) -> bytes:
    """Read one member, refusing anything over `limit` bytes (declared or actual)."""
    info = zf.getinfo(name)
    if info.file_size > limit:
        raise MemberTooLarge(f"{name}: declared size {info.file_size} bytes exceeds limit {limit}")
    with zf.open(info) as f:
        data = f.read(limit + 1)  # the header can lie; cap the real read too
    if len(data) > limit:
        raise MemberTooLarge(f"{name}: actual size exceeds limit {limit}")
    return data


def read_head_tail(zf: zipfile.ZipFile, name: str, n: int = 200_000) -> tuple[bytes, bytes]:
    """Stream a (possibly huge) member and keep only its first and last `n` bytes."""
    head = b""
    tail = b""
    with zf.open(name) as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            if len(head) < n:
                head += chunk[: n - len(head)]
            tail = (tail + chunk)[-n:]
    return head, tail
