#!/bin/bash
set -e

# 启动 milvus-lite（作为后台进程，失败不影响主服务）
echo "Attempting to start milvus-lite..."
python -c "
import sys
try:
    from milvus_lite.server import start_server
    import threading
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    print('milvus-lite started in background thread')
    import time; time.sleep(2)
except ImportError:
    print('milvus-lite not available, skipping vector DB')
except Exception as e:
    print(f'milvus-lite start failed: {e}')
" || echo "milvus-lite init failed, continuing..."

# 启动 FastAPI
echo "Starting FastAPI server..."
cd /app/backend
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
