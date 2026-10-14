#!/usr/bin/env python3
"""TuxAmp launcher: python3 tuxamp.py [files/folders...]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tuxamp.__main__ import main  # noqa: E402

main()
