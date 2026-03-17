# -*- coding: utf-8 -*-
"""
使用 vCaptions 插件的快捷键获取字幕
"""
from playwright.sync_api import sync_playwright
import time
import pyperclip  # 用于读取剪贴板

def get_subtitle_via_vcaptions(bvid="BV1GNfSBgE7b"):
    video_url = f"https://www.bilibili.com/video/{bvid}"
    
    with sync_playwright() as p:
        # 启动非 headless Chrome
        browser = p.chromium.launch(
            headless=False,
            args=[
                '--disable-blink-features=AutomationControlled',
            ]
        )
        
        page = browser.new_page()
        print(f"打开视频: {video_url}")
        page.goto(video_url)
        
        # 等待页面加载
        print("等待页面加载...")
        page.wait_for_load_state('networkidle', timeout=15000)
        
        # 等待 vCaptions 插件加载（通常几秒后自动开始识别）
        print("等待 vCaptions 识别字幕 (2秒)...")
        time.sleep(2)

        # 让页面先拿到焦点，再全选并复制字幕文本
        print("页面聚焦后等待字幕稳定...")
        page.locator("body").click()
        time.sleep(1)

        print("按下 Ctrl+A 全选字幕...")
        page.keyboard.press('Control+A')
        time.sleep(0.5)

        print("按下 Ctrl+C 复制字幕...")
        page.keyboard.press('Control+C')

        # 等待复制完成
        time.sleep(2)
        
        # 读取剪贴板
        subtitle_text = pyperclip.paste()
        
        print(f"\n获取到的字幕长度: {len(subtitle_text)} 字符")
        print(f"字幕前500字:\n{subtitle_text[:500]}")
        
        # 保存到文件
        if subtitle_text:
            safe_bvid = bvid.replace('/', '_')
            with open(f'/home/user/图片/bilibili_subtitles/{safe_bvid}_vcaptions.txt', 'w', encoding='utf-8') as f:
                f.write(subtitle_text)
            print(f"\n字幕已保存到: /home/user/图片/bilibili_subtitles/{safe_bvid}_vcaptions.txt")
        
        input("\n按回车关闭浏览器...")
        browser.close()

if __name__ == "__main__":
    import sys
    bvid = sys.argv[1] if len(sys.argv) > 1 else "BV1GNfSBgE7b"
    get_subtitle_via_vcaptions(bvid)
