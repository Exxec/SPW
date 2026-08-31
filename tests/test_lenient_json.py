import unittest

from spw.lenient_json import loads


class LenientJsonTests(unittest.TestCase):
    def test_strict_valid_json_passes_through(self) -> None:
        self.assertEqual(loads('{"id": "a", "n": 1}'), {"id": "a", "n": 1})

    def test_hash_comment_is_stripped(self) -> None:
        text = '{\n  "id": "a", # this is a comment\n  "n": 1\n}'
        self.assertEqual(loads(text), {"id": "a", "n": 1})

    def test_leading_hash_comment_before_object_is_stripped(self) -> None:
        text = "# generated file, note Starsector's parser allows this\n{\"id\": \"a\"}"
        self.assertEqual(loads(text), {"id": "a"})

    def test_slash_comment_is_stripped(self) -> None:
        text = '{\n  "id": "a", // trailing note\n  "n": 1\n}'
        self.assertEqual(loads(text), {"id": "a", "n": 1})

    def test_trailing_comma_in_object_and_array(self) -> None:
        text = '{"id": "a", "deps": ["x", "y",], "n": 1,}'
        self.assertEqual(loads(text), {"id": "a", "deps": ["x", "y"], "n": 1})

    def test_single_quoted_scalar_values(self) -> None:
        text = '{"version": {"major": \'1\', "minor": \'20\'}}'
        self.assertEqual(loads(text), {"version": {"major": "1", "minor": "20"}})

    def test_apostrophe_inside_comment_does_not_corrupt_parsing(self) -> None:
        text = "{\n  # a mod author's note, it's fine\n  \"id\": \"a\"\n}"
        self.assertEqual(loads(text), {"id": "a"})

    def test_apostrophe_inside_double_quoted_string_is_preserved(self) -> None:
        text = '{"description": "press \'E\' to open, it\'s quick"}'
        self.assertEqual(loads(text), {"description": "press 'E' to open, it's quick"})

    def test_hash_inside_double_quoted_string_is_preserved(self) -> None:
        text = '{"description": "costs #500 credits"}'
        self.assertEqual(loads(text), {"description": "costs #500 credits"})

    def test_slashes_inside_double_quoted_url_are_preserved(self) -> None:
        text = '{"source": "https://example.com/mod"}'
        self.assertEqual(loads(text), {"source": "https://example.com/mod"})

    def test_combination_matching_a_real_mod_info_shape(self) -> None:
        text = (
            "# generated file\n"
            "{\n"
            '\t"id":"example_mod",\n'
            '\t"name":"Example",\n'
            '\t"version": { "major":\'1\', "minor": \'2\', "patch": \'3\' },\n'
            "\n"
            "\t# a comment about compatibility, don't change this lightly\n"
            '\t"gameVersion":"0.98a-RC5",\n'
            '\t"dependencies": [\n'
            '\t\t{"id": "lazylib", "name": "LazyLib"},\n'
            "\t],\n"
            "}\n"
        )
        result = loads(text)
        self.assertEqual(result["id"], "example_mod")
        self.assertEqual(result["version"], {"major": "1", "minor": "2", "patch": "3"})
        self.assertEqual(result["dependencies"], [{"id": "lazylib", "name": "LazyLib"}])

    def test_invalid_json_still_raises(self) -> None:
        with self.assertRaises(Exception):
            loads("{not json at all")


if __name__ == "__main__":
    unittest.main()
