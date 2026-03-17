# -*- coding: utf-8 -*-
"""
探索 vCaptions 插件的字幕存储方式
"""
from playwright.sync_api import sync_playwright
import time

def explore_vcaptions():
    with sync_playwright() as p:
        # 连接已打开的 Chrome
        context = p.chromium.launch_persistent_context(
            '/home/user/.config/google-chrome/Default',
            headless=False
        )
        
        # 获取所有页面
        pages = context.pages
        print(f"当前页面数: {len(pages)}")
        
        # 找 B 站页面
        bilibili_page = None
        for page in pages:
            if 'bilibili.com' in page.url and 'video' in page.url:
                bilibili_page = page
                break
        
        if not bilibili_page:
            print("\n没有找到 B 站视频页面")
            print("请在 Chrome 中打开一个 B 站视频页面，等待 vCaptions 加载字幕后告诉我")
            input("按回车退出...")
            return
        
        print(f"\n找到 B 站页面: {bilibili_page.url}")
        print("等待 vCaptions 加载字幕...")
        time.sleep(5)  # 等待字幕加载
        
        # 1. 检查 localStorage
        local_storage = bilibili_page.evaluate("""() => {
            let result = {};
            for (let i = 0; i < localStorage.length; i++) {
                let key = localStorage.key(i);
                if (key.includes('vcaption') || key.includes('caption') || key.includes('subtitle')) {
                    try {
                        result[key] = localStorage.getItem(key);
                    } catch(e) {
                        result[key] = localStorage.getItem(key);
                    }
                }
            }
            return result;
        }""")
        print(f"\nLocalStorage 中包含 caption 关键字的项: {len(local_storage)}")
        for k, v in local_storage.items():
            print(f"  - {k}: {v[:200] if v else 'null'}...")
        
        # 2. 检查 sessionStorage
        session_storage = bilibili_page.evaluate("""() => {
            let result = {};
            for (let i = 0; i < sessionStorage.length; i++) {
                let key = sessionStorage.key(i);
                if (key.includes('vcaption') || key.includes('caption') || key.includes('subtitle')) {
                    result[key] = sessionStorage.getItem(key);
                }
            }
            return result;
        }""")
        print(f"\nSessionStorage 中包含 caption 关键字的项: {len(session_storage)}")
        for k, v in session_storage.items():
            print(f"  - {k}: {v[:200] if v else 'null'}...")
        
        # 3. 查找可能包含 vCaptions 数据的全局变量
        global_vars = bilibili_page.evaluate("""() => {
            let result = {};
            for (let key in window) {
                try {
                    if (typeof window[key] === 'object' && window[key] !== null) {
                        let str = JSON.stringify(window[key]);
                        if (str && (str.includes('vcaption') || str.includes('caption') || str.includes('字幕'))) {
                            result[key] = str.substring(0, 500);
                        }
                    }
                } catch(e) {}
            }
            return result;
        }""")
        print(f"\n包含 caption 相关内容的全局变量: {len(global_vars)}")
        for k, v in global_vars.items():
            print(f"  - {k}: {v[:200]}...")
        
        # 4. 查找页面中可能存在的 vCaptions 元素
        caption_elements = bilibili_page.evaluate("""() => {
            // 查找包含 vCaptions 相关的元素
            let elements = document.querySelectorAll('*');
            let result = [];
            elements.forEach(el => {
                let id = el.id || '';
                let className = el.className || '';
                let text = el.innerText ? el.innerText.substring(0, 50) : '';
                if (id.includes('vcaption') || className.includes('vcaption') || id.includes('caption') || className.includes('caption')) {
                    result.push({
                        tag: el.tagName,
                        id: id,
                        className: className,
                        text: text
                    });
                }
            });
            return result;
        }""")
        print(f"\n页面中 vCaptions 相关元素: {len(caption_elements)}")
        for el in caption_elements[:10]:
            print(f"  - <{el['tag']}> id={el['id']} class={el['className'][:30]} text={el['text'][:30]}")
        
        # 5. 检查侧边栏（side panel）
        # vCaptions 可能在 side panel 中
        side_panel_info = bilibili_page.evaluate("""() => {
            // 检查是否有扩展程序创建的元素
            let shadowRoots = [];
            function findShadowRoots(root) {
                root.querySelectorAll('*').forEach(el => {
                    if (el.shadowRoot) {
                        shadowRoots.push({
                            host: el.tagName + '#' + el.id,
                            content: el.shadowRoot.innerHTML.substring(0, 200)
                        });
                        findShadowRoots(el.shadowRoot);
                    }
                });
            }
            findShadowRoots(document);
            return shadowRoots;
        }""")
        print(f"\nShadow DOM 元素数: {len(side_panel_info)}")
        
        print("\n===== 建议操作 =====")
        print("由于 vCaptions 可能通过 Chrome Side Panel 运行，直接访问 DOM 比较困难")
        print("可能的方案：")
        print("1. 使用 Chrome DevTools Protocol 读取 side panel 内容")
        print("2. 使用扩展程序的 storage API")
        print("3. 通过键盘快捷键触发复制功能（Ctrl+Shift+C）")

if __name__ == "__main__":
    explore_vcaptions()