"""한글(HWP 5.x) 파일에서 본문 글자만 뽑는다. (표 안 글자 포함, 서식은 버림)"""
import struct
import zlib

import olefile

PARA_TEXT = 67  # HWPTAG_BEGIN(16) + 51


def hwp_text(path_or_bytes):
    ole = olefile.OleFileIO(path_or_bytes)
    header = ole.openstream("FileHeader").read()
    compressed = bool(header[36] & 1)
    sections = sorted((s for s in ole.listdir() if s[0] == "BodyText"), key=lambda s: int(s[1].replace("Section", "")))
    out = []
    for s in sections:
        data = ole.openstream(s).read()
        if compressed:
            data = zlib.decompress(data, -15)
        i = 0
        while i + 4 <= len(data):
            h = struct.unpack_from("<I", data, i)[0]
            tag, size = h & 0x3FF, (h >> 20) & 0xFFF
            i += 4
            if size == 0xFFF:
                size = struct.unpack_from("<I", data, i)[0]
                i += 4
            if tag == PARA_TEXT:
                out.append(_decode(data[i:i + size]))
            i += size
    ole.close()
    return "\n".join(t for t in out if t.strip())


def _decode(b):
    chars, j = [], 0
    while j + 1 < len(b):
        c = struct.unpack_from("<H", b, j)[0]
        if c < 32:
            # 확장 제어문자(인라인/확장)는 8글자(16바이트) 차지
            if c in (1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23, 4, 5, 6, 7, 8, 9, 19, 20):
                if c not in (9,):  # 탭은 공백으로
                    j += 16
                    continue
                chars.append(" ")
                j += 16
                continue
            if c in (10, 13):
                chars.append("\n")
            j += 2
            continue
        chars.append(chr(c))
        j += 2
    return "".join(chars)


if __name__ == "__main__":
    import sys
    print(hwp_text(sys.argv[1]))


def hwpx_text(b):
    """새 한글 형식(HWPX: zip 안의 XML)에서 글자만 뽑는다."""
    import io
    import re
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(b))
    names = sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml", n))
    out = []
    for n in names:
        x = z.read(n).decode("utf-8", "replace")
        x = re.sub(r"</hp:p>", "\n", x)
        out.append(re.sub(r"<[^>]+>", "", x))
    return "\n".join(out)


def any_text(b):
    if b[:4] == b"\xd0\xcf\x11\xe0":
        return hwp_text(b)
    if b[:2] == b"PK":
        return hwpx_text(b)
    return f"(형식 미지원: {b[:8]!r})"
