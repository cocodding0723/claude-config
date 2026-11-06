#!/usr/bin/env python3
"""Run local Vale rules on explicitly selected Markdown/text files."""
import argparse
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+', type=Path)
    args = parser.parse_args()
    files = [p.expanduser().resolve() for p in args.files]
    for p in files:
        if not p.is_file() or p.suffix.lower() not in {'.md', '.txt', '.mdx'}:
            parser.error(f'Expected a Markdown or text file: {p}')
    vale = shutil.which('vale')
    if not vale:
        parser.exit(2, 'Vale executable missing. Install Vale before running prose lint.\n')
    config = Path(__file__).resolve().parents[1] / 'vale.ini'
    return subprocess.run([vale, '--config', str(config), '--minAlertLevel',
                           'warning', *map(str, files)], check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())
