"""
VN Finance Digest API
Một API nhỏ chạy trên Render.com, được trang web (frontend) gọi vào
mỗi khi người dùng bấm nút "Lấy tin mới nhất".

Endpoint chính: GET /api/digest
Trả về JSON: { "html": "<...bản tóm tắt HTML...>", "generated_at": "...", "total_news": 135 }
"""

import os
import time
from datetime import datetime, timedelta, timezone

import feedparser
from flask import Flask, jsonify
from flask_cors import CORS
from openai import OpenAI

app = Flask(__name__)
# Cho phép trang web (chạy ở domain khác) gọi vào API này
CORS(app)

RSS_FEEDS = [
    ("CafeF - Chứng khoán", "https://cafef.vn/thi-truong-chung-khoan.rss"),
    ("CafeF - Tài chính ngân hàng", "https://cafef.vn/tai-chinh-ngan-hang.rss"),
    ("CafeF - Doanh nghiệp", "https://cafef.vn/doanh-nghiep.rss"),
    ("CafeF - Vĩ mô đầu tư", "https://cafef.vn/vi-mo-dau-tu.rss"),
    ("VnExpress - Kinh doanh", "https://vnexpress.net/rss/kinh-doanh.rss"),
    ("Vietstock - Chứng khoán", "https://vietstock.vn/830/chung-khoan.rss"),
]

HOURS_LOOKBACK = 24

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# Cache đơn giản trong bộ nhớ: tránh gọi OpenAI liên tục nếu nhiều người bấm
# nút gần nhau. Cache tồn tại trong CACHE_TTL_SECONDS.
_cache = {"data": None, "timestamp": 0}
CACHE_TTL_SECONDS = 15 * 60  # 15 phút


def fetch_recent_news():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_LOOKBACK)
    all_items = []

    for source_name, url in RSS_FEEDS:
        try:
            parsed = feedparser.parse(url, request_headers=HEADERS)
            for entry in parsed.entries:
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if published is None:
                    continue
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
                all_items.append({
                    "source": source_name,
                    "title": entry.get("title", "(Không có tiêu đề)"),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary", "")[:500],
                    "published": pub_dt,
                })
        except Exception as e:
            print(f"[LỖI] {source_name}: {e}")

    all_items.sort(key=lambda x: x["published"], reverse=True)
    return all_items


def summarize_with_ai(news_items):
    if not news_items:
        return "<p>Không có tin mới nào trong 24 giờ qua.</p>"

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    items_text = "\n\n".join(
        f"[{item['source']}] {item['title']}\nLink: {item['link']}\nTóm tắt gốc: {item['summary']}"
        for item in news_items[:60]
    )

    system_prompt = """Bạn là trợ lý biên tập tin tài chính cho một chuyên viên đầu tư tại SCIC (Tổng Công ty Đầu tư và Kinh doanh vốn Nhà nước).
Nhiệm vụ: đọc danh sách tin tức thô bên dưới, sau đó tạo bản tổng hợp tin tài chính Việt Nam trong ngày dưới dạng HTML.

Yêu cầu:
1. Phân loại tin theo các nhóm: "Thị trường chứng khoán", "Ngân hàng - Tài chính", "Doanh nghiệp - Cổ phần hóa", "Kinh tế vĩ mô", "Khác"
2. Mỗi tin chỉ giữ lại nếu thực sự liên quan tài chính/kinh tế/doanh nghiệp
3. Viết lại tiêu đề + 1-2 câu tóm tắt ngắn gọn bằng tiếng Việt, diễn đạt lại bằng lời riêng
4. Mỗi tin có link để bấm đọc full bài (dùng <a href="..." target="_blank">)
5. Đầu bản tin có một đoạn "Điểm nhấn hôm nay" (3-5 ý quan trọng nhất, bullet point)
6. Output là HTML hoàn chỉnh (dùng <h2>, <h3>, <ul>, <li>, <a>), không cần <html>/<body> tag, không markdown code fence
7. Văn phong chuyên nghiệp, súc tích"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Danh sách tin hôm nay:\n\n{items_text}"},
        ],
        temperature=0.3,
    )

    html_content = response.choices[0].message.content.strip()
    if html_content.startswith("```"):
        html_content = html_content.strip("`").lstrip("html").strip()
    return html_content, len(news_items)


@app.route("/api/digest", methods=["GET"])
def get_digest():
    now = time.time()
    # Nếu cache còn mới (dưới 15 phút) thì trả luôn, không gọi lại OpenAI
    if _cache["data"] is not None and (now - _cache["timestamp"]) < CACHE_TTL_SECONDS:
        return jsonify(_cache["data"])

    try:
        news = fetch_recent_news()
        html_content, total = summarize_with_ai(news)
        result = {
            "html": html_content,
            "total_news": total,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "from_cache": False,
        }
        _cache["data"] = {**result, "from_cache": True}
        _cache["timestamp"] = now
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
