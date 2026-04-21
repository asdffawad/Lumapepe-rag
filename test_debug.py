import httpx
import json

r = httpx.post('http://localhost:8000/api/v1/debug/retrieve', json={'query': '退货政策', 'top_k': 3})
data = r.json()
docs = data.get('stages', {}).get('dense', {}).get('items', [])
print('Retrieved', len(docs), 'docs')
for i, d in enumerate(docs[:3]):
    lang = d.get('lang', 'N/A')
    score = d.get('dense_score', 0)
    content_preview = d.get('content', '')[:80].replace('\n', ' ')
    print(f'{i+1}. lang={lang}, score={score:.3f}, content={content_preview}')
