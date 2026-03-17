# -*- coding: utf-8 -*-
"""
使用 vCaptions 插件自动获取 B 站视频字幕
"""
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
import time
import pyperclip
import os
import sys
from urllib.parse import urlparse

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
    _clear_clipboards(driver)
    time.sleep(0.5)

    def click_download(_driver):
        return _click_visible_text(_driver, "下载", exact=True)

    WebDriverWait(driver, CLICK_WAIT_SECONDS, poll_frequency=0.5).until(click_download)
    print("已点击右侧面板“下载”按钮")
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

            print(f"已点击下载中心“复制”按钮，第 {click_attempt + 1} 次尝试，方式: {strategy}")
            subtitle, source = _wait_for_clipboard_text(driver)
            if subtitle:
                print(f"已从{source}剪贴板读取到字幕内容")
                return subtitle

            print("页面可能弹出了左上角权限框，或页面显示成功但系统未真正复制；继续自动重试...")
            time.sleep(0.6)

        if scroll_attempt < 3:
            WebDriverWait(driver, CLICK_WAIT_SECONDS, poll_frequency=0.5).until(click_download)
            print("重新点击右侧面板“下载”按钮，准备继续重试")
            time.sleep(1.0)

    return None


def install_extension_and_get_subtitle(bvid="BV1GNfSBgE7b", wait_time=15):
    """安装 vCaptions 扩展并获取字幕"""
    
    # 扩展路径
    extension_path = os.path.abspath("vcaptions_extension")
    
    # 创建 Chrome 选项
    options = Options()
    options.add_argument('--no-first-run')
    options.add_argument('--no-default-browser-check')
    options.add_argument('--user-data-dir=/home/user/.config/google-chrome/Default')
    options.add_argument(f'--load-extension={extension_path}')
    options.add_experimental_option("prefs", {
        "profile.default_content_setting_values.notifications": 1,
        "profile.default_content_setting_values.geolocation": 1,
    })
    
    print(f"扩展路径: {extension_path}")
    print(f"视频 BV ID: {bvid}")
    
    # 启动 Chrome
    print("\n启动 Chrome 并加载 vCaptions 扩展...")
    driver = webdriver.Chrome(options=options)
    
    try:
        # 打开 B 站视频页面
        video_url = f"https://www.bilibili.com/video/{bvid}"
        print(f"打开视频: {video_url}")
        driver.get(video_url)
        _grant_browser_permissions(driver, video_url)
        
        # 等待页面加载
        print("等待页面加载...")
        time.sleep(8)
        
        print(f"页面标题: {driver.title}")
        
        # 等待 vCaptions 识别字幕
        print(f"等待 vCaptions 识别字幕 ({wait_time}秒)...")
        time.sleep(wait_time)

        print(f"字幕显示后额外等待 {SUBTITLE_READY_WAIT_SECONDS} 秒...")
        time.sleep(SUBTITLE_READY_WAIT_SECONDS)

        subtitle = _copy_subtitle_via_download_panel(driver)
        if not (subtitle and len(subtitle) > 10):
            print("本轮复制后剪贴板为空，直接放弃当前视频")

        if subtitle:
            print(f"\n字幕长度: {len(subtitle)} 字符")
        
        if subtitle and len(subtitle) > 10:
            print(f"字幕内容预览:\n{subtitle[:500]}")
            
            # 保存字幕
            safe_bvid = bvid.replace('/', '_')
            output_path = f'/home/user/图片/bilibili_subtitles/{safe_bvid}_vcaptions.txt'
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(subtitle)
            print(f"\n字幕已保存到: {output_path}")
        else:
            print("剪贴板为空，尝试查找页面元素...")
            
            # 尝试查找 vCaptions 相关元素
            caption_data = driver.execute_script("""
                // 尝试从 vCaptions 扩展获取数据
                let results = [];
                
                // 查找 sidepanel
                let sidepanel = document.querySelector('iframe[src*="sidepanel"]');
                if (sidepanel) {
                    results.push('Found sidepanel iframe');
                }
                
                // 查找可能的字幕容器
                let containers = document.querySelectorAll('[class*="caption"], [class*="subtitle"], [id*="caption"]');
                containers.forEach(el => {
                    let text = el.innerText?.trim();
                    if (text && text.length > 20 && text.length < 5000) {
                        results.push({tag: el.tagName, text: text.substring(0, 200)});
                    }
                });
                
                return results;
            """)
            print(f"找到的元素: {caption_data}")
        
        # 保持浏览器打开
        print("\n按 Ctrl+C 退出...")
        time.sleep(60)
        
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.quit()

if __name__ == "__main__":
    bvid = sys.argv[1] if len(sys.argv) > 1 else "BV1GNfSBgE7b"
    install_extension_and_get_subtitle(bvid)
