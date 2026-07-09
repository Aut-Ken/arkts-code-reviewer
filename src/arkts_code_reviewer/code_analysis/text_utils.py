from __future__ import annotations


def mask_comments_and_strings(source: str) -> str:
    chars = list(source)
    i = 0
    state = "code"
    quote = ""
    while i < len(chars):
        c = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""

        if state == "code":
            if c == "/" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                i += 2
                state = "line_comment"
                continue
            if c == "/" and nxt == "*":
                chars[i] = chars[i + 1] = " "
                i += 2
                state = "block_comment"
                continue
            if c in ("'", '"', "`"):
                quote = c
                chars[i] = " "
                i += 1
                state = "string"
                continue
            i += 1
            continue

        if state == "line_comment":
            if c == "\n":
                state = "code"
            else:
                chars[i] = " "
            i += 1
            continue

        if state == "block_comment":
            if c == "*" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                i += 2
                state = "code"
            else:
                if c != "\n":
                    chars[i] = " "
                i += 1
            continue

        if state == "string":
            if c == "\\":
                chars[i] = " "
                if i + 1 < len(chars) and chars[i + 1] != "\n":
                    chars[i + 1] = " "
                i += 2
                continue
            if c == quote:
                chars[i] = " "
                i += 1
                state = "code"
                continue
            if c != "\n":
                chars[i] = " "
            i += 1

    return "".join(chars)


def line_starts(source: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(source):
        if char == "\n":
            starts.append(index + 1)
    return starts


def offset_to_line_col(starts: list[int], offset: int) -> tuple[int, int]:
    low = 0
    high = len(starts) - 1
    while low <= high:
        mid = (low + high) // 2
        if starts[mid] <= offset:
            low = mid + 1
        else:
            high = mid - 1
    line_index = max(0, high)
    return line_index + 1, offset - starts[line_index] + 1


def extract_lines(source: str, start_line: int, end_line: int) -> str:
    lines = source.splitlines()
    start = max(1, start_line)
    end = min(len(lines), end_line)
    if end < start:
        return ""
    return "\n".join(lines[start - 1 : end])


def find_matching_brace(masked_source: str, open_brace_offset: int) -> int | None:
    depth = 0
    for offset in range(open_brace_offset, len(masked_source)):
        char = masked_source[offset]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return offset
    return None

