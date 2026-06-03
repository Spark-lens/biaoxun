# biaoxun

知了标讯采集程序。

测试本机访问：https://www.zhiliaobiaoxun.com/search 是否出现 403 错误（大概率属于 ip 被封禁）

- 若未封禁，直接运行 main.py 直接运行
- 若封禁，使用脚本 run_with_proxy.bat 运行 main_proxy.py
 
当前实现支持在 Windows 本地运行 Selenium，并通过 SSH 动态 SOCKS5 隧道使用 10.27.6.149 的出口访问目标站点，从而绕过本机 IP 的 403 封禁。

## 运行前准备

1. 在 Windows 上先建立 SSH 动态转发隧道，保持这个窗口不要关闭。

	ssh -N -D 1080 root@10.27.6.149

	password: 

2. 建议先验证代理是否可用。

	curl.exe --socks5-hostname 127.0.0.1:1080 -I https://www.zhiliaobiaoxun.com/search

	期望返回 200 OK，而不是 403。

3. 在项目根目录准备 .env，至少包含以下变量：

	ZBX_USERNAME=你的账号
	ZBX_PASSWORD=你的密码

	如果启用邮件发送，还需要：

	SMTP_SENDER=你的发件邮箱
	SMTP_PASSWORD=你的邮箱授权码
	SMTP_RECEIVERS=收件人1,收件人2

	启用 SSH 代理时再加入：

	BIAOXUN_PROXY_ENABLED=true
	BIAOXUN_PROXY_SCHEME=socks5
	BIAOXUN_PROXY_HOST=127.0.0.1
	BIAOXUN_PROXY_PORT=1080
	BIAOXUN_PROXY_TIMEOUT=3

## 运行方式

1. 激活 Python 环境。

	conda activate sendBiaoXunEmail

2. 启动程序。

	python main.py

3. 如果你本机直连会 403，优先直接运行根目录下的 run_with_proxy.bat。

	run_with_proxy.bat

## 说明

- 代理默认关闭，不影响原有行为。
- 启用代理后，程序会在启动浏览器前检查本地 127.0.0.1:1080 是否可用；如果 SSH 隧道没开，会直接报错。
- 采集、下载、压缩包生成和邮件发送逻辑保持不变，变化只在于浏览器的网络出口。
