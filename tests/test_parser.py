import pytest

from envdoc.parser import ParseError, parse, parse_file


def entries(text):
    return {ln.key: ln.value for ln in parse(text).entries()}


class TestBasics:
    def test_simple_assignment(self):
        assert entries("APP_NAME=Laravel\n") == {"APP_NAME": "Laravel"}

    def test_export_prefix(self):
        env = parse("export DB_HOST=127.0.0.1\n")
        line = env.get("DB_HOST")
        assert line.value == "127.0.0.1"
        assert line.exported

    def test_spaces_around_equals(self):
        assert entries("KEY = value\n") == {"KEY": "value"}

    def test_empty_value(self):
        assert entries("APP_KEY=\n") == {"APP_KEY": ""}

    def test_no_trailing_newline(self):
        assert entries("A=1") == {"A": "1"}

    def test_crlf_line_endings(self):
        assert entries("A=1\r\nB=2\r\n") == {"A": "1", "B": "2"}


class TestQuoting:
    def test_double_quotes_are_stripped(self):
        assert entries('NAME="Finico ERP"\n') == {"NAME": "Finico ERP"}

    def test_single_quotes_are_literal(self):
        assert entries("P='a\\nb'\n") == {"P": "a\\nb"}

    def test_double_quote_escapes(self):
        assert entries('P="a\\nb\\t\\"c\\""\n') == {"P": 'a\nb\t"c"'}

    def test_hash_inside_quotes_is_not_a_comment(self):
        assert entries('PW="abc # def"\n') == {"PW": "abc # def"}

    def test_records_quote_style(self):
        env = parse("A='x'\nB=\"y\"\nC=z\n")
        assert [ln.quote for ln in env.entries()] == ["'", '"', ""]

    def test_multiline_double_quoted_value(self):
        text = 'KEY="-----BEGIN-----\nabc\n-----END-----"\nNEXT=1\n'
        env = parse(text)
        assert env.get("KEY").value == "-----BEGIN-----\nabc\n-----END-----"
        assert env.get("NEXT").value == "1"
        assert env.get("NEXT").lineno == 4

    def test_unterminated_quote_is_invalid_not_fatal(self):
        env = parse('BROKEN="never closed\nOTHER=1\n')
        assert env.invalid()[0].problem == 'unterminated " quote'
        # It swallows to EOF looking for the quote; nothing is lost.
        assert env.render() == 'BROKEN="never closed\nOTHER=1\n'


class TestComments:
    def test_full_line_comment(self):
        env = parse("# database\nDB=x\n")
        assert [ln.kind for ln in env.lines] == ["comment", "entry"]

    def test_inline_comment_on_unquoted_value(self):
        assert entries("PORT=3306 # mysql default\n") == {"PORT": "3306"}

    def test_hash_without_leading_space_is_part_of_value(self):
        assert entries("URL=http://x/#frag\n") == {"URL": "http://x/#frag"}


class TestInvalid:
    def test_line_without_equals(self):
        env = parse("just some text\n")
        assert env.invalid()[0].problem == "no '=' assignment"

    def test_bad_key(self):
        env = parse("1BAD=x\n")
        assert "invalid key" in env.invalid()[0].problem

    def test_invalid_lines_survive_render(self):
        text = "GOOD=1\nnot an assignment\n"
        assert parse(text).render() == text


class TestFileView:
    def test_keys_in_first_seen_order(self):
        assert parse("B=1\nA=2\nB=3\n").keys() == ["B", "A"]

    def test_last_assignment_wins(self):
        assert parse("A=1\nA=2\n").as_dict() == {"A": "2"}

    def test_duplicates_report_every_line(self):
        assert parse("A=1\nB=2\nA=3\n").duplicates() == {"A": [1, 3]}

    def test_render_is_lossless(self):
        text = (
            "# App\n"
            "APP_NAME=\"My App\"\n"
            "\n"
            "export DEBUG=true # on locally\n"
            "KEY='-----\n"
            "multi\n"
            "-----'\n"
            "garbage line\n"
        )
        assert parse(text).render() == text


class TestParseFile:
    def test_reads_a_file(self, tmp_path):
        p = tmp_path / ".env"
        p.write_text("A=1\n", encoding="utf-8")
        env = parse_file(p)
        assert env.as_dict() == {"A": "1"}
        assert env.path == p

    def test_strips_utf8_bom(self, tmp_path):
        p = tmp_path / ".env"
        p.write_bytes("﻿APP_NAME=x\n".encode())
        assert parse_file(p).keys() == ["APP_NAME"]

    def test_missing_file(self, tmp_path):
        with pytest.raises(ParseError, match="cannot read"):
            parse_file(tmp_path / "nope")

    def test_binary_file(self, tmp_path):
        p = tmp_path / ".env"
        p.write_bytes(b"\xff\xfe\x00bad")
        with pytest.raises(ParseError, match="not UTF-8"):
            parse_file(p)
