#!/usr/bin/env python3
"""
merge_hunspell.py - Structural Hunspell AFF and DIC Merger for Ukrainian Dictionary
"""

import sys
import os
import glob
import zipfile
import tempfile
import argparse
import collections

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

class HunspellMerger:
    def __init__(self, work_dir):
        self.work_dir = work_dir
        self.aff_files = []
        self.dic_files = []

    def unpack_sources(self, zip_path):
        """Unpack ZIP archive and any embedded .oxt files."""
        print(f"Unpacking {zip_path} into {self.work_dir}...")
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extractall(self.work_dir)

        oxt_files = glob.glob(os.path.join(self.work_dir, '**/*.oxt'), recursive=True)
        for oxt in oxt_files:
            oxt_out = oxt + '_unpacked'
            with zipfile.ZipFile(oxt, 'r') as z:
                z.extractall(oxt_out)

        all_aff = glob.glob(os.path.join(self.work_dir, '**/*.aff'), recursive=True)
        all_dic = glob.glob(os.path.join(self.work_dir, '**/*.dic'), recursive=True)

        for dic in sorted(all_dic):
            if os.path.basename(dic).startswith('hyph_'):
                continue
            self.dic_files.append(dic)

        for aff in sorted(all_aff):
            self.aff_files.append(aff)

        print(f"Found {len(self.aff_files)} .aff files and {len(self.dic_files)} spellcheck .dic files.")

    def merge_aff(self):
        """Structurally merge all .aff files."""
        print("Merging .aff files...")

        single_directives = {}  # key -> (val, source_file)
        counted_directives = {
            'BREAK': set(),
            'MAP': set(),
            'REP': set(),
            'ICONV': set(),
            'OCONV': set()
        }

        pfx_classes = collections.defaultdict(lambda: {'comb': 'Y', 'rules': []})
        sfx_classes = collections.defaultdict(lambda: {'comb': 'Y', 'rules': []})

        # Supported single-value directives
        supported_single = {
            'SET', 'FLAG', 'TRY', 'WORDCHARS', 'IGNORE', 'AF', 'COMPOUNDFLAG',
            'COMPOUNDRULE', 'NOSUGGEST', 'KEEPCASE', 'CIRCUMFIX', 'NEEDAFFIX', 'FORBIDDENWORD'
        }

        for aff_path in self.aff_files:
            rel_path = os.path.relpath(aff_path, self.work_dir)
            with open(aff_path, 'r', encoding='utf-8', errors='ignore') as f:
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
                                    f"Conflict in directive {key}!\n"
                                    f"  Source 1 ({existing_src}): {existing_val}\n"
                                    f"  Source 2 ({rel_path}): {val}"
                                )
                        else:
                            single_directives[key] = (val, rel_path)

                    elif key in ('BREAK', 'MAP', 'REP', 'ICONV', 'OCONV'):
                        if len(parts) == 2 and parts[1].isdigit():
                            continue
                        counted_directives[key].add(line)

                    elif key in ('PFX', 'SFX'):
                        if len(parts) < 2:
                            continue
                        flag = parts[1]
                        classes = pfx_classes if key == 'PFX' else sfx_classes

                        if len(parts) == 4 and parts[2] in ('Y', 'N') and parts[3].isdigit():
                            classes[flag]['comb'] = parts[2]
                        else:
                            rule_tuple = tuple(parts[2:])
                            if rule_tuple not in classes[flag]['rules']:
                                classes[flag]['rules'].append(rule_tuple)
                    else:
                        raise ValueError(f"Unsupported or unrecognized directive in {rel_path}:{line_num}: '{line}'")

        # Determine FLAG mode
        flag_mode = 'char'
        if 'FLAG' in single_directives:
            flag_line_parts = single_directives['FLAG'][0].split()
            if len(flag_line_parts) >= 2:
                flag_mode = flag_line_parts[1]

        merged_aff_lines = []

        for key in ('SET', 'FLAG', 'TRY', 'WORDCHARS', 'IGNORE', 'AF', 'COMPOUNDFLAG', 'COMPOUNDRULE', 'NOSUGGEST', 'KEEPCASE', 'CIRCUMFIX', 'NEEDAFFIX', 'FORBIDDENWORD'):
            if key in single_directives:
                merged_aff_lines.append(single_directives[key][0])

        merged_aff_lines.append("")

        for key in ('BREAK', 'MAP', 'REP', 'ICONV', 'OCONV'):
            entries = sorted(list(counted_directives[key]))
            if entries:
                merged_aff_lines.append(f"{key} {len(entries)}")
                for e in entries:
                    merged_aff_lines.append(e)
                merged_aff_lines.append("")

        for flag in sorted(pfx_classes.keys()):
            data = pfx_classes[flag]
            rules = data['rules']
            if rules:
                merged_aff_lines.append(f"PFX {flag} {data['comb']} {len(rules)}")
                for r in rules:
                    merged_aff_lines.append(f"PFX {flag} " + " ".join(r))
                merged_aff_lines.append("")

        for flag in sorted(sfx_classes.keys()):
            data = sfx_classes[flag]
            rules = data['rules']
            if rules:
                merged_aff_lines.append(f"SFX {flag} {data['comb']} {len(rules)}")
                for r in rules:
                    merged_aff_lines.append(f"SFX {flag} " + " ".join(r))
                merged_aff_lines.append("")

        defined_flags = set(pfx_classes.keys()) | set(sfx_classes.keys())
        return "\n".join(merged_aff_lines), defined_flags, flag_mode

    def merge_dic(self, defined_flags, flag_mode='char'):
        """Structurally merge all .dic files without merging different flag sets."""
        print("Merging .dic files...")

        dic_entries = set()

        for dic_path in self.dic_files:
            rel_path = os.path.relpath(dic_path, self.work_dir)
            with open(dic_path, 'r', encoding='utf-8', errors='ignore') as f:
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

        # Cross validate flags
        used_flags = set()
        for e in final_entries:
            if '/' in e and not e.startswith('+cs='):
                word, flags_str = e.split('/', 1)
                parsed = parse_dic_flags(flags_str, flag_mode)
                used_flags.update(parsed)

        missing_flags = used_flags - defined_flags
        if missing_flags:
            print(f"Notice: Flags used in .dic but not defined as PFX/SFX class in .aff: {sorted(missing_flags)}")

        merged_dic_lines = [str(len(final_entries))] + final_entries
        return "\n".join(merged_dic_lines), len(final_entries)

def main():
    parser = argparse.ArgumentParser(description="Merge Ukrainian Hunspell dictionaries.")
    parser.add_argument("--zip", default="Ukrainian Dictionary.zip", help="Path to input zip file")
    parser.add_argument("--out-dir", default=".", help="Directory to output merged files")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp_dir:
        merger = HunspellMerger(tmp_dir)
        merger.unpack_sources(args.zip)

        aff_content, defined_flags, flag_mode = merger.merge_aff()
        dic_content, word_count = merger.merge_dic(defined_flags, flag_mode)

        out_aff = os.path.join(args.out_dir, "Dictionary.aff")
        out_dic = os.path.join(args.out_dir, "Ukrainian Dictionary.dic")

        tmp_aff = out_aff + ".tmp"
        tmp_dic = out_dic + ".tmp"

        with open(tmp_aff, 'w', encoding='utf-8') as f:
            f.write(aff_content)

        with open(tmp_dic, 'w', encoding='utf-8') as f:
            f.write(dic_content)

        os.replace(tmp_aff, out_aff)
        os.replace(tmp_dic, out_dic)

        print(f"Successfully generated:\n  {out_aff}\n  {out_dic} (Total entries: {word_count})")

if __name__ == "__main__":
    main()
