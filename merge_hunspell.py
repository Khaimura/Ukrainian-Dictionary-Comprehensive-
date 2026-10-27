#!/usr/bin/env python3
"""
merge_hunspell.py - Robust Structural Hunspell AFF and DIC Merger for Ukrainian Dictionary
"""

import sys
import os
import glob
import zipfile
import tempfile
import argparse
import collections
import subprocess

def is_safe_zip_path(target_dir, member_path):
    """Prevent Zip Slip vulnerabilities by verifying path containment."""
    abs_target = os.path.abspath(target_dir)
    abs_member = os.path.abspath(os.path.join(target_dir, member_path))
    return abs_member.startswith(abs_target + os.sep) or abs_member == abs_target

def parse_dic_flags(flag_str, flag_mode='char'):
    """Parse dictionary flag string based on FLAG mode in AFF."""
    if not flag_str:
        return []
    if flag_mode == 'long':
        return [flag_str[i:i+2] for i in range(0, len(flag_str), 2)]
    elif flag_mode == 'num':
        return [f.strip() for f in flag_str.split(',') if f.strip()]
    else:  # 'char' or 'UTF-8'
        return list(flag_str)

def detect_file_encoding(filepath):
    """Detect encoding by searching for SET directive in raw bytes."""
    with open(filepath, 'rb') as f:
        head = f.read(2048)
    for line in head.splitlines():
        line_str = line.strip()
        if line_str.startswith(b'SET '):
            parts = line_str.split(None, 1)
            if len(parts) >= 2:
                enc = parts[1].decode('ascii', errors='ignore').strip()
                if enc:
                    return enc
    return 'utf-8'

class HunspellMerger:
    def __init__(self, work_dir):
        self.work_dir = work_dir
        self.aff_files = []
        self.dic_files = []

    def unpack_sources(self, zip_path):
        """Recursively unpack ZIP archives and nested .oxt files with Zip Slip protection."""
        print(f"Unpacking {zip_path} into {self.work_dir}...")
        with zipfile.ZipFile(zip_path, 'r') as z:
            for member in z.infolist():
                if not is_safe_zip_path(self.work_dir, member.filename):
                    raise ValueError(f"Unsafe ZIP member path detected (Zip Slip): {member.filename}")
            z.extractall(self.work_dir)

        # Iteratively extract nested .oxt archives
        while True:
            oxt_files = glob.glob(os.path.join(self.work_dir, '**/*.oxt'), recursive=True)
            unpacked_any = False
            for oxt in oxt_files:
                oxt_out = oxt + '_unpacked'
                if not os.path.exists(oxt_out):
                    with zipfile.ZipFile(oxt, 'r') as z:
                        for member in z.infolist():
                            if not is_safe_zip_path(oxt_out, member.filename):
                                raise ValueError(f"Unsafe OXT member path detected: {member.filename}")
                        z.extractall(oxt_out)
                    unpacked_any = True
            if not unpacked_any:
                break

        # Filter spellcheck .dic and .aff files strictly
        all_aff = glob.glob(os.path.join(self.work_dir, '**/*.aff'), recursive=True)
        all_dic = glob.glob(os.path.join(self.work_dir, '**/*.dic'), recursive=True)

        for dic in sorted(all_dic):
            base_name = os.path.basename(dic)
            # Ignore hyphenation dictionaries, thesauri data/indexes, and other non-spellcheck files
            if base_name.startswith('hyph_') or base_name.startswith('th_'):
                continue
            self.dic_files.append(dic)

        for aff in sorted(all_aff):
            base_name = os.path.basename(aff)
            if base_name.startswith('hyph_') or base_name.startswith('th_'):
                continue
            self.aff_files.append(aff)

        print(f"Found {len(self.aff_files)} .aff files and {len(self.dic_files)} spellcheck .dic files.")

    def merge_aff(self):
        """Structurally merge all .aff files with full directive parsing and conflict detection."""
        print("Merging .aff files...")

        single_directives = {}  # key -> (val, source_file)
        counted_directives = {
            'BREAK': set(),
            'MAP': set(),
            'REP': set(),
            'ICONV': set(),
            'OCONV': set(),
            'AF': [],           # Preserves ordered list of flag aliases
            'COMPOUNDRULE': set(),
            'CHECKCOMPOUNDPATTERN': set()
        }

        # PFX/SFX: flag -> {'comb': 'Y'/'N', 'comb_src': file, 'rules': list_of_tuples}
        pfx_classes = {}
        sfx_classes = {}

        supported_single = {
            'SET', 'FLAG', 'TRY', 'WORDCHARS', 'IGNORE', 'COMPOUNDFLAG',
            'KEEPCASE', 'NEEDAFFIX', 'FORBIDDENWORD', 'NOSUGGEST', 'CIRCUMFIX',
            'ONLYINCOMPOUND', 'COMPOUNDMIN', 'COMPOUNDWORDMAX'
        }

        for aff_path in self.aff_files:
            rel_path = os.path.relpath(aff_path, self.work_dir)
            encoding = detect_file_encoding(aff_path)

            with open(aff_path, 'r', encoding=encoding) as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    parts = line.split()
                    key = parts[0]

                    if key in supported_single:
                        val = line
                        if key in single_directives:
                            existing_val, existing_src = single_directives[key]
                            if existing_val != val:
                                raise ValueError(
                                    f"Conflict in scalar directive {key}!\n"
                                    f"  Source 1 ({existing_src}): {existing_val}\n"
                                    f"  Source 2 ({rel_path}): {val}"
                                )
                        else:
                            single_directives[key] = (val, rel_path)

                    elif key in ('BREAK', 'MAP', 'REP', 'ICONV', 'OCONV', 'COMPOUNDRULE', 'CHECKCOMPOUNDPATTERN'):
                        # Ignore header lines with counts (e.g. 'REP 35', 'COMPOUNDRULE 2')
                        if len(parts) == 2 and parts[1].isdigit():
                            continue
                        counted_directives[key].add(line)

                    elif key == 'AF':
                        # AF directive can be 'AF count' or 'AF flag_set'
                        if len(parts) == 2 and parts[1].isdigit():
                            continue
                        entry = " ".join(parts[1:])
                        if entry not in counted_directives['AF']:
                            counted_directives['AF'].append(entry)

                    elif key in ('PFX', 'SFX'):
                        if len(parts) < 2:
                            continue
                        flag = parts[1]
                        classes = pfx_classes if key == 'PFX' else sfx_classes

                        if flag not in classes:
                            classes[flag] = {'comb': None, 'comb_src': None, 'rules': []}

                        if len(parts) == 4 and parts[2] in ('Y', 'N') and parts[3].isdigit():
                            comb = parts[2]
                            if classes[flag]['comb'] is not None and classes[flag]['comb'] != comb:
                                raise ValueError(
                                    f"Conflict in {key} class '{flag}' combinability parameter!\n"
                                    f"  Source 1 ({classes[flag]['comb_src']}): {key} {flag} {classes[flag]['comb']}\n"
                                    f"  Source 2 ({rel_path}): {key} {flag} {comb}"
                                )
                            classes[flag]['comb'] = comb
                            classes[flag]['comb_src'] = rel_path
                        else:
                            rule_tuple = tuple(parts[2:])
                            if rule_tuple not in classes[flag]['rules']:
                                classes[flag]['rules'].append(rule_tuple)
                    else:
                        raise ValueError(f"Unsupported or unrecognized Hunspell directive in {rel_path}:{line_num}: '{line}'")

        # Determine FLAG mode
        flag_mode = 'char'
        if 'FLAG' in single_directives:
            flag_line_parts = single_directives['FLAG'][0].split()
            if len(flag_line_parts) >= 2:
                flag_mode = flag_line_parts[1]

        # Format merged .aff content
        merged_aff_lines = []

        # 1. Scalar directives
        for key in ('SET', 'FLAG', 'TRY', 'WORDCHARS', 'IGNORE', 'COMPOUNDFLAG', 'KEEPCASE', 'NEEDAFFIX', 'FORBIDDENWORD', 'NOSUGGEST', 'CIRCUMFIX', 'ONLYINCOMPOUND', 'COMPOUNDMIN', 'COMPOUNDWORDMAX'):
            if key in single_directives:
                merged_aff_lines.append(single_directives[key][0])

        merged_aff_lines.append("")

        # 2. Counted block directives
        if counted_directives['AF']:
            merged_aff_lines.append(f"AF {len(counted_directives['AF'])}")
            for entry in counted_directives['AF']:
                merged_aff_lines.append(f"AF {entry}")
            merged_aff_lines.append("")

        for key in ('BREAK', 'MAP', 'REP', 'ICONV', 'OCONV', 'COMPOUNDRULE', 'CHECKCOMPOUNDPATTERN'):
            entries = sorted(list(counted_directives[key])) if key != 'AF' else counted_directives['AF']
            if entries:
                merged_aff_lines.append(f"{key} {len(entries)}")
                for e in entries:
                    merged_aff_lines.append(e)
                merged_aff_lines.append("")

        # 3. PFX classes
        for flag in sorted(pfx_classes.keys()):
            data = pfx_classes[flag]
            rules = data['rules']
            comb = data['comb'] if data['comb'] is not None else 'Y'
            if rules:
                merged_aff_lines.append(f"PFX {flag} {comb} {len(rules)}")
                for r in rules:
                    merged_aff_lines.append(f"PFX {flag} " + " ".join(r))
                merged_aff_lines.append("")

        # 4. SFX classes
        for flag in sorted(sfx_classes.keys()):
            data = sfx_classes[flag]
            rules = data['rules']
            comb = data['comb'] if data['comb'] is not None else 'Y'
            if rules:
                merged_aff_lines.append(f"SFX {flag} {comb} {len(rules)}")
                for r in rules:
                    merged_aff_lines.append(f"SFX {flag} " + " ".join(r))
                merged_aff_lines.append("")

        # Collect defined flags across PFX, SFX, and scalar flag directives
        defined_flags = set(pfx_classes.keys()) | set(sfx_classes.keys())
        for key in ('COMPOUNDFLAG', 'KEEPCASE', 'NEEDAFFIX', 'FORBIDDENWORD', 'NOSUGGEST', 'CIRCUMFIX'):
            if key in single_directives:
                parts = single_directives[key][0].split()
                if len(parts) >= 2:
                    defined_flags.add(parts[1])

        return "\n".join(merged_aff_lines), defined_flags, flag_mode

    def merge_dic(self, defined_flags, flag_mode='char'):
        """Structurally merge all .dic files without combining differing flag sets."""
        print("Merging .dic files...")

        dic_entries = set()

        for dic_path in self.dic_files:
            rel_path = os.path.relpath(dic_path, self.work_dir)
            encoding = detect_file_encoding(dic_path)

            with open(dic_path, 'r', encoding=encoding) as f:
                first_line = True
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if first_line and line.isdigit():
                        first_line = False
                        continue
                    first_line = False

                    dic_entries.add(line)

        cs_entries = sorted([e for e in dic_entries if e.startswith('+cs=')])
        word_entries = sorted([e for e in dic_entries if not e.startswith('+cs=')])

        final_entries = cs_entries + word_entries

        # Cross-validate flags
        used_flags = set()
        for e in final_entries:
            if '/' in e and not e.startswith('+cs='):
                word, flags_str = e.split('/', 1)
                parsed = parse_dic_flags(flags_str, flag_mode)
                used_flags.update(parsed)

        missing_flags = used_flags - defined_flags
        if missing_flags:
            print(f"Notice: Flags used in .dic but not defined as PFX/SFX or flag directive in .aff: {sorted(missing_flags)}")

        merged_dic_lines = [str(len(final_entries))] + final_entries
        return "\n".join(merged_dic_lines), len(final_entries)

def verify_with_hunspell(aff_path, dic_path):
    """Run real Hunspell validation on the generated dictionary files."""
    dict_base = aff_path[:-4] if aff_path.endswith('.aff') else aff_path
    cmd = ['hunspell', '-d', dict_base, '-l']
    test_words = "абзац\nнайсправжнісінький\nнеіснуючеслово999xyz\n"

    try:
        p = subprocess.run(cmd, input=test_words, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print("Warning: Hunspell binary is not installed on this system. Skipping integration test.")
        return False

    if p.returncode != 0 or "error" in p.stderr.lower():
        raise RuntimeError(f"Hunspell validation failed!\nReturn code: {p.returncode}\nStderr: {p.stderr}")

    misspelled = [w.strip() for w in p.stdout.splitlines() if w.strip()]
    if "неіснуючеслово999xyz" not in misspelled or "абзац" in misspelled:
        raise RuntimeError(f"Hunspell spellchecking validation failed!\nMisspelled output: {misspelled}")

    print("Hunspell dictionary validation passed successfully!")
    return True

def main():
    parser = argparse.ArgumentParser(description="Merge Ukrainian Hunspell dictionaries.")
    parser.add_argument("--zip", default="Ukrainian Dictionary.zip", help="Path to input zip file")
    parser.add_argument("--out-dir", default=".", help="Directory to output merged files")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        merger = HunspellMerger(tmp_dir)
        merger.unpack_sources(args.zip)

        aff_content, defined_flags, flag_mode = merger.merge_aff()
        dic_content, word_count = merger.merge_dic(defined_flags, flag_mode)

        out_aff = os.path.abspath(os.path.join(args.out_dir, "Dictionary.aff"))
        out_dic = os.path.abspath(os.path.join(args.out_dir, "Ukrainian Dictionary.dic"))

        # Write to temporary files first
        tmp_aff = out_aff + ".tmp"
        tmp_dic = out_dic + ".tmp"

        with open(tmp_aff, 'w', encoding='utf-8') as f:
            f.write(aff_content)

        with open(tmp_dic, 'w', encoding='utf-8') as f:
            f.write(dic_content)

        # Perform Hunspell verification on temporary files before replacing
        verify_with_hunspell(tmp_aff, tmp_dic)

        # Atomic replacement for both files together
        os.replace(tmp_aff, out_aff)
        os.replace(tmp_dic, out_dic)

        print(f"Successfully generated:\n  {out_aff}\n  {out_dic} (Total entries: {word_count})")

if __name__ == "__main__":
    main()
