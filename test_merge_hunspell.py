#!/usr/bin/env python3
"""
test_merge_hunspell.py - Unit and Integration Tests for Refined Hunspell Merger
"""

import unittest
import tempfile
import os
import subprocess
from merge_hunspell import is_safe_zip_path, normalize_codec_name, is_junk, split_flags

class TestHunspellMergerRefined(unittest.TestCase):
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

    def test_junk_detection(self):
        self.assertTrue(is_junk("__MACOSX/._dict.dic"))
        self.assertTrue(is_junk("folder/._file.aff"))
        self.assertFalse(is_junk("folder/file.aff"))

    def test_normalize_codec_name(self):
        self.assertEqual(normalize_codec_name("utf-8"), "utf-8-sig")
        self.assertEqual(normalize_codec_name("UTF8"), "utf-8-sig")
        self.assertEqual(normalize_codec_name("microsoft-cp1251"), "cp1251")

    def test_split_flags(self):
        self.assertEqual(split_flags("AB", "char"), ["A", "B"])
        self.assertEqual(split_flags("AaBb", "long"), ["Aa", "Bb"])
        self.assertEqual(split_flags("101,102", "num"), ["101", "102"])

    def test_cli_execution_and_hunspell_verification(self):
        cmd = ["python3", "merge_hunspell.py", "--union-classes", "--legacy-names"]
        p = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, f"Script failed: {p.stderr}")
        self.assertTrue(os.path.exists("Dictionary.aff"))
        self.assertTrue(os.path.exists("Ukrainian Dictionary.dic"))

if __name__ == "__main__":
    unittest.main()
