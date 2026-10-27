#!/usr/bin/env python3
"""
test_merge_hunspell.py - Unit and Integration Tests for Hunspell Merger
"""

import unittest
import tempfile
import os
import subprocess
from merge_hunspell import HunspellMerger, parse_dic_flags

class TestHunspellMerger(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = self.tmp_dir.name

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_parse_dic_flags(self):
        self.assertEqual(parse_dic_flags("AB", "char"), ["A", "B"])
        self.assertEqual(parse_dic_flags("AaBb", "long"), ["Aa", "Bb"])
        self.assertEqual(parse_dic_flags("101,102", "num"), ["101", "102"])

    def test_aff_merging_and_counters(self):
        aff1_path = os.path.join(self.work_dir, "aff1.aff")
        aff2_path = os.path.join(self.work_dir, "aff2.aff")

        with open(aff1_path, "w", encoding="utf-8") as f:
            f.write(
                "SET UTF-8\n"
                "TRY abc\n"
                "REP 1\n"
                "REP foo bar\n"
                "SFX A Y 1\n"
                "SFX A 0 a .\n"
            )

        with open(aff2_path, "w", encoding="utf-8") as f:
            f.write(
                "SET UTF-8\n"
                "TRY abc\n"
                "REP 1\n"
                "REP baz qux\n"
                "SFX A Y 1\n"
                "SFX A 0 b .\n"
            )

        merger = HunspellMerger(self.work_dir)
        merger.aff_files = [aff1_path, aff2_path]
        aff_content, defined_flags, flag_mode = merger.merge_aff()

        self.assertIn("SET UTF-8", aff_content)
        self.assertIn("REP 2", aff_content)
        self.assertIn("REP baz qux", aff_content)
        self.assertIn("REP foo bar", aff_content)
        self.assertIn("SFX A Y 2", aff_content)
        self.assertIn("SFX A 0 a .", aff_content)
        self.assertIn("SFX A 0 b .", aff_content)
        self.assertIn("A", defined_flags)

    def test_dic_exact_duplicates_and_different_flags(self):
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

    def test_aff_conflict_detection(self):
        aff1_path = os.path.join(self.work_dir, "aff1.aff")
        aff2_path = os.path.join(self.work_dir, "aff2.aff")

        with open(aff1_path, "w", encoding="utf-8") as f:
            f.write("SET UTF-8\n")

        with open(aff2_path, "w", encoding="utf-8") as f:
            f.write("SET ISO8859-1\n")

        merger = HunspellMerger(self.work_dir)
        merger.aff_files = [aff1_path, aff2_path]
        with self.assertRaises(ValueError):
            merger.merge_aff()

    def test_unsupported_directive_raises_error(self):
        aff1_path = os.path.join(self.work_dir, "aff1.aff")
        with open(aff1_path, "w", encoding="utf-8") as f:
            f.write("UNKNOWN_DIRECTIVE_123 foo\n")

        merger = HunspellMerger(self.work_dir)
        merger.aff_files = [aff1_path]
        with self.assertRaises(ValueError):
            merger.merge_aff()

    def test_real_hunspell_load_and_spellcheck(self):
        # Generate files dynamically in temporary directory
        aff_path = os.path.join(self.work_dir, "Dictionary.aff")
        dic_path = os.path.join(self.work_dir, "Dictionary.dic")

        with open(aff_path, "w", encoding="utf-8") as f:
            f.write("SET UTF-8\nTRY abc\n")

        with open(dic_path, "w", encoding="utf-8") as f:
            f.write("2\nабзац\nнайсправжнісінький\n")

        dict_base = os.path.join(self.work_dir, "Dictionary")
        cmd = ['hunspell', '-d', dict_base, '-l']
        input_words = "абзац\nнайсправжнісінький\nнеіснуючеслово999\n"
        p = subprocess.run(cmd, input=input_words, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, f"Hunspell failed with stderr: {p.stderr}")

        misspelled = [w.strip() for w in p.stdout.splitlines() if w.strip()]
        self.assertIn("неіснуючеслово999", misspelled)
        self.assertNotIn("абзац", misspelled)

if __name__ == "__main__":
    unittest.main()
