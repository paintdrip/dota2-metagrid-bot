# -*- coding: utf-8 -*-
"""conftest: добавляем корень репозитория в sys.path для импорта metagrid."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
