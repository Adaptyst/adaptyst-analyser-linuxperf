# SPDX-FileCopyrightText: 2026 CERN
# SPDX-License-Identifier: GPL-3.0-or-later

import sys
import os
from pathlib import Path

repository_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(repository_root / 'python'))
sys.path.insert(0, str(Path(os.environ['ADAPTYST_ANALYSER_PATH']) / 'src'))

project = 'adaptyst-analyser-linuxperf'
extensions = ['sphinx.ext.autodoc']
templates_path = []
exclude_patterns = ['_build']

html_theme = 'alabaster'
