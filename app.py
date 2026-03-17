# -*- coding: utf-8 -*-
"""
B 站 UP 主全部视频字幕爬取 - 本地网页工具
访问 http://127.0.0.1:5000 输入 UP 主 UID 即可爬取并下载字幕。
"""
import json
import zipfile
import io
import time
import threading
import os
from flask import Flask, request, render_template, jsonify, send_file, Response
from bilibili_api import (
    collect_all_videos,
    get_all_subtitles_for_video,
)

app = Flask(__name__)
app.secret_key = "bilibili-subtitle-crawler-secret"

# 默认保存目录
DEFAULT_SAVE_DIR = "/home/user/图片/bilibili_subtitles"
os.makedirs(DEFAULT_SAVE_DIR, exist_ok=True)

# 存储每个客户端的进度数据
progress_store = {}


def safe_filename(s):
    """安全的文件名"""
    return "".join(c for c in s if c.isalnum() or c in " _-（）()【】[]")


def gen_session_id():
    import uuid
    return str(uuid.uuid4())


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/crawl", methods=["POST"])
def crawl():
    data = request.get_json() or {}
    mid = (data.get("mid") or "").strip()
    if not mid:
        return jsonify({"ok": False, "error": "请填写 UP 主 UID（博主号）"}), 400
    if not mid.isdigit():
        return jsonify({"ok": False, "error": "UID 应为数字（在 UP 主空间页 URL 里可以看到，如 space.bilibili.com/123456）"}), 400

    # 生成会话 ID 用于进度追踪
    session_id = gen_session_id()
    progress_store[session_id] = {
        "status": "starting",
        "message": "正在获取视频列表...",
        "progress": 0,
        "total": None,
        "current_video": "",
        "results": [],
        "saved_dir": "",
        "saved_count": 0,
    }

    def run_crawl():
        p = progress_store[session_id]

        def progress_callback(count, total, message):
            p["progress"] = count
            p["total"] = total
            p["message"] = message

        videos, err = collect_all_videos(mid, progress_callback=progress_callback)
        if err:
            p["status"] = "error"
            p["message"] = f"获取视频列表失败：{err}"
            return

        if not videos:
            p["status"] = "done"
            p["message"] = "该 UP 主暂无投稿视频或接口未返回数据"
            p["progress"] = 0
            p["total"] = 0
            return

        # 创建该 UP 主的保存目录
        save_dir = os.path.join(DEFAULT_SAVE_DIR, f"UP_{mid}")
        os.makedirs(save_dir, exist_ok=True)
        p["save_dir"] = save_dir

        p["total"] = len(videos)
        p["message"] = f"共 {len(videos)} 个视频，开始获取字幕..."
        p["progress"] = 0

        results = []
        saved_count = 0
        for i, v in enumerate(videos):
            bvid = v["bvid"]
            title = v["title"]
            p["current_video"] = f"({i+1}/{len(videos)}) {title}"
            p["message"] = f"正在获取第 {i+1}/{len(videos)} 个视频的字幕: {title}"
            p["progress"] = i

            one = get_all_subtitles_for_video(bvid, title, delay=1.0)
            results.append({
                "bvid": one["bvid"],
                "title": one["title"],
                "subtitles": one["subtitles"],
                "error": one.get("error"),
            })

            # 实时保存字幕到文件
            subs = one.get("subtitles") or []
            if subs:
                safe_title = safe_filename(title)[:60] or bvid
                for idx, sub in enumerate(subs):
                    lan = sub.get("lan", "unknown")
                    text = sub.get("text", "")
                    if text:
                        if len(subs) > 1:
                            fname = f"{safe_title}_{bvid}_{lan}.txt"
                        else:
                            fname = f"{safe_title}_{bvid}.txt"
                        fpath = os.path.join(save_dir, fname)
                        with open(fpath, "w", encoding="utf-8") as f:
                            f.write(text)
                        saved_count += 1

            time.sleep(1.0)

        p["results"] = results
        p["status"] = "done"
        p["message"] = f"爬取完成！已保存 {saved_count} 个字幕文件到: {save_dir}"
        p["progress"] = len(videos)
        p["current_video"] = ""
        p["saved_dir"] = save_dir
        p["saved_count"] = saved_count

    # 在后台线程运行爬取
    thread = threading.Thread(target=run_crawl)
    thread.start()

    return jsonify({"ok": True, "session_id": session_id}), 200


@app.route("/api/progress/<session_id>")
def progress(session_id):
    """SSE 端点，实时推送爬取进度。"""
    def generate():
        last_msg = ""
        while True:
            if session_id not in progress_store:
                break
            p = progress_store[session_id]
            msg = json.dumps({
                "status": p["status"],
                "message": p["message"],
                "progress": p["progress"],
                "total": p["total"],
                "current_video": p.get("current_video", ""),
                "saved_dir": p.get("saved_dir", ""),
                "saved_count": p.get("saved_count", 0),
            })
            if msg != last_msg:
                yield f"data: {msg}\n\n"
                last_msg = msg
            if p["status"] == "done" or p["status"] == "error":
                break
            time.sleep(0.5)
        # 完成后返回最终结果
        if session_id in progress_store:
            p = progress_store[session_id]
            if p.get("results"):
                yield f"data: {json.dumps({'status': 'done', 'results': p['results'], 'saved_dir': p.get('saved_dir', ''), 'saved_count': p.get('saved_count', 0)})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/crawl_result/<session_id>")
def crawl_result(session_id):
    """获取爬取结果（前端轮询用）。"""
    if session_id not in progress_store:
        return jsonify({"ok": False, "error": "会话已过期"}), 400
    p = progress_store[session_id]
    return jsonify({
        "ok": True,
        "status": p["status"],
        "message": p["message"],
        "progress": p["progress"],
        "total": p["total"],
        "current_video": p.get("current_video", ""),
        "results": p.get("results", []),
        "saved_dir": p.get("saved_dir", ""),
        "saved_count": p.get("saved_count", 0),
    })


@app.route("/api/export", methods=["POST"])
def export():
    """接收前端传来的爬取结果，打包为 ZIP 返回。"""
    data = request.get_json() or {}
    videos = data.get("videos") or []
    if not videos:
        return jsonify({"ok": False, "error": "没有可导出的字幕数据"}), 400

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for v in videos:
            bvid = v.get("bvid", "")
            title = safe_filename(v.get("title", ""))[:80] or bvid
            subs = v.get("subtitles") or []
            if not subs:
                continue
            for idx, sub in enumerate(subs):
                lan = sub.get("lan", "unknown")
                text = sub.get("text", "")
                name = f"{title}_{bvid}_{lan}.txt" if len(subs) > 1 else f"{title}_{bvid}.txt"
                zf.writestr(name, text.encode("utf-8"))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name="bilibili_subtitles.zip",
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
