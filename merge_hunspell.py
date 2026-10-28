#!/usr/bin/env python3
"""
merge_hunspell.py - Архітектурно стійкий зливач словників Hunspell (AFF/DIC) для української мови.
"""

import sys
import os
import re
import glob
import zipfile
import tempfile
import argparse
import subprocess
import codecs
import shutil
from typing import Dict, List, Set, Tuple, Optional, FrozenSet

MAX_UNCOMPRESSED_SIZE = 500 * 1024 * 1024  # 500 MB
MAX_EXTRACT_FILES = 2000

ENC_ALIASES = {
    'microsoft-cp1251': 'cp1251',
    'microsoft-cp1252': 'cp1252',
    'koi8-u': 'koi8_u',
    'koi8-r': 'koi8_r',
    'utf-8': 'utf-8-sig',
    'utf8': 'utf-8-sig'
}

MORPH_SPLIT = re.compile(r'\s+(?=[A-Za-z]{2}:)')
ENTRY_RE = re.compile(r'^(?P<word>(?:[^/\\]|\\.)+)(?:/(?P<flags>\S*))?$')

def is_safe_zip_path(target_dir: str, member_path: str) -> bool:
    """Захист від вразливості Zip Slip."""
    abs_target = os.path.abspath(target_dir)
    abs_member = os.path.abspath(os.path.join(target_dir, member_path))
    return os.path.commonpath([abs_target, abs_member]) == abs_target

def is_junk(path: str) -> bool:
    """Виявляє сміттєві файли macOS та метадані."""
    parts = path.split(os.sep)
    return '__MACOSX' in parts or os.path.basename(path).startswith('._')

def safe_extract_archive(zip_path: str, extract_to: str) -> None:
    """Безпечне розпакування з обмеженням за обсягом та захистом від Zip Slip."""
    total_size = 0
    file_count = 0

    with zipfile.ZipFile(zip_path, 'r') as z:
        for member in z.infolist():
            if is_junk(member.filename):
                continue
            file_count += 1
            if file_count > MAX_EXTRACT_FILES:
                raise ValueError(f"Перевищено безпечний ліміт кількості файлів в архіві ({MAX_EXTRACT_FILES})")

            total_size += member.file_size
            if total_size > MAX_UNCOMPRESSED_SIZE:
                raise ValueError(f"Перевищено максимальний розмір розпакованих даних (> {MAX_UNCOMPRESSED_SIZE // (1024*1024)} MB)")

            if not is_safe_zip_path(extract_to, member.filename):
                raise ValueError(f"Виявлено небезпечний шлях Zip Slip: {member.filename}")

        z.extractall(extract_to)

def normalize_codec_name(enc: str) -> str:
    """Приведення назв кодувань Hunspell до стандартів Python codecs."""
    e = enc.strip().lower().replace('_', '-')
    mapped = ENC_ALIASES.get(e, e.replace('-', '_'))
    try:
        codecs.lookup(mapped)
        return mapped
    except LookupError:
        return 'utf-8-sig'

def detect_file_encoding(filepath: str) -> str:
    """Визначення кодування файлу через аналіз BOM або директиви SET у перших рядках."""
    with open(filepath, 'rb') as f:
        head = f.read(4096)
    if head.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    for line in head.splitlines():
        line_clean = line.strip()
        if line_clean.startswith(b'SET '):
            parts = line_clean.split(None, 1)
            if len(parts) >= 2:
                enc = parts[1].decode('ascii', errors='ignore').strip()
                return normalize_codec_name(enc)
    return 'utf-8-sig'

class AffixModel:
    """Модель одного .aff файлу: парсинг правил, прапорців та AF-таблиць."""
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.encoding = detect_file_encoding(filepath)
        self.flag_mode = 'char'
        self.af_list: List[str] = []
        self.directives: Dict[str, str] = {}
        self.counted_blocks: Dict[str, Dict[str, None]] = {
            'BREAK': {}, 'MAP': {}, 'REP': {}, 'ICONV': {}, 'OCONV': {},
            'COMPOUNDRULE': {}, 'CHECKCOMPOUNDPATTERN': {}
        }
        self.pfx: Dict[str, Dict] = {}
        self.sfx: Dict[str, Dict] = {}
        self._parse()

    def _parse(self):
        with open(self.filepath, 'r', encoding=self.encoding, errors='replace') as f:
            for line_no, raw_line in enumerate(f, 1):
                clean_line = raw_line.split('#', 1)[0].strip()
                if not clean_line:
                    continue

                parts = clean_line.split()
                directive = parts[0]

                if directive == 'SET':
                    continue

                elif directive == 'FLAG':
                    if len(parts) >= 2:
                        self.flag_mode = parts[1]
                    self.directives['FLAG'] = clean_line

                elif directive == 'AF':
                    if len(parts) == 2 and parts[1].isdigit():
                        continue
                    self.af_list.append(" ".join(parts[1:]))

                elif directive in self.counted_blocks:
                    if len(parts) == 2 and parts[1].isdigit():
                        continue
                    self.counted_blocks[directive][clean_line] = None

                elif directive in ('PFX', 'SFX'):
                    classes = self.pfx if directive == 'PFX' else self.sfx
                    flag = parts[1]
                    if flag not in classes:
                        classes[flag] = {'comb': 'Y', 'expected': None, 'rules': []}

                    if len(parts) == 4 and parts[2] in ('Y', 'N') and parts[3].isdigit():
                        classes[flag]['comb'] = parts[2]
                        classes[flag]['expected'] = int(parts[3])
                    else:
                        rule_tuple = tuple(parts[2:])
                        if rule_tuple not in classes[flag]['rules']:
                            classes[flag]['rules'].append(rule_tuple)

                else:
                    if len(parts) == 2 and parts[1].isdigit():
                        continue
                    if directive not in self.directives:
                        self.directives[directive] = clean_line

    def decode_dic_flags(self, flags_raw: str) -> List[str]:
        """Декодування прапорців запису .dic з урахуванням поточної таблиці AF та режиму FLAG."""
        if not flags_raw:
            return []

        if self.af_list:
            if flags_raw.isdigit():
                idx = int(flags_raw) - 1
                if 0 <= idx < len(self.af_list):
                    raw_expanded = self.af_list[idx]
                    return self._split_flags(raw_expanded, self.flag_mode)
                else:
                    raise ValueError(f"Помилка в {self.filepath}: посилання на неіснуючий AF-псевдонім /{flags_raw}")

        return self._split_flags(flags_raw, self.flag_mode)

    @staticmethod
    def _split_flags(flags_str: str, mode: str) -> List[str]:
        if not flags_str:
            return []
        if mode == 'long':
            return [flags_str[i:i+2] for i in range(0, len(flags_str), 2)]
        elif mode == 'num':
            return [f.strip() for f in flags_str.split(',') if f.strip()]
        return list(flags_str)


class DictionaryEntry:
    """Представлення слова з його розпарсеними прапорцями та морфологією."""
    def __init__(self, word: str, flags: FrozenSet[str], morph: str):
        self.word = word
        self.flags = flags
        self.morph = morph

    @classmethod
    def parse_line(cls, line: str, aff_model: AffixModel) -> 'DictionaryEntry':
        line = line.strip()
        parts = MORPH_SPLIT.split(line, maxsplit=1)
        morph = parts[1].strip() if len(parts) > 1 else ""
        word_part = parts[0]

        m = ENTRY_RE.match(word_part)
        if m:
            word = m.group('word')
            raw_flags = m.group('flags')
            if raw_flags and not word.startswith('+cs='):
                flags = aff_model.decode_dic_flags(raw_flags)
            else:
                flags = []
        else:
            word = word_part
            flags = []

        return cls(word=word, flags=frozenset(flags), morph=morph)


class UniversalHunspellMerger:
    def __init__(self, work_dir: str, union_classes: bool = False, strict: bool = False):
        self.work_dir = work_dir
        self.union_classes = union_classes
        self.strict = strict
        self.aff_models: List[AffixModel] = []
        self.dic_paths: List[str] = []

    def collect_sources(self):
        """Знаходить усі .aff та споріднені .dic файли."""
        aff_files = glob.glob(os.path.join(self.work_dir, '**/*.aff'), recursive=True)
        for aff in sorted(aff_files):
            if is_junk(aff):
                continue
            base = os.path.basename(aff)
            if base.startswith(('hyph_', 'th_')):
                continue
            dic = os.path.splitext(aff)[0] + '.dic'
            if os.path.exists(dic) and not is_junk(dic):
                self.aff_models.append(AffixModel(aff))
                self.dic_paths.append(dic)
            else:
                print(f"Попередження: Для {aff} не знайдено парного .dic, файл пропускається.")

        print(f"Завантажено {len(self.aff_models)} пар AFF/DIC для об'єднання.")
        if not self.aff_models:
            raise FileNotFoundError("В архіві не знайдено жодної валідної пари .aff/.dic!")

    def merge(self) -> Tuple[str, str, int]:
        flag_modes = set(m.flag_mode for m in self.aff_models)
        if len(flag_modes) > 1:
            raise ValueError(f"Конфлікт режимів FLAG між джерелами: {flag_modes}")

        target_flag_mode = list(flag_modes)[0] if flag_modes else 'char'

        merged_directives = {}
        merged_counted = {k: {} for k in self.aff_models[0].counted_blocks}

        UNION_DIRECTIVES = {'TRY', 'WORDCHARS'}

        for model in self.aff_models:
            rel_path = os.path.relpath(model.filepath, self.work_dir)
            for k, val in model.directives.items():
                if k in ('SET', 'FLAG'):
                    continue
                if k in UNION_DIRECTIVES:
                    prev_val = merged_directives.get(k, k + " ").split(None, 1)
                    prev_chars = prev_val[1] if len(prev_val) > 1 else ""
                    curr_chars = val.split(None, 1)[1] if len(val.split(None, 1)) > 1 else ""
                    merged_chars = "".join(dict.fromkeys(prev_chars + curr_chars))
                    merged_directives[k] = f"{k} {merged_chars}"
                elif k in merged_directives and merged_directives[k] != val:
                    if self.strict:
                        raise ValueError(f"Конфлікт директиви '{k}' між джерелами:\n  1: {merged_directives[k]}\n  2: {val}")
                    else:
                        print(f"Попередження: Незбіг директиви '{k}' ({rel_path}). Збережено перше значення.")
                else:
                    merged_directives[k] = val

            for k, block_dict in model.counted_blocks.items():
                for line in block_dict.keys():
                    merged_counted[k][line] = None

        # PFX / SFX blocks
        merged_pfx = {}
        merged_sfx = {}
        origin_pfx = {}
        origin_sfx = {}

        for model in self.aff_models:
            rel_path = os.path.relpath(model.filepath, self.work_dir)
            for kind, src_classes, target_classes, origin_dict in (
                ('PFX', model.pfx, merged_pfx, origin_pfx),
                ('SFX', model.sfx, merged_sfx, origin_sfx)
            ):
                for flag, data in src_classes.items():
                    if data['expected'] is not None and len(data['rules']) != data['expected']:
                        raise ValueError(f"Помилка цілісності у {model.filepath}: {kind} {flag} "
                                         f"декларує {data['expected']} правил, але знайдено {len(data['rules'])}")

                    if flag not in target_classes:
                        target_classes[flag] = {'comb': data['comb'], 'rules': list(data['rules'])}
                        origin_dict[flag] = rel_path
                    else:
                        if target_classes[flag]['comb'] != data['comb']:
                            raise ValueError(f"Конфлікт комбінування прапорця {kind} {flag}: "
                                             f"{target_classes[flag]['comb']} != {data['comb']} ({rel_path})")

                        existing_rules = set(target_classes[flag]['rules'])
                        new_rules = set(data['rules'])
                        if existing_rules != new_rules and not self.union_classes:
                            raise ValueError(
                                f"Клас {kind} '{flag}' визначено по-різному у джерелах:\n"
                                f"  Перше джерело ({origin_dict[flag]}): {len(existing_rules)} правил\n"
                                f"  Поточне джерело ({rel_path}): {len(new_rules)} правил"
                            )

                        for r in data['rules']:
                            if r not in target_classes[flag]['rules']:
                                target_classes[flag]['rules'].append(r)

        # Merge .dic entries: EXACT deduplication only. Distinct flag sets for the same word remain distinct records.
        # Key: (word, frozenset(flags), morph)
        unique_entries: Set[Tuple[str, FrozenSet[str], str]] = set()

        for model, dic_path in zip(self.aff_models, self.dic_paths):
            enc = detect_file_encoding(dic_path)
            with open(dic_path, 'r', encoding=enc, errors='replace') as f:
                first = True
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if first and line.isdigit():
                        first = False
                        continue
                    first = False

                    entry = DictionaryEntry.parse_line(line, model)
                    unique_entries.add((entry.word, entry.flags, entry.morph))

        # Build AF table for unique flag sets
        af_table: List[str] = []
        flag_set_to_af_idx: Dict[str, int] = {}

        for word, flags, morph in unique_entries:
            if flags:
                canonical_str = "".join(sorted(flags))
                if canonical_str not in flag_set_to_af_idx:
                    af_table.append(canonical_str)
                    flag_set_to_af_idx[canonical_str] = len(af_table)

        # Generate AFF content
        aff_out = []
        aff_out.append("SET UTF-8")
        if target_flag_mode != 'char':
            aff_out.append(f"FLAG {target_flag_mode}")

        for k in ('LANG', 'KEY', 'TRY', 'WORDCHARS', 'IGNORE'):
            if k in merged_directives:
                aff_out.append(merged_directives[k])

        for k, v in sorted(merged_directives.items()):
            if k not in ('LANG', 'KEY', 'TRY', 'WORDCHARS', 'IGNORE'):
                aff_out.append(v)
        aff_out.append("")

        if af_table:
            aff_out.append(f"AF {len(af_table)}")
            for af_entry in af_table:
                aff_out.append(f"AF {af_entry}")
            aff_out.append("")

        for k, block_dict in merged_counted.items():
            lines = list(block_dict.keys())
            if lines:
                aff_out.append(f"{k} {len(lines)}")
                aff_out.extend(lines)
                aff_out.append("")

        for kind, classes in (('PFX', merged_pfx), ('SFX', merged_sfx)):
            for flag in sorted(classes.keys()):
                rules = classes[flag]['rules']
                comb = classes[flag]['comb']
                aff_out.append(f"{kind} {flag} {comb} {len(rules)}")
                for r in rules:
                    aff_out.append(f"{kind} {flag} " + " ".join(r))
                aff_out.append("")

        # Generate DIC content
        dic_lines = []
        total_words = len(unique_entries)
        dic_lines.append(str(total_words))

        for word, flags, morph in sorted(unique_entries, key=lambda x: (x[0], x[2])):
            line_parts = [word]
            if flags:
                canonical_str = "".join(sorted(flags))
                af_idx = flag_set_to_af_idx[canonical_str]
                line_parts[0] = f"{word}/{af_idx}"
            if morph:
                line_parts.append(morph)
            dic_lines.append(" ".join(line_parts))

        return "\n".join(aff_out), "\n".join(dic_lines), total_words


def verify_with_hunspell(aff_path: str, good_words: List[str], bad_words: List[str]) -> bool:
    """Перевірка створеного словника через утиліту Hunspell."""
    base_path = os.path.splitext(aff_path)[0]
    cmd = ['hunspell', '-d', base_path, '-i', 'UTF-8', '-l']
    payload = "\n".join(good_words + bad_words) + "\n"

    try:
        p = subprocess.run(cmd, input=payload, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        print("Попередження: Hunspell не встановлено в системі.")
        return False

    if p.returncode != 0 or "error" in p.stderr.lower():
        raise RuntimeError(f"Помилка ініціалізації/парсингу Hunspell ({p.returncode}):\n{p.stderr}")

    misspelled = set(p.stdout.split())
    falsely_rejected = [w for w in good_words if w in misspelled]
    if falsely_rejected:
        raise AssertionError(f"Hunspell відхилив легітимні слова: {falsely_rejected}")

    missed_invalid = [w for w in bad_words if w not in misspelled]
    if missed_invalid:
        raise AssertionError(f"Hunspell пропустив некоректні слова як правильні: {missed_invalid}")

    print("✔ Комплексна перевірка Hunspell успішно пройдена!")
    return True


def main():
    parser = argparse.ArgumentParser(description="Архітектурне об'єднання українських словників Hunspell.")
    parser.add_argument("--zip", default="Ukrainian Dictionary.zip", help="Шлях до вхідного ZIP/OXT архіву")
    parser.add_argument("--out-dir", default=".", help="Каталог для збереження результату")
    parser.add_argument("--name", default="uk_UA", help="Базова назва вихідних файлів (наприклад uk_UA або Dictionary)")
    parser.add_argument("--good", nargs="*", default=["абзац", "ґанок", "єнот", "ідея", "їжак", "м'яч", "найкращий"], help="Правильні тестові слова")
    parser.add_argument("--bad", nargs="*", default=["неіснуючеслово999xyz", "самолєт"], help="Некоректні тестові слова")
    parser.add_argument("--union-classes", action="store_true", help="Дозволити об'єднання класів афіксів з різними правилами")
    parser.add_argument("--strict", action="store_true", help="Строгий режим збоїв на конфліктах")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_extract_dir, tempfile.TemporaryDirectory() as stage_dir:
        print(f"Розпакування {args.zip}...")
        safe_extract_archive(args.zip, temp_extract_dir)

        for oxt in glob.glob(os.path.join(temp_extract_dir, '**/*.oxt'), recursive=True):
            if not is_junk(oxt):
                safe_extract_archive(oxt, oxt + "_unpacked")

        merger = UniversalHunspellMerger(temp_extract_dir, union_classes=args.union_classes, strict=args.strict)
        merger.collect_sources()

        aff_content, dic_content, word_count = merger.merge()

        st_aff = os.path.join(stage_dir, f"{args.name}.aff")
        st_dic = os.path.join(stage_dir, f"{args.name}.dic")

        with open(st_aff, 'w', encoding='utf-8', newline='\n') as f:
            f.write(aff_content + "\n")
        with open(st_dic, 'w', encoding='utf-8', newline='\n') as f:
            f.write(dic_content + "\n")

        # Perform Hunspell verification on staged pair and check status
        verified = verify_with_hunspell(st_aff, args.good, args.bad)
        if not verified and args.strict:
            sys.exit("Перевірку Hunspell не виконано або вона завершилася помилкою (--strict).")

        # Copy to output directory safely
        out_aff = os.path.abspath(os.path.join(args.out_dir, f"{args.name}.aff"))
        out_dic = os.path.abspath(os.path.join(args.out_dir, f"{args.name}.dic"))

        for src, dst in ((st_aff, out_aff), (st_dic, out_dic)):
            tmp_dst = dst + ".tmp"
            shutil.copyfile(src, tmp_dst)
            os.replace(tmp_dst, dst)

        # Also maintain output named 'Dictionary.aff' and 'Ukrainian Dictionary.dic'
        if args.name == "uk_UA" or args.name == "Dictionary":
            alt_aff = os.path.abspath(os.path.join(args.out_dir, "Dictionary.aff"))
            alt_dic = os.path.abspath(os.path.join(args.out_dir, "Ukrainian Dictionary.dic"))
            shutil.copyfile(out_aff, alt_aff)
            shutil.copyfile(out_dic, alt_dic)

        print(f"\nУспішно створено та верифіковано словник:")
        print(f"  AFF: {out_aff}")
        print(f"  DIC: {out_dic} (Загалом унікальних лексичних записів: {word_count})")


if __name__ == "__main__":
    main()
