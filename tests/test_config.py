import unittest

from app.config import parse_csv_set


class ParseCsvSetTests(unittest.TestCase):
    def test_parses_trims_and_deduplicates_values(self) -> None:
        self.assertEqual(
            parse_csv_set("first, second,first,, "),
            frozenset({"first", "second"}),
        )

    def test_empty_value_is_fail_closed_empty_set(self) -> None:
        self.assertEqual(parse_csv_set(""), frozenset())


if __name__ == "__main__":
    unittest.main()
