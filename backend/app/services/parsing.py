import io
import re
import zipfile
from dataclasses import dataclass

import pdfplumber
from docx import Document as WordDocument
from docx.table import Table

from app.core.errors import ApiError


@dataclass
class Part:
    content: str
    anchor: dict


def invalid(message, code="PARSE_FAILED"):
    return ApiError(422, code, message)


def parse(data: bytes, fmt: str, settings) -> tuple[list[Part], int]:
    parts = []
    total = 0

    def add(content, anchor, *, indexed=True):
        nonlocal total
        content = content.strip()
        if not content:
            return
        total += len(content)
        if total > settings.max_extracted_chars:
            raise invalid("提取正文超过10万字符上限", "TEXT_TOO_LONG")
        if not indexed:
            return
        # Anchor applies to every window; no merging across pages/sections/table rows.
        for offset in range(0, len(content), 680):
            piece = content[offset : offset + 800]
            parts.append(
                Part(piece, {**anchor, "char_start": offset, "char_end": offset + len(piece)})
            )
            if offset + 800 >= len(content):
                break

    try:
        if fmt == "md":
            content = data.decode("utf-8-sig")
            if "\x00" in content:
                raise invalid("Markdown必须为UTF-8文本")
            headings = []
            paragraph, line_start = [], 1

            def flush(end):
                nonlocal paragraph
                add(
                    "\n".join(paragraph),
                    {
                        "type": "markdown",
                        "heading": " / ".join(headings),
                        "line_start": line_start,
                        "line_end": end,
                    },
                )
                paragraph = []

            lines = content.splitlines()
            table_header = None
            table_header_line = None
            for number, line in enumerate(lines, 1):
                match = re.match(r"^(#{1,6})\s+(.+)$", line)
                if match:
                    flush(number - 1)
                    level = len(match[1])
                    headings = headings[: level - 1] + [match[2].strip()]
                    add(
                        line,
                        {
                            "type": "markdown",
                            "heading": " / ".join(headings),
                            "line_start": number,
                            "line_end": number,
                        },
                        indexed=False,
                    )
                elif (
                    "|" in line and number < len(lines) and re.match(r"^[\s|:\-]+$", lines[number])
                ):
                    flush(number - 1)
                    table_header = line
                    table_header_line = number
                    add(
                        line,
                        {
                            "type": "markdown",
                            "heading": " / ".join(headings),
                            "line_start": number,
                            "line_end": number,
                        },
                        indexed=False,
                    )
                elif table_header is not None and "|" in line:
                    if not re.match(r"^[\s|:\-]+$", line):
                        add(
                            table_header + "\n" + line,
                            {
                                "type": "markdown",
                                "heading": " / ".join(headings),
                                "line_start": table_header_line,
                                "line_end": number,
                                "table_row": True,
                            },
                        )
                elif not line.strip():
                    flush(number - 1)
                    table_header = None
                else:
                    table_header = None
                    if not paragraph:
                        line_start = number
                    paragraph.append(line)
            flush(len(lines))
        elif fmt == "pdf":
            if not data.startswith(b"%PDF-"):
                raise invalid("文件内容不是有效PDF")
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                if len(pdf.pages) > settings.max_pdf_pages:
                    raise invalid("PDF超过100页上限", "PDF_TOO_MANY_PAGES")
                for number, page in enumerate(pdf.pages, 1):
                    text = page.extract_text() or ""
                    if not text.strip() and page.images:
                        raise invalid("包含无法提取文字的扫描页，首版仅支持文字PDF", "OCR_REQUIRED")
                    add(text, {"type": "pdf", "page": number})
                    for table_no, table in enumerate(page.extract_tables(), 1):
                        if not table:
                            continue
                        header = [str(v or "") for v in table[0]]
                        for row_no, row in enumerate(table[1:], 2):
                            add(
                                "；".join(
                                    f"{h}: {v or ''}" for h, v in zip(header, row, strict=False)
                                ),
                                {"type": "pdf", "page": number, "table": table_no, "row": row_no},
                            )
        elif fmt == "docx":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(i.file_size for i in entries) > 50_000_000:
                    raise invalid("DOCX解压大小超限", "DOCX_TOO_LARGE")
                if "word/document.xml" not in archive.namelist():
                    raise invalid("文件内容不是有效DOCX")
            doc = WordDocument(io.BytesIO(data))
            heading, table_no, paragraph_no = "", 0, 0
            for block in doc.iter_inner_content():
                if isinstance(block, Table):
                    table_no += 1
                    rows = block.rows
                    if rows:
                        header = [c.text for c in rows[0].cells]
                        for number, row in enumerate(rows, 1):
                            add(
                                "；".join(
                                    f"{h}: {cell.text}"
                                    for h, cell in zip(header, row.cells, strict=False)
                                ),
                                {
                                    "type": "docx",
                                    "heading": heading,
                                    "table": table_no,
                                    "row": number,
                                },
                                indexed=number > 1,
                            )
                else:
                    paragraph_no += 1
                    is_heading = block.style and (
                        block.style.name.startswith("Heading") or block.style.name == "Title"
                    )
                    if is_heading:
                        heading = block.text
                    add(
                        block.text,
                        {"type": "docx", "heading": heading, "paragraph": paragraph_no},
                        indexed=not is_heading,
                    )
        else:
            raise invalid("仅支持.md、.pdf、.docx")
    except ApiError:
        raise
    except Exception:
        raise invalid("文件损坏、加密或无法解析，请检查文件后重新上传") from None
    if not parts:
        raise invalid("未提取到可用正文，请检查文件是否为空或扫描件", "EMPTY_DOCUMENT")
    return parts, total
