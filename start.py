#!/usr/bin/env python3
"""Start a configured local copy without shell-sourcing private settings."""
import os
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parent
settings=root/'private/installation.env'
if settings.exists():
    for line in settings.read_text().splitlines():
        key,sep,value=line.partition('=')
        if sep and key in ('UNSTUCKERY_TIMEZONE','UNSTUCKERY_CANVAS_ORIGIN'):
            os.environ[key]=value.strip()
subprocess.run([sys.executable,str(root/'server.py'),'--ai-review','--review-config',str(root/'private/review.env'),*sys.argv[1:]],check=True)
