import bz2
import unittest

from spark_pipeline import (
    find_bzip2_stream_headers,
    parse_bzip2_member,
    parse_game_page,
)
from wiki_parser import is_game_article, parse_page


class WikiParserGameFilterTests(unittest.TestCase):
    def test_keeps_main_namespace_video_game_article(self) -> None:
        page = parse_page(
            """<page>
            <title>Example RPG</title>
            <ns>0</ns>
            <id>123</id>
            <revision><text xml:space="preserve">
            Example RPG is a role-playing video game.

            [[Category:2024 video games]]
            </text></revision>
            </page>"""
        )

        self.assertIsNotNone(page)
        self.assertTrue(is_game_article(page))

    def test_rejects_non_main_namespace_page(self) -> None:
        page = parse_page(
            """<page>
            <title>Example RPG</title>
            <ns>1</ns>
            <id>123</id>
            <revision><text>Example RPG is a video game.</text></revision>
            </page>"""
        )

        self.assertIsNone(page)

    def test_rejects_article_about_a_comic_with_same_game_title(self) -> None:
        page = parse_page(
            """<page>
            <title>The Amazing Spider-Man</title>
            <ns>0</ns>
            <id>931</id>
            <revision><text xml:space="preserve">
            The Amazing Spider-Man is an American comic book series.

            [[Category:Comics]]
            </text></revision>
            </page>"""
        )

        self.assertIsNotNone(page)
        self.assertFalse(is_game_article(page))

    def test_rejects_article_about_the_video_game_industry(self) -> None:
        page = parse_page(
            """<page>
            <title>Video game industry</title>
            <ns>0</ns>
            <id>932</id>
            <revision><text xml:space="preserve">
            The video game industry is the economic sector involved in games.

            [[Category:Video game industry]]
            </text></revision>
            </page>"""
        )

        self.assertIsNotNone(page)
        self.assertFalse(is_game_article(page))


class WikiDumpBlockTests(unittest.TestCase):
    def test_detects_concatenated_streams_without_duplicate_boundary(self) -> None:
        first_member = bz2.compress(b"<mediawiki/>")
        second_member = bz2.compress(b"<mediawiki/>")
        dump = first_member + second_member
        boundary = len(first_member)

        first_chunk = dump[: boundary + 9]
        second_chunk = dump[boundary:]

        self.assertEqual(
            find_bzip2_stream_headers(first_chunk, 0, boundary),
            [0],
        )
        self.assertEqual(
            find_bzip2_stream_headers(
                second_chunk, boundary, len(dump)
            ),
            [boundary],
        )

    def test_decompresses_and_filters_pages_from_a_bzip2_member(self) -> None:
        xml = (
            b"<mediawiki><page><title>Example RPG</title><ns>0</ns><id>123</id>"
            b"<revision><text>Example RPG is a role-playing video game."
            b"</text></revision></page></mediawiki>"
        )

        pages = list(parse_bzip2_member(bz2.compress(xml)))

        self.assertEqual([page["wiki_title"] for page in pages], ["Example RPG"])

    def test_skips_pages_without_game_signal_in_lead_or_categories(self) -> None:
        page_xml = (
            b"<page><title>History</title><ns>0</ns><id>124</id>"
            b"<revision><text>History is a subject.\n\n"
            b"Some later text mentions a video game.</text></revision></page>"
        )

        self.assertIsNone(parse_game_page(page_xml))

    def test_keeps_game_article_identified_by_category(self) -> None:
        page_xml = (
            b"<page><title>Example Game</title><ns>0</ns><id>125</id>"
            b"<revision><text>Example Game is an interactive experience.\n\n"
            b"[[Category:2024 video games]]</text></revision></page>"
        )

        page = parse_game_page(page_xml)

        self.assertIsNotNone(page)
        self.assertEqual(page["wiki_title"], "Example Game")


if __name__ == "__main__":
    unittest.main()
