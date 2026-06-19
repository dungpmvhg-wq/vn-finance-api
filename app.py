"""
VN Finance Digest API - phiên bản async
Dùng FastAPI + httpx async để tránh bị gunicorn/uvicorn timeout khi gọi OpenAI.
"""

import os
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager

import feedparser
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Cache đơn giản trong bộ nhớ - 15 phút
_cache = {"data": None, "timestamp": 0.0}
CACHE_TTL = 15 * 60

RSS_FEEDS = [
    ("CafeF - Chứng khoán", "https://cafef.vn/thi-truong-chung-khoan.rss"),
    ("CafeF - Tài chính ngân hàng", "https://cafef.vn/tai-chinh-ngan-hang.rss"),
    ("CafeF - Doanh nghiệp", "https://cafef.vn/doanh-nghiep.rss"),
    ("VnExpress - Kinh doanh", "https://vnexpress.net/rss/kinh-doanh.rss"),
    ("Vietstock", "https://vietstock.vn/830/chung-khoan.rss"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def fetch_news():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    items = []
    for source, url in RSS_FEEDS:
        try:
            d = feedparser.parse(url, request_headers=HEADERS)
            for e in d.entries:
                pub = e.get("published_parsed") or e.get("updated_parsed")
                if not pub:
                    continue
                pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
                items.append({
                    "source": source,
                    "title": e.get("title", ""),
                    "link": e.get("link", ""),
                    "summary": e.get("summary", "")[:200],
                })
        except Exception as ex:
            print(f"RSS error {source}: {ex}")
    return items


async def summarize(items):
    if not items:
        return "<p>Không có tin mới trong 24 giờ qua.</p>", 0

    text = "\n\n".join(
        f"[{it['source']}] {it['title']}\nLink: {it['link']}\n{it['summary']}"
        for it in items[:30]
    )

    system = """Bạn là trợ lý biên tập tin tài chính cho chuyên viên đầu tư SCIC.
Tạo bản tổng hợp tin tài chính VN dạng HTML từ danh sách tin dưới đây.
Yêu cầu:
1. Phân loại: "Thị trường chứng khoán", "Ngân hàng - Tài chính", "Doanh nghiệp", "Kinh tế vĩ mô"
2. Mỗi tin: tiêu đề + 1-2 câu tóm tắt + link đọc thêm (<a href="..." target="_blank">)
3. Đầu bản tin: "Điểm nhấn hôm nay" (3-5 ý bullet)
4. Output: HTML thuần (dùng h2, h3, ul, li, a), không markdown, không code fence"""

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Tin hôm nay:\n\n{text}"},
                ],
                "temperature": 0.3,
                "max_tokens": 2000,
            },
        )
        resp.raise_for_status()
        html = resp.json()["choices"][0]["message"]["content"].strip()
        if html.startswith("```"):
            html = html.strip("`").lstrip("html").strip()
        return html, len(items)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/digest")
async def digest():
    import time
    now = time.time()
    if _cache["data"] and (now - _cache["timestamp"]) < CACHE_TTL:
        return {**_cache["data"], "from_cache": True}

    items = fetch_news()
    html, total = await summarize(items)
    result = {
        "html": html,
        "total_news": total,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "from_cache": False,
    }
    _cache["data"] = result
    _cache["timestamp"] = now
    return result

   
