"""OCR portfolio parser — extract stock positions from broker screenshots.

Ephemeral: temp files are written to a system tempdir and deleted in finally blocks.
Never writes to cache/portfolio or any persistent path.

OCR engine priority:
  1. rapidocr_onnxruntime (pip install rapidocr-onnxruntime)
  2. pytesseract + tesseract binary
  3. Fallback: return empty positions with a warning (manual edit always works)

All engines are optional deps. The manual-edit table in the UI is the primary path.
"""

import os
import re
import tempfile

_OCR_ENGINE = None  # lazily detected


def _detect_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE

    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
        _OCR_ENGINE = "rapidocr"
        return _OCR_ENGINE
    except ImportError:
        pass

    try:
        import pytesseract  # noqa: F401
        pytesseract.get_tesseract_version()
        _OCR_ENGINE = "tesseract"
        return _OCR_ENGINE
    except Exception:
        pass

    _OCR_ENGINE = "none"
    return _OCR_ENGINE


def _ocr_rapidocr(image_path):
    from rapidocr_onnxruntime import RapidOCR
    engine = RapidOCR()
    result, _elapse = engine(image_path)
    if not result:
        return ""
    return "\n".join(line[1] for line in result)


def _ocr_tesseract(image_path):
    import pytesseract
    from PIL import Image
    img = Image.open(image_path)
    return pytesseract.image_to_string(img, lang="chi_sim+eng")


def ocr_image(image_bytes, filename="upload.png"):
    """Run OCR on image bytes. Returns (raw_text, engine_used, warnings).

    Temp file is always deleted after processing.
    """
    warnings = []
    engine = _detect_engine()

    if engine == "none":
        return "", "none", ["OCR 引擎不可用（需安装 rapidocr-onnxruntime 或 pytesseract），请手动填写持仓"]

    tmp_path = None
    try:
        suffix = os.path.splitext(filename)[1] or ".png"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="ocr_")
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(image_bytes)

        if engine == "rapidocr":
            raw_text = _ocr_rapidocr(tmp_path)
        elif engine == "tesseract":
            raw_text = _ocr_tesseract(tmp_path)
        else:
            raw_text = ""
            warnings.append("未知 OCR 引擎")

        return raw_text, engine, warnings
    except Exception as e:
        warnings.append(f"OCR 处理失败: {type(e).__name__}: {e}")
        return "", engine, warnings
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


_CODE_RE = re.compile(r"(?<!\d)((?:60|68|00|30)\d{4})(?!\d)")


def parse_positions(raw_text):
    """Extract structured positions from OCR raw text.

    Returns list of dicts: [{code, name?, qty?, available?, cost?}, ...]
    Looks for 6-digit A-share codes (60/68/00/30 prefix).
    """
    if not raw_text:
        return []

    positions = []
    seen_codes = set()
    lines = raw_text.split("\n")

    for line in lines:
        codes_in_line = _CODE_RE.findall(line)
        for code in codes_in_line:
            if code in seen_codes:
                continue
            seen_codes.add(code)

            pos = {"code": code}

            name = _extract_name_near_code(line, code)
            if name:
                pos["name"] = name

            numbers = _extract_numbers(line, code)
            if len(numbers) >= 1:
                pos["qty"] = numbers[0]
            if len(numbers) >= 2:
                pos["available"] = numbers[1]
            if len(numbers) >= 3:
                pos["cost"] = numbers[2]

            positions.append(pos)

    return positions


def _extract_name_near_code(line, code):
    """Try to find a Chinese stock name adjacent to the code in the line."""
    cn_re = re.compile(r"[\u4e00-\u9fff]{2,6}")
    names = cn_re.findall(line)
    if names:
        return names[0]
    return None


def _extract_numbers(line, code):
    """Extract numeric values from the line (excluding the code itself)."""
    remaining = line.replace(code, " ", 1)
    num_re = re.compile(r"(\d+(?:[.,]\d+)?)")
    raw_nums = num_re.findall(remaining)
    result = []
    for s in raw_nums:
        s = s.replace(",", "")
        try:
            if "." in s:
                result.append(float(s))
            else:
                result.append(int(s))
        except ValueError:
            continue
    return result
