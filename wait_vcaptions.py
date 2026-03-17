# -*- coding: utf-8 -*-
"""
B 站字幕爬取 - 等待 vCaptions 扩展安装后自动批量爬取
流程：
1. 启动 Selenium Chrome 浏览器
2. 提示用户手动安装 vCaptions 扩展
3. 用户确认安装完成后，按回车继续
4. 开始批量爬取指定 UP 主的所有视频字幕
"""
import os
import sys
import time
import pyperclip
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait

# 导入 B 站 API
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bilibili_api
from bilibili_api import collect_all_videos

# 默认保存目录
DEFAULT_SAVE_DIR = "/home/user/图片/bilibili_subtitles"
os.makedirs(DEFAULT_SAVE_DIR, exist_ok=True)

# vCaptions 扩展路径
EXTENSION_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vcaptions_extension")
SUBTITLE_READY_WAIT_SECONDS = 1
CLICK_WAIT_SECONDS = 20
COPY_RETRY_ATTEMPTS = 6
CLIPBOARD_POLL_TIMEOUT_SECONDS = 3.0
COMMON_PERMISSION_NAMES = [
    "clipboardReadWrite",
    "clipboardSanitizedWrite",
    "notifications",
    "geolocation",
]
COMMON_PERMISSION_DESCRIPTORS = [
    {"name": "clipboard-read"},
    {"name": "clipboard-write"},
    {"name": "notifications"},
    {"name": "geolocation"},
]


def _click_visible_text(driver, text, exact=True):
    """递归穿透 open shadow DOM，点击可见且可交互的指定文案元素。"""
    script = r"""
const targetText = arguments[0];
const exact = arguments[1];

function isVisible(el) {
  if (!el || !el.isConnected) return false;
  const style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') return false;
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0;
}

function isClickable(el) {
  if (!el) return false;
  if (typeof el.click === 'function' && (el.tagName === 'BUTTON' || el.tagName === 'A')) return true;
  if (el.getAttribute('role') === 'button') return true;
  if (el.onclick) return true;
  const style = window.getComputedStyle(el);
  return style.cursor === 'pointer';
}

function walk(root, out) {
  const elements = root.querySelectorAll('*');
  for (const el of elements) {
    if (el.shadowRoot) walk(el.shadowRoot, out);
    out.push(el);
  }
}

const all = [];
walk(document, all);

const matched = all.filter(el => {
  const txt = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  if (!txt) return false;
  return exact ? txt === targetText : txt.includes(targetText);
}).filter(isVisible);

matched.sort((a, b) => {
  const aRect = a.getBoundingClientRect();
  const bRect = b.getBoundingClientRect();
  const aRightPenalty = aRect.left >= window.innerWidth * 0.65 ? 0 : 500;
  const bRightPenalty = bRect.left >= window.innerWidth * 0.65 ? 0 : 500;
  const aScore = aRightPenalty + (isClickable(a) ? 0 : 1000) + (a.innerText || a.textContent || '').trim().length;
  const bScore = bRightPenalty + (isClickable(b) ? 0 : 1000) + (b.innerText || b.textContent || '').trim().length;
  return aScore - bScore;
});

const target = matched.find(isClickable) || matched[0];
if (!target) return false;

target.scrollIntoView({block: 'center', inline: 'center'});
target.click();
return true;
"""
    return bool(driver.execute_script(script, text, exact))


def _grant_browser_permissions(driver, page_url):
    """提前允许常见权限，尽量避免 Chrome 左上角权限弹窗打断复制。"""
    parsed = urlparse(page_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if not parsed.scheme or not parsed.netloc:
        return

    try:
        driver.execute_cdp_cmd(
            "Browser.grantPermissions",
            {"origin": origin, "permissions": COMMON_PERMISSION_NAMES},
        )
    except Exception:
        pass

    for descriptor in COMMON_PERMISSION_DESCRIPTORS:
        try:
            driver.execute_cdp_cmd(
                "Browser.setPermission",
                {
                    "origin": origin,
                    "permission": descriptor,
                    "setting": "granted",
                },
            )
        except Exception:
            continue


def _scroll_right_panel(driver):
    """只在右侧下载黑框内小幅下拉，避免整页下拉或直接拉到底。"""
    script = r"""
function walk(root, out) {
  const elements = root.querySelectorAll('*');
  for (const el of elements) {
    if (el.shadowRoot) walk(el.shadowRoot, out);
    out.push(el);
  }
}

function parentOrHost(el) {
  if (!el) return null;
  if (el.parentElement) return el.parentElement;
  const root = el.getRootNode && el.getRootNode();
  return root instanceof ShadowRoot ? root.host : null;
}

function parseBg(style) {
  const m = (style.backgroundColor || '').match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
  if (!m) return null;
  return [parseInt(m[1], 10), parseInt(m[2], 10), parseInt(m[3], 10)];
}

function isDark(el) {
  const rgb = parseBg(window.getComputedStyle(el));
  if (!rgb) return false;
  return rgb[0] < 70 && rgb[1] < 70 && rgb[2] < 70;
}

function isScrollable(el) {
  if (!el) return false;
  const style = window.getComputedStyle(el);
  return (style.overflowY === 'auto' || style.overflowY === 'scroll') &&
    el.scrollHeight > el.clientHeight + 20;
}

function panelScore(el) {
  if (!el) return -1;
  const rect = el.getBoundingClientRect();
  if (rect.width < 220 || rect.height < 180) return -1;
  let score = 0;
  if (rect.left >= window.innerWidth * 0.55) score += 200;
  if (isDark(el)) score += 120;
  score += Math.min(rect.height, 900) / 10;
  score += Math.min(rect.width, 500) / 20;
  const txt = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  if (txt.includes('文件格式')) score += 200;
  if (txt.includes('无水印下载')) score += 200;
  if (txt.includes('内容预览')) score += 150;
  if (txt.includes('复制')) score += 120;
  if (txt.includes('下载')) score += 60;
  return score;
}

const all = [];
walk(document, all);

const anchors = all.filter(el => {
  const txt = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  return txt.includes('文件格式') || txt.includes('无水印下载') || txt.includes('内容预览');
});

const candidates = new Set();
for (const anchor of anchors) {
  let cur = anchor;
  for (let i = 0; cur && i < 8; i += 1) {
    if (panelScore(cur) > 0) candidates.add(cur);
    cur = parentOrHost(cur);
  }
}

const panels = Array.from(candidates).sort((a, b) => panelScore(b) - panelScore(a));
const panel = panels[0];
if (!panel) return 0;

const scrollables = [];
for (const el of [panel, ...panel.querySelectorAll('*')]) {
  if (isScrollable(el)) scrollables.push(el);
}

scrollables.sort((a, b) => {
  const ar = a.getBoundingClientRect();
  const br = b.getBoundingClientRect();
  const aScore = (a.scrollHeight - a.clientHeight) + ar.height;
  const bScore = (b.scrollHeight - b.clientHeight) + br.height;
  return bScore - aScore;
});

const target = scrollables[0];
if (!target) return 0;

const maxScrollTop = Math.max(0, target.scrollHeight - target.clientHeight);
if (maxScrollTop <= 0) return 0;

const delta = Math.max(80, Math.min(220, Math.round(target.clientHeight * 0.35)));
const next = Math.min(target.scrollTop + delta, Math.max(0, maxScrollTop - 40));
if (next <= target.scrollTop) return 0;

target.scrollTop = next;
return next;
"""
    return driver.execute_script(script)


def _find_copy_button_in_right_panel(driver):
    """定位右侧下载黑框底部按钮行中的“复制”按钮。"""
    script = r"""
function walk(root, out) {
  const elements = root.querySelectorAll('*');
  for (const el of elements) {
    if (el.shadowRoot) walk(el.shadowRoot, out);
    out.push(el);
  }
}

function parentOrHost(el) {
  if (!el) return null;
  if (el.parentElement) return el.parentElement;
  const root = el.getRootNode && el.getRootNode();
  return root instanceof ShadowRoot ? root.host : null;
}

function parseBg(style) {
  const m = (style.backgroundColor || '').match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
  if (!m) return null;
  return [parseInt(m[1], 10), parseInt(m[2], 10), parseInt(m[3], 10)];
}

function isDark(el) {
  const rgb = parseBg(window.getComputedStyle(el));
  if (!rgb) return false;
  return rgb[0] < 70 && rgb[1] < 70 && rgb[2] < 70;
}

function isVisible(el) {
  if (!el || !el.isConnected) return false;
  const style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') return false;
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0;
}

function isClickable(el) {
  if (!el) return false;
  if (typeof el.click === 'function' && (el.tagName === 'BUTTON' || el.tagName === 'A')) return true;
  if (el.getAttribute('role') === 'button') return true;
  if (el.onclick) return true;
  const style = window.getComputedStyle(el);
  return style.cursor === 'pointer';
}

function panelScore(el) {
  if (!el) return -1;
  const rect = el.getBoundingClientRect();
  if (rect.width < 220 || rect.height < 180) return -1;
  let score = 0;
  if (rect.left >= window.innerWidth * 0.55) score += 200;
  if (isDark(el)) score += 120;
  score += Math.min(rect.height, 900) / 10;
  const txt = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  if (txt.includes('文件格式')) score += 200;
  if (txt.includes('无水印下载')) score += 200;
  if (txt.includes('内容预览')) score += 150;
  if (txt.includes('复制')) score += 120;
  if (txt.includes('下载')) score += 60;
  return score;
}

const all = [];
walk(document, all);

const anchors = all.filter(el => {
  const txt = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  return txt.includes('文件格式') || txt.includes('无水印下载') || txt.includes('内容预览');
});

const candidates = new Set();
for (const anchor of anchors) {
  let cur = anchor;
  for (let i = 0; cur && i < 8; i += 1) {
    if (panelScore(cur) > 0) candidates.add(cur);
    cur = parentOrHost(cur);
  }
}

const panels = Array.from(candidates).sort((a, b) => panelScore(b) - panelScore(a));
const panel = panels[0];
if (!panel) return false;

const copyButtons = [];
const downloadButtons = [];
for (const el of [panel, ...panel.querySelectorAll('*')]) {
  const txt = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  if (!isVisible(el) || !isClickable(el)) continue;
  if (txt === '复制') copyButtons.push(el);
  if (txt === '下载') downloadButtons.push(el);
}

const panelRect = panel.getBoundingClientRect();
const target = copyButtons.sort((a, b) => {
  const ar = a.getBoundingClientRect();
  const br = b.getBoundingClientRect();

  function score(copyRect) {
    let s = 0;
    const bottomBias = copyRect.top - panelRect.top;
    s += bottomBias;
    if (copyRect.top >= panelRect.top + panelRect.height * 0.6) s += 400;

    for (const downloadEl of downloadButtons) {
      const dr = downloadEl.getBoundingClientRect();
      const sameRow = Math.abs(dr.top - copyRect.top) <= 30;
      const onRight = dr.left > copyRect.right - 10;
      const closeEnough = dr.left - copyRect.right <= 220;
      if (sameRow && onRight && closeEnough) {
        s += 1000;
        s -= Math.abs(dr.top - copyRect.top);
        s -= Math.abs((dr.left - copyRect.right) - 40) * 0.5;
      }
    }

    return s;
  }

  return score(br) - score(ar) || br.top - ar.top || br.left - ar.left;
})[0];

if (!target) return null;
target.scrollIntoView({block: 'center', inline: 'center'});
return target;
"""
    return driver.execute_script(script)


def _dispatch_mouse_click(driver, element):
    """向元素分发完整鼠标事件序列，尽量接近人工点击。"""
    script = r"""
const el = arguments[0];
if (!el) return false;
el.scrollIntoView({block: 'center', inline: 'center'});
const rect = el.getBoundingClientRect();
const clientX = rect.left + rect.width / 2;
const clientY = rect.top + rect.height / 2;
const events = ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'];
for (const type of events) {
  const EventCtor = type.startsWith('pointer') ? PointerEvent : MouseEvent;
  el.dispatchEvent(new EventCtor(type, {
    bubbles: true,
    cancelable: true,
    composed: true,
    pointerId: 1,
    isPrimary: true,
    button: 0,
    buttons: 1,
    clientX,
    clientY,
    view: window,
  }));
}
return true;
"""
    return bool(driver.execute_script(script, element))


def _click_copy_button(driver, element):
    """对同一个复制按钮尝试多种点击方式，优先使用更像人工的点击。"""
    strategies = [
        ("actions", lambda: ActionChains(driver).move_to_element(element).pause(0.1).click(element).perform()),
        ("selenium", lambda: element.click()),
        ("mouse-events", lambda: _dispatch_mouse_click(driver, element)),
        ("js-click", lambda: driver.execute_script("arguments[0].click(); return true;", element)),
    ]

    driver.execute_script("window.focus();")

    for strategy_name, strategy in strategies:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element)
            strategy()
            return strategy_name
        except Exception:
            continue

    return None


def _read_system_clipboard_text():
    try:
        return pyperclip.paste() or ""
    except Exception:
        return ""


def _read_browser_clipboard_text(driver):
    script = """
const done = arguments[arguments.length - 1];
if (!navigator.clipboard || !navigator.clipboard.readText) {
  done({ok: false, text: "", error: "clipboard api unavailable"});
  return;
}

navigator.clipboard.readText()
  .then(text => done({ok: true, text: text || ""}))
  .catch(err => done({ok: false, text: "", error: String(err)}));
"""
    try:
        result = driver.execute_async_script(script)
        if result and result.get("ok"):
            return result.get("text", "") or ""
    except Exception:
        pass
    return ""


def _clear_clipboards(driver):
    try:
        pyperclip.copy("")
    except Exception:
        pass

    script = """
const done = arguments[arguments.length - 1];
if (!navigator.clipboard || !navigator.clipboard.writeText) {
  done(false);
  return;
}

navigator.clipboard.writeText("")
  .then(() => done(true))
  .catch(() => done(false));
"""
    try:
        driver.execute_async_script(script)
    except Exception:
        pass


def _pick_valid_clipboard_text(driver, min_length=10):
    candidates = [
        ("system", _read_system_clipboard_text()),
        ("browser", _read_browser_clipboard_text(driver)),
    ]
    valid = []
    for source, text in candidates:
        stripped = (text or "").strip()
        if len(stripped) > min_length:
            valid.append((len(stripped), source, text))

    if not valid:
        return None, None

    _, source, text = max(valid, key=lambda item: item[0])
    return text, source


def _wait_for_clipboard_text(driver, min_length=10, timeout=CLIPBOARD_POLL_TIMEOUT_SECONDS):
    deadline = time.time() + timeout

    while time.time() < deadline:
        text, source = _pick_valid_clipboard_text(driver, min_length=min_length)
        if text:
            return text, source
        time.sleep(0.2)

    return _pick_valid_clipboard_text(driver, min_length=min_length)


def _copy_subtitle_via_download_panel(driver):
    """按“下载 -> 复制”流程复制字幕文本。"""
    _clear_clipboards(driver)
    time.sleep(0.5)

    def click_download(_driver):
        return _click_visible_text(_driver, "下载", exact=True)

    WebDriverWait(driver, CLICK_WAIT_SECONDS, poll_frequency=0.5).until(click_download)
    print("    已点击右侧面板“下载”按钮")
    time.sleep(1.5)

    for scroll_attempt in range(4):
        _scroll_right_panel(driver)
        time.sleep(0.8)

        for click_attempt in range(COPY_RETRY_ATTEMPTS):
            copy_button = _find_copy_button_in_right_panel(driver)
            if not copy_button:
                break

            _clear_clipboards(driver)
            time.sleep(0.2)

            strategy = _click_copy_button(driver, copy_button)
            if not strategy:
                continue

            print(f"    已点击下载中心“复制”按钮，第 {click_attempt + 1} 次尝试，方式: {strategy}")
            subtitle, source = _wait_for_clipboard_text(driver)
            if subtitle:
                print(f"    已从{source}剪贴板读取到字幕内容")
                return subtitle

            print("    页面可能弹出了左上角权限框，或页面显示成功但系统未真正复制；继续自动重试...")
            time.sleep(0.6)

        if scroll_attempt < 3:
            WebDriverWait(driver, CLICK_WAIT_SECONDS, poll_frequency=0.5).until(click_download)
            print("    重新点击右侧面板“下载”按钮，准备继续重试")
            time.sleep(1.0)

    return None


def create_driver():
    """创建 Chrome driver，加载 vCaptions 扩展"""
    options = Options()
    options.add_argument('--no-first-run')
    options.add_argument('--no-default-browser-check')
    options.add_argument('--user-data-dir=/home/user/.config/google-chrome/Default')
    options.add_argument(f'--load-extension={EXTENSION_PATH}')
    options.add_argument('--disable-popup-blocking')
    options.add_experimental_option("prefs", {
        "profile.default_content_setting_values.notifications": 1,
        "profile.default_content_setting_values.geolocation": 1,
    })
    
    return webdriver.Chrome(options=options)


def get_subtitle_with_vcaptions(driver, bvid):
    """使用 vCaptions 获取单个视频的字幕"""
    video_url = f"https://www.bilibili.com/video/{bvid}"

    print(f"  打开视频: {video_url}")
    driver.get(video_url)
    _grant_browser_permissions(driver, video_url)
    time.sleep(6)  # 等待页面加载
    print(f"    字幕显示后额外等待 {SUBTITLE_READY_WAIT_SECONDS} 秒...")
    time.sleep(SUBTITLE_READY_WAIT_SECONDS)

    try:
        subtitle = _copy_subtitle_via_download_panel(driver)
        if subtitle and len(subtitle) > 10:
            print(f"    字幕长度: {len(subtitle)} 字符")
            return subtitle
        print("    下载中心复制后剪贴板为空，直接放弃当前视频")
    except Exception as e:
        print(f"    通过下载中心复制字幕失败，直接放弃当前视频: {e}")
    
    return None


def collect_all_videos_with_retry(mid: str, max_retries=5, base_delay=10):
    """
    带重试的获取视频列表
    """
    for attempt in range(max_retries):
        try:
            # 使用当前可用的 WBI 接口拉取投稿列表
            videos, err = collect_all_videos(mid)
            if err:
                print(f"  尝试 {attempt+1}/{max_retries} 失败: {err}")
                if "频繁" in err or "too fast" in err.lower():
                    wait_time = base_delay * (attempt + 1)
                    print(f"  等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                    continue
                if attempt < max_retries - 1:
                    time.sleep(base_delay)
                    continue
            return videos, err
        except Exception as e:
            print(f"  尝试 {attempt+1}/{max_retries} 异常: {e}")
            if attempt < max_retries - 1:
                time.sleep(base_delay)
    
    return None, "达到最大重试次数"


def batch_crawl_with_vcaptions(driver, mid, save_dir):
    """使用 vCaptions 批量爬取指定 UP 主的所有视频字幕"""
    print(f"\n开始批量爬取 UP 主 {mid} 的视频字幕...")
    
    # 获取视频列表（带重试）
    print("获取视频列表...")
    videos, err = collect_all_videos_with_retry(mid, max_retries=5, base_delay=15)
    if err:
        print(f"获取视频列表失败: {err}")
        return
    
    if not videos:
        print("该 UP 主没有视频或获取失败")
        return
    
    print(f"共获取到 {len(videos)} 个视频\n")
    
    # 逐个爬取字幕
    success_count = 0
    fail_count = 0
    
    for i, v in enumerate(videos):
        bvid = v["bvid"]
        title = v["title"]
        
        print(f"[{i+1}/{len(videos)}] {title}")
        
        subtitle = get_subtitle_with_vcaptions(driver, bvid)
        
        if subtitle:
            # 保存字幕
            safe_title = "".join(c for c in title if c.isalnum() or c in " _-（）()【】[]")[:60] or bvid
            fname = f"{safe_title}_{bvid}.txt"
            fpath = os.path.join(save_dir, fname)
            
            try:
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(subtitle)
                print(f"    ✓ 已保存: {fname}")
                success_count += 1
            except Exception as e:
                print(f"    ✗ 保存失败: {e}")
                fail_count += 1
        else:
            print("    ✗ 获取字幕失败")
            fail_count += 1
        
        # 每个视频间隔
        time.sleep(2)
    
    print(f"\n批量爬取完成！成功: {success_count}, 失败: {fail_count}")
    print(f"字幕保存目录: {save_dir}")


def get_browser_cookies(driver):
    """从浏览器获取 B 站的 cookies"""
    try:
        driver.get("https://www.bilibili.com")
        time.sleep(3)
        
        # 获取所有 cookies
        cookies = driver.get_cookies()
        
        # 提取 SESSDATA
        sessdata = None
        for cookie in cookies:
            if cookie['name'] == 'SESSDATA':
                sessdata = cookie['value']
                break
        
        if sessdata:
            return sessdata
        else:
            print("未找到 SESSDATA，请确保浏览器已登录 B 站")
            return None
    except Exception as e:
        print(f"获取 cookies 失败: {e}")
        return None


def build_bilibili_cookie_header(cookies):
    """仅保留 B 站相关 cookies，拼成 requests 可直接使用的 Cookie 头。"""
    cookie_pairs = []
    seen = set()
    for cookie in cookies:
        domain = cookie.get("domain", "")
        if "bilibili.com" not in domain and "biliapi.com" not in domain:
            continue
        name = cookie.get("name")
        value = cookie.get("value", "")
        if not name or name in seen:
            continue
        seen.add(name)
        cookie_pairs.append(f"{name}={value}")
    return "; ".join(cookie_pairs)


def main():
    print("\n" + "="*60)
    print("B 站字幕爬取工具 - 等待 vCaptions 扩展安装模式")
    print("="*60)
    
    # 检查 cookie 文件
    cookie_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookie.txt")
    if os.path.exists(cookie_file):
        with open(cookie_file, "r", encoding="utf-8") as f:
            cookie_text = f.read().strip()
            if cookie_text:
                print("✓ 已加载 cookie.txt 中的登录态")
    
    # 输入 UP 主 UID
    mid = input("\n请输入 UP 主 UID（博主号，如 space.bilibili.com/123456）: ").strip()
    if not mid.isdigit():
        print("错误: UID 必须是数字")
        return
    
    # 确认保存目录
    save_dir = os.path.join(DEFAULT_SAVE_DIR, f"UP_{mid}")
    os.makedirs(save_dir, exist_ok=True)
    print(f"字幕将保存到: {save_dir}")
    
    # 创建浏览器
    print("\n启动 Chrome 浏览器...")
    driver = create_driver()
    
    try:
        # 打开扩展管理页面，让用户安装
        driver.get("chrome://extensions/")
        time.sleep(2)
        
        print("\n" + "="*60)
        print("请手动安装 vCaptions 扩展！")
        print("="*60)
        print(f"扩展路径: {EXTENSION_PATH}")
        print("\n安装步骤：")
        print("1. 在打开的扩展页面，点击右上角「开发者模式」开启")
        print("2. 点击「加载已解压的扩展程序」")
        print(f"3. 选择路径: {EXTENSION_PATH}")
        print("4. 确认扩展已显示并开启")
        print("\n安装完成后，回到这个终端按回车键继续...")
        print("="*60 + "\n")
        
        input("按回车键继续...")
        
        # 先让用户在浏览器中登录 B 站
        print("\n" + "="*60)
        print("请先在浏览器中登录 B 站！")
        print("="*60)
        print("1. 在打开的浏览器中访问 bilibili.com")
        print("2. 登录你的 B 站账号")
        print("3. 登录完成后回到这个终端按回车键继续")
        print("="*60 + "\n")
        
        input("已登录 B 站后，按回车键继续...")
        
        # 从浏览器获取登录态
        print("\n获取浏览器登录态...")
        
        # 刷新 B 站首页，确保 cookie 最新
        driver.get("https://www.bilibili.com")
        time.sleep(5)  # 等待页面加载和 cookie 设置
        
        # 获取所有 cookies，打印调试信息
        cookies = driver.get_cookies()
        print(f"  共获取到 {len(cookies)} 个 cookies")
        
        # 打印包含 bilibili 的 cookies
        bili_cookies = [c for c in cookies if 'bilibili' in c.get('domain', '')]
        print(f"  B 站相关 cookies: {[c['name'] for c in bili_cookies]}")
        
        sessdata = None
        for cookie in cookies:
            if cookie['name'] == 'SESSDATA':
                sessdata = cookie['value']
                break
        
        if sessdata:
            cookie_header = build_bilibili_cookie_header(cookies)
            cookie_to_save = cookie_header or sessdata

            with open(cookie_file, "w", encoding="utf-8") as f:
                f.write(cookie_to_save)
            print("✓ 已保存登录态到 cookie.txt")

            bilibili_api.update_cookie_header(cookie_to_save)
        else:
            print("⚠ 未找到 SESSDATA，请确保：")
            print("  1. 浏览器已登录 B 站")
            print("  2. 如果没登录，请先在浏览器中手动登录")
        
        print("\n开始批量爬取字幕...\n")
        
        batch_crawl_with_vcaptions(driver, mid, save_dir)
        
        print("\n" + "="*60)
        print("全部完成！请查看字幕文件")
        print("="*60)
        
        # 保持浏览器打开
        print("\n浏览器保持打开状态，按 Ctrl+C 退出...")
        while True:
            time.sleep(10)
            
    except KeyboardInterrupt:
        print("\n用户中断")
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
