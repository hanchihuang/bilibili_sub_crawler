# -*- coding: utf-8 -*-
"""
B 站 API：获取 UP 主视频列表、视频 cid、字幕列表及字幕内容
"""
import hashlib
import os
import re
import time
import urllib.parse

import requests

COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookie.txt")
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]
SESSDATA = ""
COOKIE_VALUE = ""


def _extract_sessdata(cookie_text: str) -> str:
    if not cookie_text:
        return ""
    if "=" not in cookie_text:
        return cookie_text.strip()
    match = re.search(r"(?:^|;\s*)SESSDATA=([^;]+)", cookie_text)
    return match.group(1).strip() if match else ""


def update_cookie_header(cookie_text: str):
    """更新请求头里的 Cookie，兼容旧格式（仅 SESSDATA）与完整 cookie 串。"""
    global COOKIE_VALUE, SESSDATA
    COOKIE_VALUE = (cookie_text or "").strip()
    SESSDATA = _extract_sessdata(COOKIE_VALUE)
    if not COOKIE_VALUE:
        HEADERS.pop("Cookie", None)
        SESSION.headers.pop("Cookie", None)
        return
    if "=" not in COOKIE_VALUE:
        COOKIE_VALUE = f"SESSDATA={COOKIE_VALUE}"
    HEADERS["Cookie"] = COOKIE_VALUE
    SESSION.headers["Cookie"] = COOKIE_VALUE


HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
if os.path.exists(COOKIE_FILE):
    with open(COOKIE_FILE, "r", encoding="utf-8") as f:
        update_cookie_header(f.read().strip())


def _request_json(url: str, *, params=None, timeout=15):
    r = SESSION.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _get_wbi_keys():
    data = _request_json("https://api.bilibili.com/x/web-interface/nav", timeout=15)
    wbi_img = data.get("data", {}).get("wbi_img") or {}
    img_url = wbi_img.get("img_url", "")
    sub_url = wbi_img.get("sub_url", "")
    img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
    if not img_key or not sub_key:
        raise RuntimeError("无法获取 WBI 签名密钥")
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _sign_wbi_params(params: dict):
    mixin_key = _get_wbi_keys()
    signed = {k: v for k, v in params.items() if v is not None}
    signed["wts"] = int(time.time())
    signed = {k: re.sub(r"[!'()*]", "", str(v)) for k, v in signed.items()}
    query = urllib.parse.urlencode(sorted(signed.items()))
    signed["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    return signed


def get_up_videos(mid: str, pn: int = 1, ps: int = 50):
    """获取 UP 主投稿视频列表（一页）。mid 为 UP 主 UID（数字字符串）。"""
    url = "https://api.bilibili.com/x/space/wbi/arc/search"
    params = _sign_wbi_params({"mid": str(mid), "pn": pn, "ps": ps, "order": "pubdate", "tid": 0})
    try:
        data = _request_json(url, params=params, timeout=15)
        if data.get("code") != 0:
            return None, data.get("message", "未知错误")
        lst = data.get("data", {}).get("list", {}).get("vlist") or []
        total = data.get("data", {}).get("page", {}).get("count", 0)
        return {"vlist": lst, "total": total}, None
    except Exception as e:
        return None, str(e)


def get_up_videos_cursor(mid: str, cursor: str = ""):
    """无需 WBI：app 端 cursor 分页获取投稿列表。返回 (items, next_cursor, has_more)。"""
    time.sleep(3)  # 降低请求频率，避免触发风控
    url = "https://app.biliapi.com/x/v2/space/archive/cursor"
    params = {"vmid": str(mid)}
    if cursor:
        params["cursor"] = cursor
    try:
        data = _request_json(url, params=params, timeout=15)
        if data.get("code") != 0:
            return None, None, False, data.get("message", "未知错误")
        d = data.get("data", {})
        items = d.get("items") or []
        cursor_next = str(d.get("cursor", {}).get("next_cursor") or d.get("cursor") or "")
        has_more = bool(d.get("has_more", True) and cursor_next)
        return items, cursor_next, has_more, None
    except Exception as e:
        return None, None, False, str(e)


def get_up_videos_legacy(mid: str, pn: int = 1, ps: int = 50):
    """备用：旧版 space/arc/search（无 wbi），若 wbi 失败可尝试。"""
    url = "https://api.bilibili.com/x/space/arc/search"
    params = {"mid": str(mid), "pn": pn, "ps": ps}
    try:
        data = _request_json(url, params=params, timeout=15)
        if data.get("code") != 0:
            return None, data.get("message", "未知错误")
        lst = data.get("data", {}).get("list", {}).get("vlist") or []
        total = data.get("data", {}).get("page", {}).get("count", 0)
        return {"vlist": lst, "total": total}, None
    except Exception as e:
        return None, str(e)


def get_video_cids(bvid: str):
    """根据 bvid 获取所有分 P 的 cid 列表。"""
    url = "https://api.bilibili.com/x/player/pagelist"
    params = {"bvid": bvid}
    try:
        data = _request_json(url, params=params, timeout=10)
        if data.get("code") != 0:
            return None, data.get("message", "未知错误")
        pages = data.get("data", [])
        return [p.get("cid") for p in pages if p.get("cid")], None
    except Exception as e:
        return None, str(e)


def get_subtitle_list(bvid: str, cid: int):
    """获取某视频某 P 的字幕列表（含 subtitle_url）。"""
    url = "https://api.bilibili.com/x/player/v2"
    params = {"bvid": bvid, "cid": cid}
    try:
        data = _request_json(url, params=params, timeout=10)
        if data.get("code") != 0:
            return None, data.get("message", "未知错误")
        sub = data.get("data", {}).get("subtitle") or {}
        subtitles = sub.get("subtitles") or []
        return subtitles, None
    except Exception as e:
        return None, str(e)


def fetch_subtitle_content(subtitle_url: str):
    """根据字幕 URL 拉取字幕正文。url 可能为 // 开头，需补 https:。"""
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url
    try:
        data = _request_json(subtitle_url, timeout=10)
        body = data.get("body", [])
        return body, None
    except Exception as e:
        return None, str(e)


def body_to_plain_text(body: list) -> str:
    """将字幕 body 列表转为纯文本（按时间顺序拼接）。"""
    if not body:
        return ""
    lines = []
    for item in body:
        if isinstance(item, dict) and "content" in item:
            lines.append(item["content"].strip())
        elif isinstance(item, str):
            lines.append(item.strip())
    return "\n".join(lines)


def get_all_subtitles_for_video(bvid: str, title: str, delay: float = 0.5):
    """
    获取一个视频的全部字幕（多 P 合并，优先中文）。
    返回 {"title": str, "bvid": str, "subtitles": [{"lan": str, "text": str}], "error": str or None}
    """
    result = {"title": title, "bvid": bvid, "subtitles": [], "error": None}
    cids, err = get_video_cids(bvid)
    if err or not cids:
        result["error"] = err or "无法获取分 P 列表"
        return result
    time.sleep(delay)
    all_subtitles = []
    for cid in cids:
        subs, err = get_subtitle_list(bvid, cid)
        time.sleep(delay)
        if err:
            continue
        for s in subs:
            sub_url = s.get("subtitle_url") or ""
            if not sub_url:
                continue
            lan = s.get("lan_doc") or s.get("lan") or "未知"
            body, err2 = fetch_subtitle_content(sub_url)
            time.sleep(delay)
            if err2 or not body:
                continue
            text = body_to_plain_text(body)
            if not text:
                continue
            all_subtitles.append({"lan": lan, "text": text})
        if all_subtitles:
            break
    result["subtitles"] = all_subtitles
    return result


def _normalize_vlist_item(v):
    """统一不同接口返回的项为 {bvid, title}。"""
    bvid = v.get("bvid") or v.get("bv_id")
    title = v.get("title") or v.get("name") or ""
    return bvid, title


def collect_all_videos(mid: str, progress_callback=None, use_legacy_fallback=True):
    """
    收集 UP 主全部视频 bvid+title。
    优先使用 app cursor 接口（无需 WBI），失败则尝试 wbi/legacy。
    progress_callback(current_count, total_or_None, status_message) 可选。
    """
    collected = []
    pn = 1
    ps = 50
    total = None
    while True:
        out, err = get_up_videos(mid, pn=pn, ps=ps)
        if err and use_legacy_fallback:
            out, err = get_up_videos_legacy(mid, pn=pn, ps=ps)
        if err:
            return None, err
        vlist = out["vlist"]
        if total is None:
            total = out.get("total", 0)
        if progress_callback:
            progress_callback(len(collected) + len(vlist), total, f"正在获取视频列表... 已获取 {len(collected) + len(vlist)} / {total}")
        for v in vlist:
            bvid, title = _normalize_vlist_item(v)
            if bvid:
                collected.append({"bvid": bvid, "title": title})
        if len(vlist) < ps:
            break
        pn += 1
        time.sleep(2)  # 降低请求频率
    return collected, None
