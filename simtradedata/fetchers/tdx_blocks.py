"""Decode TDX's 384-byte header and fixed 2,813-byte block records."""

import struct

import pandas as pd


def parse_blocks(data):
    if len(data) < 386:
        raise ValueError("Truncated TDX block header")
    count = struct.unpack_from("<H", data, 384)[0]
    if len(data) != 386 + count * 2813:
        raise ValueError("TDX block size does not match its record count")
    rows = []
    for index in range(count):
        offset = 386 + index * 2813
        name = data[offset:offset + 9].split(b"\0", 1)[0].decode("gbk")
        stock_count, block_type = struct.unpack_from("<HH", data, offset + 9)
        if stock_count > 400:
            raise ValueError("TDX block exceeds its 400-code capacity")
        for code_index in range(stock_count):
            start = offset + 13 + code_index * 7
            code = data[start:start + 7].split(b"\0", 1)[0].decode("ascii")
            if len(code) != 6 or not code.isascii() or not code.isdigit():
                raise ValueError("Invalid security code in TDX block")
            rows.append({"blockname": name, "block_type": block_type,
                         "code_index": code_index, "code": code})
    return pd.DataFrame(rows, columns=["blockname", "block_type", "code_index", "code"])
