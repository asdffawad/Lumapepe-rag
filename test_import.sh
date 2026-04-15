#!/bin/bash
cd /app
python -c "from scripts.ingest_data import _parse_file_safe; print('OK'); import sys; print('path:', sys.path[:5])"
