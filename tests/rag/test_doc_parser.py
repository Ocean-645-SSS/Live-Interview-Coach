"""测试 M1 最小文档解析器的输入边界。"""

import pytest

from liverag.rag.doc_parser import parse_file_content


def test_parse_utf8_txt_returns_decoded_content():
    """测试正常UTF-8和.txt文件可以被解析，并且和原文一致"""
    content = "LiveRAG 支持 UTF-8 文本。\n第二行内容。"

    result = parse_file_content(content.encode("utf-8"), ".txt")

    assert result == content


def test_parse_markdown_returns_source_unchanged():
    """测试正常.md文件可以被解析，并且和原文一致"""
    content = "# LiveRAG\n\n- 保留 Markdown 标记\n- 保留原始换行\n"

    result = parse_file_content(content.encode("utf-8"), ".md")

    assert result == content


def test_parse_empty_file_raises_value_error():
    """空文件会被拒绝"""
    with pytest.raises(ValueError):
        parse_file_content(b"", ".txt")


def test_parse_invalid_utf8_raises_value_error():
    """验证无法按UTF-8解码的字节会抛出ValueError"""
    with pytest.raises(ValueError):
        parse_file_content(b"\xff\xfe\xfa", ".txt")


def test_parse_unsupported_extension_raises_value_error():
    """验证不被支持的文件类型会抛出异常"""
    with pytest.raises(ValueError):
        parse_file_content(b"not a real PDF", ".pdf")
