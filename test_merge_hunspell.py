#!/usr/bin/env python3
"""
test_merge_hunspell.py - Robust Unit and Integration Tests for Hunspell Merger
"""

import unittest
import tempfile
import os
import subprocess
from merge_hunspell import HunspellMerger, parse_dic_flags, is_safe_zip_path, detect_file_encoding, verify_with_hunspell

class TestHunspellMerger(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = self.tmp_dir.name

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_zip_slip_protection(self):
        self.assertTrue(is_safe_zip_path(self.work_dir, "good_file.txt"))
        self.assertTrue(is_safe_zip_path(self.work_dir, "subfolder/good_file.txt"))
        self.assertFalse(is_safe_zip_path(self.work_dir, "../outside.txt"))
        self.assertFalse(is_safe_zip_path(self.work_dir, "/etc/passwd"))

    def test_parse_dic_flags(self):
        self.assertEqual(parse_dic_flags("AB", "char"), ["A", "B"])
        self.assertEqual(parse_dic_flags("AaBb", "long"), ["Aa", "Bb"])
        self.assertEqual(parse_dic_flags("101,102", "num"), ["101", "102"])

    def test_detect_file_encoding(self):
        aff_path = os.path.join(self.work_dir, "test.aff")
        with open(aff_path, "wb") as f:
            f.write(b"SET UTF-8\nTRY abc\n")
        self.assertEqual(detect_file_encoding(aff_path), "UTF-8")

    def test_aff_merging_af_compoundrule_and_counters(self):
        aff1_path = os.path.join(self.work_dir, "aff1.aff")
        aff2_path = os.path.join(self.work_dir, "aff2.aff")

        with open(aff1_path, "w", encoding="utf-8") as f:
            f.write(
                "SET UTF-8\n"
                "TRY abc\n"
                "AF 1\n"
                "AF A\n"
                "COMPOUNDRULE 1\n"
                "COMPOUNDRULE (a)(b)\n"
                "SFX A Y 1\n"
                "SFX A 0 a .\n"
            )

        with open(aff2_path, "w", encoding="utf-8") as f:
            f.write(
                "SET UTF-8\n"
                "TRY abc\n"
                "AF 1\n"
                "AF B\n"
                "COMPOUNDRULE 1\n"
                "COMPOUNDRULE (c)(d)\n"
                "SFX A Y 1\n"
                "SFX A 0 b .\n"
            )

        merger = HunspellMerger(self.work_dir)
        merger.aff_files = [aff1_path, aff2_path]
        aff_content, defined_flags, flag_mode = merger.merge_aff()

        self.assertIn("SET UTF-8", aff_content)
        self.assertIn("AF 2", aff_content)
        self.assertIn("AF A", aff_content)
        self.assertIn("AF B", aff_content)
        self.assertIn("COMPOUNDRULE 2", aff_content)
        self.assertIn("COMPOUNDRULE (a)(b)", aff_content)
        self.assertIn("COMPOUNDRULE (c)(d)", aff_content)
        self.assertIn("SFX A Y 2", aff_content)

    def test_pfx_sfx_combinability_conflict_raises_error(self):
        aff1_path = os.path.join(self.work_dir, "aff1.aff")
        aff2_path = os.path.join(self.work_dir, "aff2.aff")

        with open(aff1_path, "w", encoding="utf-8") as f:
            f.write("SET UTF-8\nSFX A Y 1\nSFX A 0 a .\n")

        with open(aff2_path, "w", encoding="utf-8") as f:
            f.write("SET UTF-8\nSFX A N 1\nSFX A 0 b .\n")

        merger = HunspellMerger(self.work_dir)
        merger.aff_files = [aff1_path, aff2_path]
        with self.assertRaises(ValueError):
            merger.merge_aff()

    def test_dic_exact_duplicates_preserved_flag_sets(self):
        dic1_path = os.path.join(self.work_dir, "dic1.dic")
        dic2_path = os.path.join(self.work_dir, "dic2.dic")

        with open(dic1_path, "w", encoding="utf-8") as f:
            f.write("3\nслово/A\nслово/A\nслово/B\n")

        with open(dic2_path, "w", encoding="utf-8") as f:
            f.write("2\nслово/B\nінше/C\n")

        merger = HunspellMerger(self.work_dir)
        merger.dic_files = [dic1_path, dic2_path]
        dic_content, word_count = merger.merge_dic({"A", "B", "C"}, "char")

        lines = dic_content.splitlines()
        self.assertEqual(lines[0], "3")
        self.assertIn("слово/A", lines)
        self.assertIn("слово/B", lines)
        self.assertIn("інше/C", lines)
        self.assertNotIn("слово/AB", lines)

    def test_unsupported_directive_raises_error(self):
        aff1_path = os.path.join(self.work_dir, "aff1.aff")
        with open(aff1_path, "w", encoding="utf-8") as f:
            f.write("UNKNOWN_DIRECTIVE_123 foo\n")

        merger = HunspellMerger(self.work_dir)
        merger.aff_files = [aff1_path]
        with self.assertRaises(ValueError):
            merger.merge_aff()

    def test_verify_with_hunspell_function(self):
        aff_path = os.path.join(self.work_dir, "Dictionary.aff")
        dic_path = os.path.join(self.work_dir, "Ukrainian Dictionary.dic")

        with open(aff_path, "w", encoding="utf-8") as f:
            f.write("SET UTF-8\nTRY abc\n")

        with open(dic_path, "w", encoding="utf-8") as f:
            f.write("2\nабзац\nнайсправжнісінький\n")

        self.assertTrue(verify_with_hunspell(aff_path, dic_path))

if __name__ == "__main__":
    unittest.main()
