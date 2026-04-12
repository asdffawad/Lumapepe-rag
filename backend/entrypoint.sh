#!/bin/bash
set -e

# 启动 milvus-lite（后台进程，不阻塞）
python -c "
import sys, time
try:
    from milvus_lite.server import start_server
    import threading
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    time.sleep(3)
    print('milvus-lite started')
except Exception as e:
    print('milvus-lite unavailable:', e)
" &

# 启动 FastAPI
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
