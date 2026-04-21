"""Tests para el renderer markdown → Telegram HTML."""

from __future__ import annotations

from adapters.inbound.telegram.message_mapper import format_response


def test_empty_response_returns_empty():
    assert format_response("") == ""


def test_plain_text_passthrough():
    assert format_response("hola mundo") == "hola mundo"


def test_escapes_html_entities_in_plain_text():
    assert format_response("1 < 2 && 3 > 2") == "1 &lt; 2 &amp;&amp; 3 &gt; 2"


def test_bold_maps_to_b_tag():
    assert format_response("**hola**") == "<b>hola</b>"


def test_italic_maps_to_i_tag():
    assert format_response("*hola*") == "<i>hola</i>"


def test_strikethrough_maps_to_s_tag():
    assert format_response("~~hola~~") == "<s>hola</s>"


def test_inline_code_maps_to_code_tag():
    assert format_response("usá `print()` nomás") == "usá <code>print()</code> nomás"


def test_inline_code_escapes_entities():
    assert format_response("`a<b>`") == "<code>a&lt;b&gt;</code>"


def test_fenced_code_block_with_language():
    md = "```python\nprint('hi')\n```"
    assert format_response(md) == "<pre><code class=\"language-python\">print('hi')</code></pre>"


def test_fenced_code_block_without_language():
    md = "```\nfoo\n```"
    assert format_response(md) == "<pre>foo</pre>"


def test_fenced_code_block_escapes_entities():
    md = "```\n<tag>\n```"
    assert format_response(md) == "<pre>&lt;tag&gt;</pre>"


def test_link_maps_to_a_tag():
    assert format_response("[click](https://ex.com)") == '<a href="https://ex.com">click</a>'


def test_link_escapes_href():
    result = format_response("[x](https://ex.com?a=1&b=2)")
    assert '<a href="https://ex.com?a=1&amp;b=2">x</a>' == result


def test_heading_becomes_bold():
    result = format_response("# Título")
    assert result == "<b>Título</b>"


def test_multiple_headings_and_paragraphs():
    md = "# H1\n\nparrafo uno\n\n## H2\n\nparrafo dos"
    result = format_response(md)
    assert "<b>H1</b>" in result
    assert "<b>H2</b>" in result
    assert "parrafo uno" in result
    assert "parrafo dos" in result


def test_unordered_list():
    md = "- uno\n- dos\n- tres"
    result = format_response(md)
    assert "• uno" in result
    assert "• dos" in result
    assert "• tres" in result


def test_ordered_list():
    md = "1. primero\n2. segundo"
    result = format_response(md)
    assert "1. primero" in result
    assert "2. segundo" in result


def test_nested_unordered_list():
    md = "- padre\n  - hijo"
    result = format_response(md)
    assert "• padre" in result
    assert "  • hijo" in result


def test_blockquote_maps_to_blockquote_tag():
    result = format_response("> una cita")
    assert result == "<blockquote>una cita</blockquote>"


def test_horizontal_rule_becomes_separator():
    result = format_response("arriba\n\n---\n\nabajo")
    assert "arriba" in result
    assert "──────────" in result
    assert "abajo" in result


def test_mixed_inline_formatting():
    md = "un **bold** y un *italic* y `code`"
    result = format_response(md)
    assert result == "un <b>bold</b> y un <i>italic</i> y <code>code</code>"


def test_no_unescaped_angle_brackets_from_llm_content():
    """El LLM puede devolver texto con < > que no son HTML — deben escaparse."""
    md = "if a < b and b > c: return"
    result = format_response(md)
    assert "<" not in result.replace("&lt;", "")
    assert ">" not in result.replace("&gt;", "")


def test_realistic_llm_response():
    md = (
        "# Resumen\n\n"
        "Encontré **3 bugs** en el módulo `auth`:\n\n"
        "1. Token expirado\n"
        "2. Race condition\n"
        "3. SQL injection\n\n"
        "Ver [docs](https://docs.ex.com) para más info."
    )
    result = format_response(md)
    assert "<b>Resumen</b>" in result
    assert "<b>3 bugs</b>" in result
    assert "<code>auth</code>" in result
    assert "1. Token expirado" in result
    assert '<a href="https://docs.ex.com">docs</a>' in result
