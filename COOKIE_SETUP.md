# B 站 Cookie 配置说明

如果遇到 "请求过于频繁" 错误，可以配置登录后的 SESSDATA。

## 获取 SESSDATA 方法

1. 登录 B 站 (bilibili.com)
2. 按 F12 打开开发者工具
3. 切换到 Application/Application 面板
4. 左侧找到 Cookies -> https://www.bilibili.com
5. 找到 SESSDATA 这一项，复制其值

## 配置方法

将复制的 SESSDATA 值保存到当前目录下的 `cookie.txt` 文件中（纯文本，不要有其他内容）。

例如：
```bash
# 创建 cookie.txt 文件，内容就是你的 SESSDATA 值
vi cookie.txt
# 粘贴后保存退出
```

配置好后，重新运行程序即可使用登录态请求，降低被限流的风险。
