#!/usr/bin/env python3
"""
test_merge_hunspell.py - Robust Unit and Integration Tests for Universal Hunspell Merger
"""

import unittest
import tempfile
import os
import subprocess
from merge_hunspell import (
    UniversalHunspellMerger,
    AffixModel,
    DictionaryEntry,
    is_safe_zip_path,
    detect_file_encoding,
    verify_with_hunspell
)

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

    def test_detect_file_encoding(self):
        aff_path = os.path.join(self.work_dir, "test.aff")
        with open(aff_path, "wb") as f:
            f.write(b"SET UTF-8\nTRY abc\n")
        self.assertEqual(detect_file_encoding(aff_path), "utf-8-sig")

    def test_af_alias_expansion_in_dic_and_merge(self):
        aff1_path = os.path.join(self.work_dir, "test1.aff")
        dic1_path = os.path.join(self.work_dir, "test1.dic")

        with open(aff1_path, "w", encoding="utf-8") as f:
            f.write("SET UTF-8\nAF 1\nAF A\nSFX A Y 1\nSFX A 0 a .\n")
        with open(dic1_path, "w", encoding="utf-8") as f:
            f.write("1\nслово/1\n")

        merger = UniversalHunspellMerger(self.work_dir, union_classes=True)
        merger.aff_models = [AffixModel(aff1_path)]
        merger.dic_paths = [dic1_path]

        aff_out, dic_out, word_count = merger.merge()
        self.assertEqual(word_count, 1)
        self.assertIn("AF 1", aff_out)
        self.assertIn("AF A", aff_out)
        self.assertIn("слово/1", dic_out)

    def test_different_pfx_sfx_classes_conflict(self):
        aff1_path = os.path.join(self.work_dir, "test1.aff")
        aff2_path = os.path.join(self.work_dir, "test2.aff")

        with open(aff1_path, "w", encoding="utf-8") as f:
            f.write("SET UTF-8\nSFX A Y 1\nSFX A 0 a .\n")
        with open(aff2_path, "w", encoding="utf-8") as f:
            f.write("SET UTF-8\nSFX A Y 1\nSFX A 0 b .\n")

        merger = UniversalHunspellMerger(self.work_dir, union_classes=False)
        merger.aff_models = [AffixModel(aff1_path), AffixModel(aff2_path)]
        with self.assertRaises(ValueError):
            merger.merge()

    def test_verify_with_hunspell_staging(self):
        stage_dir = os.path.join(self.work_dir, "stage")
        os.makedirs(stage_dir, exist_ok=True)
        aff_path = os.path.join(stage_dir, "uk_UA.aff")
        dic_path = os.path.join(stage_dir, "uk_UA.dic")

        with open(aff_path, "w", encoding="utf-8") as f:
            f.write("SET UTF-8\nTRY abc\n")
        with open(dic_path, "w", encoding="utf-8") as f:
            f.write("2\nабзац\nнайкращий\n")

        self.assertTrue(verify_with_hunspell(aff_path, ["абзац", "найкращий"], ["неіснуючеслово999xyz"]))

if __name__ == "__main__":
    unittest.main()
