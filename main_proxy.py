import collections
import zipfile
import tarfile
import io
import os
import random
import subprocess
import socket
import sys
import re
import time
import pandas as pd

# 自动安装 py7zr（用于 7z 格式压缩）
try:
    import py7zr
except ImportError:
    print("py7zr 未安装，正在自动安装...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "py7zr", "-q"])
    import py7zr
    print("py7zr 安装完成")
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common import StaleElementReferenceException, TimeoutException
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
if sys.version_info >= (3, 10):
    collections.Callable = collections.abc.Callable
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service


def load_local_env(file_path=".env"):
    """轻量读取 .env 文件（不覆盖系统已存在环境变量）。"""
    if not os.path.exists(file_path):
        return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()

                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]

                os.environ.setdefault(key, value)
    except Exception as e:
        print(f"读取 .env 失败: {str(e)}")


load_local_env()



class GBKZipInfo(zipfile.ZipInfo):
    """
    重写 _encodeFilenameFlags，使 ZIP 文件名以 GBK 编码写入，
    不设置 UTF-8 标志位（bit 11），确保 Windows 内置解压工具
    能用系统默认编码（GBK/CP936）正确识别中文文件名，不报损坏。
    """
    def _encodeFilenameFlags(self):
        try:
            return self.filename.encode('ascii'), self.flag_bits
        except UnicodeEncodeError:
            # 使用 GBK 编码，不加 UTF-8 标志位 (0x800)
            return self.filename.encode('gbk', errors='replace'), self.flag_bits


class EnhancedBidSpider:
    def __init__(self):
        timestamp = datetime.   now().strftime("%Y%m%d")
        self.pdf_dir = os.path.join(os.getcwd(), f"招标文件包_{timestamp}")

        # 移除旧目录清理逻辑，直接创建新目录
        os.makedirs(self.pdf_dir, exist_ok=True)
        print(f"新建下载目录: {self.pdf_dir}")

        self.pdf_files = {}  # 存储结构改为 {标题: (文件名, 显示名称)}

        # 代理配置（默认关闭；启用后让 Chrome 走本地 SSH 动态转发）
        self.proxy_enabled = os.getenv("BIAOXUN_PROXY_ENABLED", "false").strip().lower() in (
            "1", "true", "yes", "on")
        self.proxy_scheme = os.getenv("BIAOXUN_PROXY_SCHEME", "socks5").strip().lower()
        self.proxy_host = os.getenv("BIAOXUN_PROXY_HOST", "127.0.0.1").strip() or "127.0.0.1"
        self.proxy_port = int(os.getenv("BIAOXUN_PROXY_PORT", "1080"))
        self.proxy_timeout = float(os.getenv("BIAOXUN_PROXY_TIMEOUT", "3"))

        if self.proxy_enabled:
            self._validate_proxy_tunnel()

        # 初始化浏览器驱动
        self.driver = self._init_browser()
        # 初始化显式等待对象，最长等待时间为25秒
        self.wait = WebDriverWait(self.driver, 25)
        # 用于存储所有采集到的数据
        self.all_data = []
        # 登录使用的用户名和密码（从环境变量读取）
        self.username = os.getenv("ZBX_USERNAME", "").strip()
        self.password = os.getenv("ZBX_PASSWORD", "").strip()
        # 最小金额阈值
        self.MIN_AMOUNT = 40_0000
        # 去重
        self.seen_titles = set()  # 新增：用于存储已出现的标题
        # 压缩包路径
        self.zip_file_path = ""
        self.tar_file_path = ""
        self.sevenz_file_path = ""

        smtp_receivers = [
            x.strip() for x in os.getenv("SMTP_RECEIVERS", "").split(",") if x.strip()
        ]

        # 邮件配置信息（从环境变量读取）
        self.email_config = {
            'smtp_server': os.getenv('SMTP_SERVER', 'smtp.feishu.cn').strip(),  # SMTP服务器地址
            'smtp_port': int(os.getenv('SMTP_PORT', '465')),  # SSL端口号
            'sender': os.getenv('SMTP_SENDER', '').strip(),  # 发件邮箱地址
            'password': os.getenv('SMTP_PASSWORD', '').strip(),  # 邮箱授权码
            # 收件人列表
            'receivers': smtp_receivers,
            'send_email': os.getenv('SEND_EMAIL', 'true').strip().lower() in ('1', 'true', 'yes', 'on')  # 是否发送邮件的开关
        }

        self.keywords = [
            "备份", "灾备集成", "灾切", "容灾", "人力外包", "人力资源池入围",
            "科技人力外包", "IT 驻场服务", "信息技术外协", "信息化人力服务",
            "技术派驻服务", "第三方 IT 人力", "项目技术外包"
        ]
        # self.keyword_filter_enabled = True
        self.keyword_filter_enabled = False

        self._validate_sensitive_config()

    def _validate_sensitive_config(self):
        """校验关键环境变量，避免因漏配导致运行中异常。"""
        missing = []
        if not self.username:
            missing.append("ZBX_USERNAME")
        if not self.password:
            missing.append("ZBX_PASSWORD")

        if self.email_config['send_email']:
            if not self.email_config['sender']:
                missing.append("SMTP_SENDER")
            if not self.email_config['password']:
                missing.append("SMTP_PASSWORD")
            if not self.email_config['receivers']:
                missing.append("SMTP_RECEIVERS")

        if missing:
            raise ValueError("缺少必要环境变量: " + ", ".join(missing))

    def _get_proxy_server_url(self):
        if not self.proxy_enabled:
            return None
        return f"{self.proxy_scheme}://{self.proxy_host}:{self.proxy_port}"

    def _is_proxy_port_open(self):
        try:
            with socket.create_connection((self.proxy_host, self.proxy_port), timeout=self.proxy_timeout):
                return True
        except OSError:
            return False

    def _validate_proxy_tunnel(self):
        """在启动浏览器前检查本地 SSH 动态转发是否可用。"""
        if not self.proxy_enabled:
            return

        if self.proxy_scheme not in ("socks5", "http"):
            raise ValueError(f"不支持的代理协议: {self.proxy_scheme}，请使用 socks5 或 http")

        if not (1 <= self.proxy_port <= 65535):
            raise ValueError(f"代理端口无效: {self.proxy_port}")

        if not self._is_proxy_port_open():
            raise ConnectionError(
                f"代理不可用: {self.proxy_scheme}://{self.proxy_host}:{self.proxy_port}。"
                f"请先在 Windows 上建立 SSH 动态转发，例如: ssh -N -D {self.proxy_port} root@10.27.6.149"
            )

        print(f"代理已就绪: {self.proxy_scheme}://{self.proxy_host}:{self.proxy_port}")

    def _build_archive_attachment(self, file_path):
        """将本地压缩文件封装为 MIMEApplication 附件对象"""
        with open(file_path, 'rb') as f:
            part = MIMEApplication(f.read())
        part.add_header('Content-Disposition', 'attachment',
                        filename=os.path.basename(file_path))
        return part

    def _send_email(self, attachment_path):
        """
        发送两封邮件：
          邮件一：Excel 数据表 + ZIP 压缩包
          邮件二：TAR.GZ 压缩包（默认）；
                  若 TAR.GZ 文件超过 MAX_ATTACH_MB，则改发 7Z（压缩率更高体积更小）；
                  7Z 文件始终仅保留在本地。
        飞书 SMTP 单封限制约 30 MB；Base64 编码后体积膨胀 ~37%，
        故以原始文件 20 MB 作为安全阈值。
        """
        if not self.email_config['send_email']:
            print("邮件发送功能已禁用")
            return

        MAX_ATTACH_MB = 28          # 附件原始大小安全阈值（MB）
        date_str = datetime.now().strftime('%Y-%m-%d')
        time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        receivers = self.email_config['receivers']

        # ===== 邮件一：Excel + ZIP =====
        msg1 = MIMEMultipart()
        msg1['From'] = self.email_config['sender']
        msg1['To'] = ",".join(receivers)
        msg1['Subject'] = f"【1/2】招标数据报告 {date_str}（Excel + ZIP）"
        body1 = f"""
        <h3>招标数据采集完成（第 1 封，共 2 封）</h3>
        <p>采集时间：{time_str}</p>
        <p>数据总量：{len(self.all_data)} 条</p>
        <p>日期范围：{self.start_date} 至 {self.end_date}</p>
        <p>本封附件：Excel 数据表格 + ZIP 招标文件压缩包</p>
        <p>第 2 封附件：TAR.GZ 压缩包（若超限则为 7Z）</p>
        """
        msg1.attach(MIMEText(body1, 'html', 'utf-8'))
        try:
            with open(attachment_path, 'rb') as f:
                excel_part = MIMEApplication(f.read())
            excel_part.add_header('Content-Disposition', 'attachment',
                                  filename=os.path.basename(attachment_path))
            msg1.attach(excel_part)
            print(f"成功附加Excel文件：{os.path.basename(attachment_path)}")
        except Exception as e:
            print(f"附加Excel失败: {str(e)}")
        if self.zip_file_path and os.path.exists(self.zip_file_path):
            try:
                msg1.attach(self._build_archive_attachment(self.zip_file_path))
                print(f"成功附加ZIP文件：{os.path.basename(self.zip_file_path)}")
            except Exception as e:
                print(f"附加ZIP失败: {str(e)}")
        else:
            print("ZIP文件不存在，跳过")

        # ===== 选择第二封邮件的附件：默认 TAR.GZ，超限则回退到 7Z =====
        tar_ok  = self.tar_file_path and os.path.exists(self.tar_file_path)
        sevz_ok = self.sevenz_file_path and os.path.exists(self.sevenz_file_path)

        # 判断 TAR.GZ 是否超过安全阈值
        tar_size_mb = os.path.getsize(self.tar_file_path) / 1024 / 1024 if tar_ok else 0
        use_tar = tar_ok and (tar_size_mb <= MAX_ATTACH_MB)

        if use_tar:
            arch2_path  = self.tar_file_path
            arch2_label = "TAR.GZ"
            print(f"TAR.GZ 大小 {tar_size_mb:.1f} MB，在安全阈值内，将用于第二封邮件")
        elif sevz_ok:
            arch2_path  = self.sevenz_file_path
            arch2_label = "7Z"
            print(f"TAR.GZ 大小 {tar_size_mb:.1f} MB，超出 {MAX_ATTACH_MB} MB 阈值，"
                  f"回退为 7Z 附件")
        else:
            arch2_path  = None
            arch2_label = ""
            print("TAR.GZ 和 7Z 文件均不可用，将跳过第二封邮件")

        # ===== 邮件二：TAR.GZ（或 7Z） =====
        msg2 = None
        if arch2_path:
            msg2 = MIMEMultipart()
            msg2['From'] = self.email_config['sender']
            msg2['To'] = ",".join(receivers)
            msg2['Subject'] = f"【2/2】招标数据报告 {date_str}（{arch2_label} 压缩包）"
            body2 = f"""
            <h3>招标数据采集完成（第 2 封，共 2 封）</h3>
            <p>采集时间：{time_str}</p>
            <p>日期范围：{self.start_date} 至 {self.end_date}</p>
            <p>本封附件：{arch2_label} 招标文件压缩包（与第 1 封 ZIP 内容相同）</p>
            """
            msg2.attach(MIMEText(body2, 'html', 'utf-8'))
            try:
                msg2.attach(self._build_archive_attachment(arch2_path))
                print(f"成功附加{arch2_label}文件：{os.path.basename(arch2_path)}")
            except Exception as e:
                print(f"附加{arch2_label}失败: {str(e)}")
                msg2 = None

        # ===== 统一建立 SMTP 连接，依次发送两封 =====
        try:
            with smtplib.SMTP_SSL(self.email_config['smtp_server'],
                                  self.email_config['smtp_port']) as server:
                server.login(self.email_config['sender'],
                             self.email_config['password'])

                server.send_message(msg1)
                print(f"第一封邮件发送成功（Excel + ZIP），收件人：{receivers}")

                if msg2:
                    server.send_message(msg2)
                    print(f"第二封邮件发送成功（{arch2_label}），收件人：{receivers}")

        except Exception as e:
            print(f"邮件发送失败: {str(e)}")

    def _create_pdf_zip(self):
        """创建PDF文件压缩包"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d")
            zip_filename = f"招标文件包_{timestamp}.zip"
            self.zip_file_path = os.path.join(os.getcwd(), zip_filename)

            # 创建压缩文件（使用 GBKZipInfo 保证中文文件名兼容 Windows 内置解压）
            with zipfile.ZipFile(self.zip_file_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                pdf_count = 0
                # 遍历PDF目录中的所有PDF文件
                for root, _, files in os.walk(self.pdf_dir):
                    for file in files:
                        if file.endswith('.pdf'):
                            file_path = os.path.join(root, file)
                            arcname = os.path.basename(file_path)
                            # 用 GBKZipInfo 写入，保持中文文件名且不设 UTF-8 标志位
                            zinfo = GBKZipInfo(arcname)
                            zinfo.compress_type = zipfile.ZIP_DEFLATED
                            with open(file_path, 'rb') as f:
                                zipf.writestr(zinfo, f.read())
                            pdf_count += 1

            print(f"PDF压缩完成，共{pdf_count}个文件，保存为：{self.zip_file_path}")
            return self.zip_file_path

        except Exception as e:
            print(f"创建PDF压缩包失败: {str(e)}")
            return None

    def _create_tar_gz(self):
        """创建 tar.gz 压缩包（使用 Python 标准库 tarfile）"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d")
            tar_filename = f"招标文件包_{timestamp}.tar.gz"
            self.tar_file_path = os.path.join(os.getcwd(), tar_filename)

            with tarfile.open(self.tar_file_path, 'w:gz') as tar:
                pdf_count = 0
                for root, _, files in os.walk(self.pdf_dir):
                    for file in files:
                        if file.endswith('.pdf'):
                            file_path = os.path.join(root, file)
                            tar.add(file_path, arcname=file)
                            pdf_count += 1

            print(f"TAR.GZ 压缩完成，共{pdf_count}个文件，保存为：{self.tar_file_path}")
            return self.tar_file_path

        except Exception as e:
            print(f"创建 TAR.GZ 压缩包失败: {str(e)}")
            return None

    def _create_7z(self):
        """创建 7z 压缩包（使用 py7zr）"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d")
            sevenz_filename = f"招标文件包_{timestamp}.7z"
            self.sevenz_file_path = os.path.join(os.getcwd(), sevenz_filename)

            pdf_count = 0
            with py7zr.SevenZipFile(self.sevenz_file_path, mode='w') as szf:
                for root, _, files in os.walk(self.pdf_dir):
                    for file in files:
                        if file.endswith('.pdf'):
                            file_path = os.path.join(root, file)
                            szf.write(file_path, arcname=file)
                            pdf_count += 1

            print(f"7Z 压缩完成，共{pdf_count}个文件，保存为：{self.sevenz_file_path}")
            return self.sevenz_file_path

        except Exception as e:
            print(f"创建 7Z 压缩包失败: {str(e)}")
            return None

    def _force_login(self):
        print("触发登录弹窗...")
        try:
            self.driver.execute_script("document.body.style.zoom = '75%'")

            login_selectors = [
                (By.CSS_SELECTOR, "span.loginBt"),
                (By.CSS_SELECTOR, "div.loginCon span.loginBt"),
                (By.XPATH, "//span[contains(@class,'loginBt')]")
            ]
            login_span = None
            last_login_error = None
            for by, selector in login_selectors:
                try:
                    login_span = WebDriverWait(self.driver, 10).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    break
                except Exception as e:
                    last_login_error = e

            if not login_span:
                raise last_login_error or TimeoutException("未找到登录入口")

            # 使用JavaScript点击登录按钮，避免被浮层或样式影响
            self.driver.execute_script("arguments[0].click();", login_span)
            print("已点击登录按钮")

            # 等待实际可见的登录弹窗出现
            self.wait.until(
                EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, "div.dialog-login-model, div.login-wrap")
                )
            )
            time.sleep(1)

            # 等待标签列表出现，然后优先点击“密码登录”
            tab_items = self.wait.until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.tabs .tab"))
            )
            password_tab = None
            for tab in tab_items:
                if "密码登录" in tab.text:
                    password_tab = tab
                    break
            if password_tab is None and len(tab_items) >= 2:
                password_tab = tab_items[1]
            if password_tab is None:
                raise TimeoutException("未找到密码登录标签")

            # 点击密码登录标签
            self.driver.execute_script("arguments[0].click();", password_tab)
            print("已切换到密码登录")

            # 输入用户名和密码
            self._input_credentials()
            # 提交登录表单
            self._submit_login()

        except Exception as e:
            print(f"登录失败: {str(e)}")
            # 保存登录失败的截图
            self.driver.save_screenshot("login_error.png")
            # 抛出异常
            raise

    def _input_credentials(self):
        """
        输入登录凭证（用户名和密码）
        """
        try:
            # 等待用户名输入框可见
            username_input = self.wait.until(
                EC.visibility_of_element_located(
                    (By.XPATH,
                     '//div[contains(@class, "el-dialog__wrapper")]//input[@placeholder="请输入手机号或邮箱"]')
                )
            )
            # 等待密码输入框可见
            password_input = self.wait.until(
                EC.visibility_of_element_located(
                    (By.XPATH,
                     '//div[contains(@class, "el-dialog__wrapper")]//input[@placeholder="请输入密码"]')
                )
            )

            # 模拟人类输入用户名
            self._human_type(username_input, self.username)
            # 模拟人类输入密码
            self._human_type(password_input, self.password)

        except Exception as e:
            print(f"输入凭证失败: {str(e)}")
            # 抛出异常
            raise

    def _submit_login(self):
        """
        提交登录表单
        """
        try:
            # 等待登录/注册按钮可点击
            submit_btn = self.wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH,
                     '//button[contains(@class,"el-button--primary")]//span[contains(text(), "立即登录")]/..')
                )
            )
            # 使用JavaScript点击登录/注册按钮
            self.driver.execute_script("arguments[0].click();", submit_btn)
            print("已提交登录表单")

            # 等待登录弹窗消失
            self.wait.until(
                EC.invisibility_of_element_located(
                    (By.XPATH, '//div[contains(@class, "el-dialog__wrapper")]')
                )
            )

        except Exception as e:
            print(f"提交登录失败: {str(e)}")
            # 抛出异常
            raise

    def _human_type(self, element, text):
        """
        模拟人类输入文本
        :param element: 输入框元素
        :param text: 要输入的文本
        """
        # 清空输入框
        element.clear()
        # 逐个字符输入文本，并随机间隔一段时间
        for char in text:
            element.send_keys(char)
            time.sleep(random.uniform(0.05, 0.2))

    def _init_browser(self):
        """
        初始化浏览器驱动
        :return: 初始化后的浏览器驱动对象
        """
        # 创建Chrome浏览器选项对象（只创建一次，避免覆盖已设置的参数）
        options = webdriver.ChromeOptions()
        # 启动时最大化窗口
        options.add_argument("--start-maximized")
        # 禁用GPU加速
        options.add_argument("--disable-gpu")
        # 禁用沙箱模式
        options.add_argument("--no-sandbox")
        # 禁用/dev/shm使用
        options.add_argument("--disable-dev-shm-usage")
        # 如启用代理，则让 Chrome 通过本地 SSH 动态转发访问目标网站
        proxy_server_url = self._get_proxy_server_url()
        if proxy_server_url:
            options.add_argument(f"--proxy-server={proxy_server_url}")
            print(f"Chrome 代理参数已启用: {proxy_server_url}")
        # 设置远程调试端口
        options.add_argument("--remote-debugging-port=9222")
        # 设置用户代理
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
        # 禁用自动化控制特征，避免被网站检测为爬虫
        options.add_argument("--disable-blink-features=AutomationControlled")
        # 隐藏 Selenium 自动化标志
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        prefs = {
            "download.default_directory": self.pdf_dir,  # 指定下载目录
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True
        }
        options.add_experimental_option("prefs", prefs)

        # 使用 webdriver-manager 自动下载和管理 ChromeDriver
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        # 通过 JS 覆盖 navigator.webdriver 属性，进一步规避检测
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
        return driver

    def _smart_wait(self, selector):
        """
        智能等待元素出现，如果等待超时则刷新页面并重试
        :param selector: CSS选择器
        :return: 匹配的元素列表
        """
        try:
            # 等待元素出现
            return self.wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, selector)))
        except:
            # 刷新页面
            self.driver.refresh()
            # 等待5秒
            time.sleep(5)
            # 再次等待元素出现
            return self.wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, selector)))

    def _matches_keywords(self, title, products):
        text = f"{title} {products or ''}"
        normalized = self._normalize_text_for_matching(text)
        matched = []
        for kw in self.keywords:
            normalized_kw = self._normalize_text_for_matching(kw)
            if normalized_kw in normalized:
                matched.append(kw)
        return matched

    def _click_prerequisite_elements(self):
        """
        点击设置条件组相关元素
        :return: 设置成功返回True，失败返回False
        """
        try:
            print("开始设置条件组...")
            # 等待展开条件面板的图标可点击
            expand_icon = self.wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "i.el-icon-caret-right")
                )
            )
            # 使用JavaScript点击展开图标
            self.driver.execute_script("arguments[0].click();", expand_icon)
            print("已展开条件面板")
            # 等待3秒确保面板展开
            time.sleep(3)

            # 等待条件组元素出现
            condition_element = self.wait.until(
                EC.presence_of_element_located(
                    (By.XPATH,
                     "//div[contains(@class,'savedname')]/h3[text()='week']/..")
                )
            )
            # 使用JavaScript点击条件组元素
            self.driver.execute_script(
                "arguments[0].click();", condition_element)
            print("已选择条件组week")
            # 等待3秒确保条件组加载完成
            time.sleep(3)
            return True

        except Exception as e:
            print(f"条件设置失败: {str(e)}")
            return False

    # 三重验证条件是否生效
    def _validate_condition_applied(self):
        """三重验证条件是否应用成功"""
        try:
            # 修改验证条件中的文本匹配
            condition_tag = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//span[contains(text(),'条件组22')]"))
            )
            # ... 其他验证逻辑保持不变 ...
            print("三重验证通过")
            return True
        except TimeoutException:
            print("条件应用验证超时")
            return False

    # 状态恢复
    def _recover_condition_state(self):
        """
        恢复页面状态
        """
        print("执行状态恢复...")
        try:
            # 尝试关闭已展开的面板
            active_icon = self.driver.find_element(
                By.CSS_SELECTOR, "i.el-icon-caret-right.is-active")
            self.driver.execute_script("arguments[0].click();", active_icon)
            time.sleep(0.5)
        except:
            pass

        # 轻量级刷新（保持登录状态）
        self.driver.execute_script("location.reload()")
        # 等待结果列表出现
        WebDriverWait(self.driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "ul.result-ul"))
        )
        print("页面状态已恢复")

    # 分页
    def _safe_pagination(self):
        """
        增强版安全翻页操作（解决元素过期/窗口卡死问题）
        改进点：
        1. 增加页面加载状态验证
        2. 优化元素定位方式
        3. 添加窗口状态恢复机制
        4. 增强异常处理流程
        :return: 翻页成功返回True，失败返回False
        """
        max_retries = 5  # 增加最大重试次数
        retry_delay = 3  # 重试间隔(秒)
        last_exception = None

        for attempt in range(1, max_retries + 1):
            try:
                # === 阶段1：预检页面状态 ===
                # 等待页面完全加载
                self.wait.until(
                    lambda d: d.execute_script(
                        "return document.readyState === 'complete'")
                )

                # 关闭可能存在的遮挡元素
                self._close_obstructing_elements()

                # === 阶段2：定位分页元素 ===
                # 使用更可靠的分页容器定位方式
                pagination = WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR,
                         ".el-pagination:not([style*='display:none'])")
                    )
                )

                # 动态定位下一页按钮（避免元素过期）
                next_btn = WebDriverWait(pagination, 10).until(
                    EC.element_to_be_clickable(
                        (By.CSS_SELECTOR, "button.btn-next:not(.disabled)")
                    )
                )

                # 移除可能导致问题的刷新操作
                # self.driver.execute_script("window.stop();")  # 停止当前加载
                # self.driver.refresh()
                # WebDriverWait(self.driver, 15).until(
                #     EC.presence_of_element_located((By.CSS_SELECTOR, "div.main-container"))
                # )

                # === 阶段3：执行翻页操作 ===
                # 滚动到分页区域（带视觉对齐）
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({behavior: 'auto', block: 'center', inline: 'center'});",
                    pagination
                )
                time.sleep(0.5)  # 确保滚动完成

                # 使用更可靠的点击方式
                try:
                    # 先尝试直接点击
                    next_btn.click()
                except:
                    # 如果直接点击失败，使用JavaScript点击
                    self.driver.execute_script(
                        "arguments[0].click();", next_btn)

                # === 阶段4：验证翻页结果 ===
                # 等待旧内容消失
                WebDriverWait(self.driver, 20).until(
                    EC.invisibility_of_element_located(
                        (By.CSS_SELECTOR, "div.loading-mask")
                    )
                )

                # 等待新内容加载（双重验证）
                WebDriverWait(self.driver, 25).until(
                    EC.presence_of_all_elements_located(
                        (By.CSS_SELECTOR, "div.biditem"))
                )
                WebDriverWait(self.driver, 15).until(
                    lambda d: len(d.find_elements(
                        By.CSS_SELECTOR, "div.biditem")) > 0
                )

                print(f"第{attempt}次翻页成功")
                return True

            except StaleElementReferenceException as e:
                last_exception = e
                print(f"元素状态过期，正在重试({attempt}/{max_retries})")
                self.driver.refresh()
                time.sleep(retry_delay)

            except TimeoutException as e:
                last_exception = e
                print(f"等待超时，尝试恢复状态({attempt}/{max_retries})")
                self._recover_page_state()
                time.sleep(retry_delay)

            except Exception as e:
                last_exception = e
                print(f"翻页异常 [{type(e).__name__}]: {str(e)}")
                self.driver.save_screenshot(
                    f"page_error_{int(time.time())}.png")

                # 关键恢复操作
                if attempt < max_retries:
                    self._restore_window_focus()
                    self._recover_page_state()
                    time.sleep(retry_delay)

        # 所有重试失败后处理
        print(f"连续{max_retries}次尝试失败，最后错误：{str(last_exception)}")
        return False

    def _recover_page_state(self):
        """恢复页面基础状态"""
        print("执行页面状态恢复...")
        try:
            # 尝试返回原始页面
            if len(self.driver.window_handles) > 1:
                self.driver.switch_to.window(self.driver.window_handles[0])

            # 清除可能存在的悬浮菜单
            self.driver.execute_script(
                "document.body.style.pointerEvents = 'auto';")

            # 基础刷新
            self.driver.execute_script("location.reload()")

            # 等待页面加载完成
            self.wait.until(
                lambda d: d.execute_script(
                    "return document.readyState === 'complete'")
            )

            # 等待主容器出现
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "div.main-container"))
            )

            # 等待数据项出现
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_all_elements_located(
                    (By.CSS_SELECTOR, "div.biditem"))
            )

            print("页面状态已恢复")
        except Exception as e:
            print(f"页面恢复失败: {str(e)}")

    def _close_obstructing_elements(self):
        """关闭可能遮挡操作的弹窗/广告"""
        try:
            close_btns = self.driver.find_elements(
                By.CSS_SELECTOR,
                "div.popup-close, span.close-btn, i.close-icon"
            )
            for btn in close_btns[-3:]:  # 只处理最近3个元素
                try:
                    btn.click()
                    print("已关闭遮挡元素")
                    time.sleep(0.5)
                except:
                    pass
        except:
            pass

    def _restore_window_focus(self):
        """恢复窗口焦点"""
        try:
            self.driver.switch_to.window(self.driver.window_handles[0])
            self.driver.execute_script("window.focus();")
        except:
            pass

    def crawl(self):
        try:
            print("正在初始化浏览器...")
            if self.proxy_enabled:
                self._validate_proxy_tunnel()
            # 打开指定的搜索页面
            self.driver.get(
                "https://www.zhiliaobiaoxun.com/search")        # 强制登录操作
            self._force_login()
            time.sleep(5)
            # 增强的关闭按钮处理逻辑
            max_close_retries = 1
            for attempt in range(max_close_retries):
                try:
                    close_btn = WebDriverWait(self.driver, 10).until(
                        EC.element_to_be_clickable(
                            (By.CSS_SELECTOR, "span.closebtn"))
                    )
                    self.driver.execute_script(
                        "arguments[0].click();", close_btn)
                    print(f"第{attempt + 1}次尝试关闭弹窗成功")
                    break
                except Exception as close_e:
                    print(
                        f"关闭按钮处理失败（尝试 {attempt + 1}/{max_close_retries}）: {str(close_e)}")
                    if attempt == max_close_retries - 1:
                        print("达到最大重试次数，跳过关闭按钮操作")
                    time.sleep(1)
            # 等待5秒，确保页面加载和登录操作完成
            time.sleep(1)

            if self._click_prerequisite_elements():
                print("条件组设置完成，开始采集...")
            else:
                print("条件组设置失败，尝试继续采集...")
            # 等待5秒，确保条件组设置操作完成
            time.sleep(5)
            # 精确等待列表容器加载
            print("等待数据容器加载...")
            self.wait.until(
                EC.presence_of_all_elements_located(
                    (By.CSS_SELECTOR, "div.biditem")
                )
            )
            print("数据容器加载确认")

            # 修正日期范围计算
            today = datetime.now().date()
            if today.weekday() == 0:  # 周一
                self.start_date = today - timedelta(days=3)  # 上周五
                self.end_date = today - timedelta(days=1)  # 周日
            elif today.weekday() == 2:  # 周三
                self.start_date = today - timedelta(days=2)  # 周一
                self.end_date = today - timedelta(days=1)  # 周二
            elif today.weekday() == 4:  # 周五
                self.start_date = today - timedelta(days=2)  # 周三
                self.end_date = today - timedelta(days=1)  # 周四
            else:  # 其他时间
                # self.start_date = today - timedelta(days=5)
                # self.end_date = today - timedelta(days=1)  # 关键修改：总是排除今天
                self.start_date = today - timedelta(days=4)
                self.end_date = today - timedelta(days=1)  # 关键修改：总是排除今天

            print(f"采集日期范围：{self.start_date} 至 {self.end_date}（排除今天）")

            # 主采集循环
            page = 1

            while True:
                print(f"正在处理第 {page} 页...")
                page_start_count = len(self.all_data)  # 记录处理前数据量

                # 解析页面并判断是否需要全局停止
                should_continue = self._parse_page()
                if not should_continue:
                    print("触发全局停止条件，终止采集")
                    break

                # 分页终止条件判断
                if page >= 50:
                    print("达到最大页数限制")
                    break

                if not self._safe_pagination():
                    print("已到最后一页")
                    break

                page += 1
                time.sleep(3)  # 增加翻页间隔

        except Exception as e:
            print(f"核心错误: {str(e)}")
            # 保存错误截图
            self.driver.save_screenshot("fatal_error.png")
        finally:
            # 关闭浏览器驱动
            self.driver.quit()
            # 保存采集到的数据
            self._save_data()

    def _parse_page(self):
        """增强版页面解析（精确控制采集流程）"""
        try:
            # 等待数据加载完成，且加载指示器消失
            WebDriverWait(self.driver, 15).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, "div.biditem") and
                not d.find_elements(By.CSS_SELECTOR, ".loading-indicator")
            )

            # 获取当前页面的HTML源代码
            html = self.driver.page_source
            # 使用BeautifulSoup解析HTML
            soup = BeautifulSoup(html, 'lxml')
            # 选择所有的招标项目元素
            items = soup.select('div.biditem')
            # 全局停止标志
            should_stop_crawl = False
            # 页面是否有有效数据标志
            page_has_valid_data = False

            valid_methods = ["公开招标", "邀请招标", "竞争性谈判", "询比价",
                             "竞争性磋商", "竞价", "单一来源", "网上直购", "定点采购"]

            for idx, item in enumerate(items):
                try:
                    # 提取基础数据（带异常保护）
                    pub_element = item.select_one('.brandInfo-date')
                    money_element = item.select_one('.brandInfo-money')

                    # 关键修复：确保基础数据存在
                    if not all([pub_element, money_element]):
                        print(f"第{idx + 1}条数据缺失关键字段，跳过")
                        continue

                    # 提取发布时间和预算金额文本
                    pub_time = pub_element.text.strip()
                    money_text = money_element.text.strip()

                    # 日期有效性校验
                    try:
                        pub_date = datetime.strptime(
                            pub_time, "%Y-%m-%d").date()
                    except ValueError:
                        print(f"异常日期格式：{pub_time}，跳过该条目")
                        continue

                    # 当发现有效数据时标记（移动到此处）
                    if self.start_date <= pub_date <= self.end_date:
                        page_has_valid_data = True

                    # 遇到早于开始日期的数据立即停止整个采集
                    if pub_date < self.start_date:
                        print(
                            f"发现历史数据 {pub_time}，早于采集范围开始日期 {self.start_date}，停止采集")
                        should_stop_crawl = True
                        break

                    # 排除当天及未来日期
                    if pub_date >= datetime.now().date():
                        print(f"[第{idx + 1}条] 跳过当天数据：{pub_time}")
                        continue

                    # 检查是否在目标日期范围内
                    if not (self.start_date <= pub_date <= self.end_date):
                        print(f"[第{idx + 1}条] 非目标范围数据：{pub_time}")
                        continue

                    # ============ 新增：投标状态过滤 ============
                    bids_time = item.select_one('.bidsTime')
                    if bids_time and '投标已截止' in bids_time.get_text(strip=True):
                        print(f"第{idx + 1}条数据已截止，跳过")
                        continue

                    # 精准定位招标方式
                    bid_method_text = "其他"  # 默认值

                    # 步骤1：定位到目标容器
                    target_div = item.select_one('div.biddetailinfo')
                    valid_methods = ["公开招标", "邀请招标", "竞争性谈判", "询比价",
                                     "竞争性磋商", "竞价", "单一来源", "网上直购", "定点采购"]
                    if target_div:
                        # 步骤2：在该容器内查找所有subStatus元素
                        method_spans = target_div.select('span.subStatus')
                        # 步骤3：遍历查找有效招标方式
                        for span in method_spans:
                            method_text = span.get_text(strip=True)
                            if method_text in valid_methods:
                                bid_method_text = method_text
                                break  # 找到第一个有效项即停止

                    # --- 完整数据提取 ---
                    raw_data = {
                        'titleText': item.select_one('.biditem-title .name').text if item.select_one('.biditem-title .name') else "",
                        'bidMethod': bid_method_text,
                        'products': [tag.get_text(strip=True) for tag in item.select('.tags-wrap .tag-text')],
                        'money_text': money_text,
                        'money_value': None,
                        'caller': item.select_one('.companyinfo-name').text if item.select_one(
                            '.companyinfo-name') else "",
                        'pubTime': pub_time,
                        'province': item.select_one('.brandInfo-area').text.split()[0] if item.select_one(
                            '.brandInfo-area') else "",
                        'bidStatus': self._parse_status(item),
                        'signupTime': self._parse_deadline(item, 'signup'),
                        'tenderTime': self._parse_deadline(item, 'tender')
                    }

                    # 金额解析
                    raw_text, parsed_value = self._parse_money(
                        raw_data['money_text'])
                    raw_data['money_text'] = raw_text
                    raw_data['money_value'] = parsed_value

                    # 金额过滤（保留空值或大于40万）
                    if raw_data['money_value'] is not None and 0 <= raw_data['money_value'] <= self.MIN_AMOUNT:
                        print(f"过滤金额数据：{raw_data['money_text']}")
                        continue

                    # 状态判断（基于剩余逻辑）
                    status = "进行中"  # 默认状态
                    if bids_time:
                        if '剩余' in bids_time.text:
                            days = int(bids_time.select_one('.days').text)
                            status = "进行中" if days > 0 else "已截止"
                        else:
                            status = "未开始"
                    # 数据标准化
                    processed = {
                        "招标方式": self._clean_text(bid_method_text[:10]),
                        "公告标题": self._clean_text(self.clean_html(raw_data['titleText'])),
                        "附件": "",  # 先添加占位符，后面会更新
                        "产品": "、".join([self._clean_text(self.clean_html(p)) for p in raw_data['products']]),
                        "预算金额": raw_data['money_text'],
                        "招标公司": self._clean_text(self.clean_html(raw_data['caller'])),
                        "发布时间": self.format_date(raw_data['pubTime']),
                        "省份": self._clean_text(raw_data['province']),
                        "招标状态": status,
                        "标书获取截止时间": raw_data['signupTime'],
                        "投标截止时间": raw_data['tenderTime']
                    }

                    if self.keyword_filter_enabled:
                        if not self._matches_keywords(processed["公告标题"], processed["产品"]):
                            continue

                    # 去重逻辑
                    clean_title = processed["公告标题"].strip()
                    if not clean_title:
                        print(f"第{idx + 1}条数据标题为空，跳过")
                        continue

                    if clean_title in self.seen_titles:
                        print(f"发现重复标题：{clean_title[:20]}...，跳过")
                        continue

                    self.seen_titles.add(clean_title)

                    # 添加数据前的最后校验
                    if status == "已截止":
                        print(f"动态校验发现已截止项目: {raw_data['titleText']}")
                        continue

                    # 将处理后的数据添加到总数据列表中
                    self.all_data.append(processed)

                    # 点击进入详情页并导出PDF
                    try:
                        title_elements = self.driver.find_elements(
                            By.CSS_SELECTOR, ".biditem-title .name")
                        if idx < len(title_elements):
                            title_link = title_elements[idx]

                            # 新增：记录原始窗口句柄
                            main_window = self.driver.current_window_handle

                            # 使用新标签页打开（防止窗口切换失败）
                            self.driver.execute_script(
                                "arguments[0].target = '_blank';", title_link)
                            self.driver.execute_script(
                                "arguments[0].click();", title_link)

                            try:
                                # 窗口切换保护（15秒超时）
                                WebDriverWait(self.driver, 15).until(
                                    EC.number_of_windows_to_be(2))
                                new_window = [
                                    w for w in self.driver.window_handles if w != main_window][0]
                                self.driver.switch_to.window(new_window)

                                # 获取标书获取截止时间和投标截止时间
                                try:
                                    # 增加等待时间，确保页面完全加载
                                    WebDriverWait(self.driver, 15).until(
                                        EC.presence_of_element_located(
                                            (By.CSS_SELECTOR, "div.bidInfo"))
                                    )

                                    # 先截屏，方便调试
                                    screenshot_path = os.path.join(
                                        self.pdf_dir, f"detail_{int(time.time())}.png")
                                    self.driver.save_screenshot(
                                        screenshot_path)

                                    # ===== 标书获取截止时间 - 多种选择器尝试 =====
                                    get_bid_deadline = None

                                    # 方法1: 带data-v属性的标准结构
                                    selectors_bid_get = [
                                        "//li//p[contains(@class, 'title') and contains(text(), '标书获取') and contains(text(), '截止时间')]/following-sibling::p[contains(@class, 'name')]",
                                        "//li//p[contains(text(), '标书获取截止时间')]/following-sibling::p",
                                        "//div[contains(text(), '标书获取截止时间')]/../following-sibling::div",
                                        "//li[.//p[contains(text(), '标书获取') and contains(text(), '截止')]]//p[contains(@class, 'name')]",
                                        "//li[contains(.,'标书获取截止时间')]//p[last()]"
                                    ]

                                    for selector in selectors_bid_get:
                                        elements = self.driver.find_elements(
                                            By.XPATH, selector)
                                        if elements:
                                            temp_text = elements[0].text.strip(
                                            )
                                            if temp_text and temp_text != "-" and re.match(r'\d{4}-\d{2}-\d{2}', temp_text):
                                                get_bid_deadline = temp_text
                                                print(
                                                    f"✓ 获取到标书获取截止时间: {get_bid_deadline} (选择器: {selector})")
                                                break

                                    # 如果上面的方法都失败，尝试直接搜索全页面文本
                                    if not get_bid_deadline:
                                        page_source = self.driver.page_source
                                        bid_get_matches = re.search(
                                            r'标书获取截止时间</p>\s*<p[^>]*>([^<]+)</p>', page_source)
                                        if bid_get_matches:
                                            temp_text = bid_get_matches.group(
                                                1).strip()
                                            if temp_text and temp_text != "-" and re.match(r'\d{4}-\d{2}-\d{2}', temp_text):
                                                get_bid_deadline = temp_text
                                                print(
                                                    f"✓ 通过正则获取到标书获取截止时间: {get_bid_deadline}")

                                    # 更新数据
                                    if get_bid_deadline:
                                        processed["标书获取截止时间"] = get_bid_deadline

                                    # ===== 投标截止时间 - 多种选择器尝试 =====
                                    bid_deadline = None

                                    selectors_bid_deadline = [
                                        "//li//p[contains(@class, 'title') and contains(text(), '投标截止时间')]/following-sibling::p[contains(@class, 'name')]",
                                        "//li//p[contains(text(), '投标截止时间')]/following-sibling::p",
                                        "//div[contains(text(), '投标截止时间')]/../following-sibling::div",
                                        "//li[.//p[contains(text(), '投标截止')]]//p[contains(@class, 'name')]",
                                        "//li[contains(.,'投标截止时间')]//p[last()]"
                                    ]

                                    for selector in selectors_bid_deadline:
                                        elements = self.driver.find_elements(
                                            By.XPATH, selector)
                                        if elements:
                                            temp_text = elements[0].text.strip(
                                            )
                                            if temp_text and temp_text != "-" and re.match(r'\d{4}-\d{2}-\d{2}', temp_text):
                                                bid_deadline = temp_text
                                                print(
                                                    f"✓ 获取到投标截止时间: {bid_deadline} (选择器: {selector})")
                                                break

                                    # 如果上面的方法都失败，尝试直接搜索全页面文本
                                    if not bid_deadline:
                                        page_source = self.driver.page_source
                                        bid_deadline_matches = re.search(
                                            r'投标截止时间</p>\s*<p[^>]*>([^<]+)</p>', page_source)
                                        if bid_deadline_matches:
                                            temp_text = bid_deadline_matches.group(
                                                1).strip()
                                            if temp_text and temp_text != "-" and re.match(r'\d{4}-\d{2}-\d{2}', temp_text):
                                                bid_deadline = temp_text
                                                print(
                                                    f"✓ 通过正则获取到投标截止时间: {bid_deadline}")

                                    # 更新数据
                                    if bid_deadline:
                                        processed["投标截止时间"] = bid_deadline

                                    # 记录数据获取结果
                                    if not get_bid_deadline and not bid_deadline:
                                        print(
                                            f"⚠ 警告: 未能获取到任何截止时间数据，标题: {processed['公告标题'][:20]}...")

                                except Exception as e:
                                    print(f"获取截止时间失败: {str(e)}")
                                    # 尝试获取页面源码以便调试
                                    try:
                                        with open(os.path.join(self.pdf_dir, f"error_page_{int(time.time())}.html"), "w", encoding="utf-8") as f:
                                            f.write(self.driver.page_source)
                                    except:
                                        pass

                                # PDF下载流程（新增重试机制）
                                pdf_downloaded = False
                                for retry in range(3):
                                    try:
                                        # 等待并点击导出按钮
                                        export_btn = WebDriverWait(self.driver, 10).until(
                                            EC.element_to_be_clickable(
                                                (By.CSS_SELECTOR, ".export-pdf, .tools-export"))
                                        )
                                        self.driver.execute_script(
                                            "arguments[0].click();", export_btn)

                                        # 监控下载状态（最多等待30秒）
                                        start_time = time.time()
                                        while time.time() - start_time < 30:
                                            time.sleep(1)
                                            downloaded = [f for f in os.listdir(
                                                self.pdf_dir) if f.endswith('.pdf')]
                                            if any(f not in [x[0] for x in self.pdf_files.values()] for f in downloaded):
                                                pdf_downloaded = True
                                                break
                                        if pdf_downloaded:
                                            break
                                    except Exception as e:
                                        print(
                                            f"PDF下载第{retry+1}次尝试失败: {str(e)}")
                                        if retry == 2:
                                            print("连续3次下载失败，跳过该文件")

                                # 获取最新下载的PDF
                                if pdf_downloaded:
                                    initial_files = set(
                                        os.listdir(self.pdf_dir))
                                    time.sleep(2)  # 确保文件写入完成
                                    current_files = set(
                                        os.listdir(self.pdf_dir))
                                    new_files = list(
                                        current_files - initial_files)

                                    if new_files:
                                        latest_file = max(new_files, key=lambda f: os.path.getctime(
                                            os.path.join(self.pdf_dir, f)))
                                        # 改进文件名生成，使用更可靠的方式来命名PDF
                                        clean_name = re.sub(
                                            r'[<>:"/\\|?*]', '', processed["公告标题"])[:80] + ".pdf"
                                        clean_name = clean_name.replace(
                                            " ", "_")

                                        # 重命名并记录
                                        new_path = os.path.join(
                                            self.pdf_dir, clean_name)
                                        os.rename(os.path.join(
                                            self.pdf_dir, latest_file), new_path)

                                        if os.path.exists(new_path):
                                            self.pdf_files[processed["公告标题"]] = (
                                                clean_name, clean_name[:-4])
                                            print(f"成功记录附件：{clean_name}")
                                        else:
                                            print(
                                                f"文件重命名失败：{latest_file} -> {clean_name}")
                                    else:
                                        print("未检测到新PDF文件")

                            except Exception as e:
                                print(f"详情页操作异常：{str(e)}")
                            finally:
                                # 强制窗口清理
                                if len(self.driver.window_handles) > 1:
                                    try:
                                        self.driver.close()
                                    except:
                                        pass
                                    self.driver.switch_to.window(main_window)

                                # 额外窗口检查
                                if len(self.driver.window_handles) > 1:
                                    print("检测到残留窗口，执行强制清理...")
                                    for handle in self.driver.window_handles[1:]:
                                        try:
                                            self.driver.switch_to.window(
                                                handle)
                                            self.driver.close()
                                        except:
                                            pass
                                    self.driver.switch_to.window(main_window)
                        else:
                            print(f"未找到第{idx + 1}条数据的标题元素")

                    except Exception as e:
                        print(f"导出PDF失败: {str(e)}")
                        # 新增：跳过当前条目但继续执行
                        continue  # 直接继续下一条数据

                except Exception as e:
                    print(f"解析第{idx + 1}条数据异常：{str(e)}")
                    continue

            return not should_stop_crawl  # 返回是否继续采集

        except Exception as e:
            print(f"页面解析异常：{str(e)}")
            return True  # 异常时仍允许翻页

    def _download_pdf(self):
        try:
            # 添加显式等待
            export_btn = WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, ".tools-export, .export-pdf")
                )
            )
            self.driver.execute_script("arguments[0].click();", export_btn)

            # 添加下载状态检查
            WebDriverWait(self.driver, 30).until(
                lambda d: any(f.endswith('.crdownload')
                              for f in os.listdir(self.pdf_dir))
            )

        except TimeoutException:
            print("PDF下载超时，可能已存在或下载失败")

    def _is_recent_data(self, date_str):
        """严格校验日期范围（包含当天检测）"""
        try:
            # 获取当前日期
            today = datetime.now().date()
            # 将传入的日期字符串转换为日期对象
            pub_date = datetime.strptime(date_str, "%Y-%m-%d").date()

            # 排除今天及未来日期
            if pub_date >= today:
                print(f"发现当天/未来数据：{date_str}")
                return False

            # 校验日期是否在允许的采集日期范围内
            return self.start_date <= pub_date <= self.end_date

        except ValueError as e:
            # 若日期字符串格式不符合要求，打印错误信息并返回False
            print(f"日期解析失败：{date_str}，错误：{str(e)}")
            return False
        except Exception as e:
            # 处理其他未知异常，打印异常信息并返回False
            print(f"日期校验异常：{str(e)}")
            return False

    def _should_continue_pagination(self):
        """智能分页终止判断（增强版）"""
        # 定义检查最近数据的数量
        check_depth = 10
        # 从已采集的数据中筛选出最近 check_depth 条数据，且其发布时间在采集日期范围内的日期
        valid_dates = [
            datetime.strptime(item["发布时间"], "%Y/%m/%d").date()
            for item in self.all_data[-check_depth:]
            if item.get("发布时间") and
            self.start_date <= datetime.strptime(
                item["发布时间"], "%Y/%m/%d").date() <= self.end_date
        ]

        # 若筛选后没有有效日期数据，说明最近无有效数据，停止分页
        if not valid_dates:
            print("最近10条无有效数据，停止分页")
            return False

        # 对有效日期数据按降序排序
        sorted_dates = sorted(valid_dates, reverse=True)
        # 如果排序后的数据和原数据顺序相同，说明数据日期是递减的，可能已到旧数据区域，停止分页
        if sorted_dates == valid_dates:
            print("检测到日期递减顺序，可能已到旧数据区域")
            return False

        # 若不满足上述停止条件，继续分页
        return True

    # 解析金额
    def _parse_money(self, text):
        # 去除传入文本的首尾空格，若文本为空则赋值为空字符串
        original_text = text.strip() if text else ""
        try:
            # 使用正则表达式提取文本中的数字和单位（可能包含"万"）
            num_match = re.search(r'(\d+\.?\d*)(万?)', original_text)
            if num_match:
                # 将匹配到的数字部分转换为浮点数
                number = float(num_match.group(1))
                # 获取匹配到的单位
                unit = num_match.group(2)
                # 如果单位是"万"，则将数字乘以10000，否则直接使用该数字
                value = number * 10000 if unit == "万" else number
                return original_text, value
            # 若未匹配到数字和单位，返回原始文本和 None
            return original_text, None
        except:
            # 处理解析过程中的异常，返回原始文本和 None
            return original_text, None

    def _parse_status(self, item):
        """解析招标状态"""
        # 从传入的招标项目元素中选择时间标签元素
        time_tag = item.select_one('.bidsTime')
        if not time_tag:
            # 若未找到时间标签元素，默认招标状态为已截止
            return 2

        if '剩余' in time_tag.text:
            # 若时间标签文本中包含"剩余"，提取剩余天数
            days = int(time_tag.select_one('.days').text)
            # 若剩余天数大于0，招标状态为进行中；否则为已截止
            return 1 if days > 0 else 2
        # 若时间标签文本中不包含"剩余"，招标状态为未开始
        return 0

    def _parse_deadline(self, item, type):
        """解析截止时间"""
        try:
            # 获取当前日期和时间
            base_date = datetime.now()
            # 根据传入的类型选择对应的时间标签元素
            days_str = item.select(
                f'.bidsTime{2 if type == "signup" else 3} .days')
            if days_str:
                # 若找到对应的时间标签元素，提取剩余天数
                days = int(days_str[0].text)
                # 计算截止日期并格式化为 "YYYY-MM-DD" 格式
                return (base_date + timedelta(days=days)).strftime("%Y-%m-%d")
            # 若未找到对应的时间标签元素，返回"未公开"
            return "未公开"
        except:
            # 处理解析过程中的异常，返回"未公开"
            return "未公开"

    def _clean_text(self, text):
        """深度清理文本中的特殊字符"""
        if not text:
            return ""
        # 移除控制字符、表情符号、特殊符号等
        cleaned = re.sub(
            r'[\u0000-\u001F\u007F-\u009F\u200B-\u200F\u202A-\u202E\u2600-\u26FF\u2700-\u27BF]',
            '',
            str(text)
        )
        # 保留前一步结果，移除个别已知问题字符
        cleaned = re.sub(r'™', '', cleaned)
        # 保留常用汉字、标点、字母数字（不过度删除，确保数字和英文字母保留）
        cleaned = re.sub(
            r'[^\u4E00-\u9FA5\u3000-\u303F\uff00-\uffef\u0030-\u0039\u0041-\u005A\u0061-\u007A\u0020\u0021-\u007E\u00A0-\u00FF]',
            '',
            cleaned
        )
        return cleaned.strip()

    def _clean_text_for_excel(self, text):
        """专门用于Excel的文本清理，移除所有可能导致编码问题的字符"""
        if not text:
            return ""

        # 仅移除控制字符、不可见格式控制、emoji 等高风险字符，保留所有常见文本与数字
        cleaned = re.sub(
            r'[\u0000-\u001F\u007F-\u009F\u200B-\u200F\u202A-\u202E\uFE00-\uFE0F\uFE20-\uFE2F\u1F000-\u1FFFF]',
            '',
            str(text)
        )
        # 特定符号剔除，但保留英文字母和常见符号
        cleaned = re.sub(r'[\u2122\u00A9\u00AE]', '', cleaned)
        return cleaned.strip()

    def _normalize_text_for_matching(self, text):
        """
        标准化文本以便更好地匹配
        :param text: 需要标准化的文本
        :return: 标准化后的文本
        """
        if not text:
            return ""

        # 1. 转为小写
        text = text.lower()

        # 2. 移除扩展名
        text = re.sub(r'\.pdf$', '', text)

        # 3. 替换特殊分隔符为空格
        text = re.sub(r'[_\-\[\]\(\)\{\}\<\>\|]', ' ', text)

        # 4. 去除多余空格
        text = re.sub(r'\s+', ' ', text).strip()

        # 5. 特殊处理连续年份，如"20252026"转为"2025 2026"
        text = re.sub(r'(\d{4})(\d{4})', r'\1 \2', text)

        return text

    @staticmethod
    def clean_html(text):
        """清理HTML标签"""
        # 使用正则表达式去除文本中的HTML标签，并去除首尾空格
        return re.sub('<[^<]+?>', '', str(text)).strip()

    @staticmethod
    def format_date(date_str):
        """标准化日期格式"""
        try:
            # 将日期字符串从 "YYYY-MM-DD" 格式转换为 "YYYY/MM/DD" 格式
            return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y/%m/%d")
        except:
            # 处理日期格式转换异常，直接返回原始日期字符串
            return date_str

    def _save_data(self):
        if self.all_data:
            final_data = []

            # 先创建三种格式压缩包，以便后续 Excel 可以引用
            self._create_pdf_zip()
            self._create_tar_gz()
            self._create_7z()

            # 获取所有PDF文件名列表
            # 直接扫描 pdf_dir 目录，而不是从 ZIP 内读取文件名；
            # ZIP 内部存储的是 GBK 字节但 Python 按 CP437 解码，会产生乱码。
            # 磁盘上的文件名始终是正确的。
            pdf_files_in_zip = [
                f for f in os.listdir(self.pdf_dir) if f.endswith('.pdf')
            ]
            print(f"目录中共有{len(pdf_files_in_zip)}个PDF文件")

            for item in self.all_data:
                # 将数据中的发布时间字符串转换为日期对象
                item_date = datetime.strptime(item["发布时间"], "%Y/%m/%d").date()

                # 最终复核所有条件
                if (self.start_date <= item_date <= self.end_date) and (item_date < datetime.now().date()):
                    # 添加附件信息（核心修复部分）
                    title = item["公告标题"]

                    # 方法1：使用预先记录的pdf_files字典
                    if title in self.pdf_files:
                        file_name, display_name = self.pdf_files[title]
                        full_path = os.path.join(self.pdf_dir, file_name)

                        if os.path.exists(full_path):
                            # 计算文件大小（MB）
                            file_size = os.path.getsize(full_path)/1024/1024
                            # 设置附件信息，稍后在Excel中设置超链接
                            item["附件"] = {
                                "file_name": file_name,
                                "display_name": display_name,
                                "size": file_size,
                                "exists": True
                            }
                        else:
                            # 尝试在压缩包中查找匹配的PDF
                            matched_pdfs = self._find_matching_pdf(
                                title, pdf_files_in_zip)
                            if matched_pdfs:
                                # 使用找到的第一个匹配文件
                                matched_pdf = matched_pdfs[0]
                                item["附件"] = {
                                    "file_name": matched_pdf,
                                    "display_name": matched_pdf[:-4],
                                    "size": 1.0,  # 无法准确获取压缩包内文件大小，使用默认值
                                    "exists": True
                                }
                                print(f"在压缩包中找到匹配: {matched_pdf}")
                            else:
                                print(f"文件不存在：{file_name}")
                                item["附件"] = "文件丢失"
                    else:
                        # 尝试在压缩包中查找匹配的PDF
                        matched_pdfs = self._find_matching_pdf(
                            title, pdf_files_in_zip)
                        if matched_pdfs:
                            # 使用找到的第一个匹配文件
                            matched_pdf = matched_pdfs[0]
                            item["附件"] = {
                                "file_name": matched_pdf,
                                "display_name": matched_pdf[:-4],
                                "size": 1.0,  # 无法准确获取压缩包内文件大小，使用默认值
                                "exists": True
                            }
                            print(f"在压缩包中找到匹配: {matched_pdf}")
                        else:
                            # 分类记录缺失原因
                            item["附件"] = "未找到匹配文件"

                    final_data.append(item)
                else:
                    print(f"最终过滤：{item['发布时间']}")

            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"bid_data_{timestamp}.xlsx"

                # 数据处理
                processed_data = []
                for item in final_data:
                    item_copy = item.copy()
                    # 对数值/日期/标题等字段仅移除控制字符，避免数字被删
                    preserve_fields = {"公告标题", "预算金额",
                                       "发布时间", "标书获取截止时间", "投标截止时间", "产品"}
                    for key, value in item_copy.items():
                        if not isinstance(value, str):
                            continue
                        if key in preserve_fields:
                            # 仅去掉不可见控制类字符，保留数字与连接符
                            item_copy[key] = re.sub(
                                r'[\u0000-\u001F\u007F-\u009F\u200B-\u200F\u202A-\u202E]', '', value).strip()
                        else:
                            cleaned_value = self._clean_text_for_excel(value)
                            try:
                                cleaned_value.encode('utf-8')
                                item_copy[key] = cleaned_value
                            except UnicodeEncodeError:
                                # 如果仍然有编码问题，使用更激进的清理，但保留英文字母
                                item_copy[key] = re.sub(
                                    r'[^\u4E00-\u9FA5\u3000-\u303F\uff00-\uffef\u0030-\u0039\u0041-\u005A\u0061-\u007A\u0020\u0021\u0022\u0023\u0024\u0025\u0026\u0027\u0028\u0029\u002A\u002B\u002C\u002D\u002E\u002F\u003A\u003B\u003C\u003D\u003E\u003F\u0040\u005B\u005C\u005D\u005E\u005F\u0060\u007B\u007C\u007D\u007E\u00A0-\u00FF]', '', str(value))

                    if isinstance(item["附件"], dict) and item["附件"]["exists"]:
                        # 将附件信息转换为字符串，稍后在Excel中使用
                        item_copy["附件"] = f'PDF:{item["附件"]["file_name"]}:{item["附件"]["display_name"]}:{item["附件"]["size"]:.1f}'
                    processed_data.append(item_copy)

                df = pd.DataFrame(processed_data)
                df.insert(0, '序号', range(1, len(df) + 1))
                df['所属业委会'] = ''
                df['跟进情况'] = ''

                # 修改列顺序，将附件放在招标方式和公告标题之间
                columns_order = ['序号', '所属业委会', '招标方式', '附件', '公告标题', '产品', '预算金额',
                                 '招标公司', '跟进情况', '发布时间', '省份', '招标状态',
                                 '标书获取截止时间', '投标截止时间']
                df = df.reindex(columns=columns_order)

                with pd.ExcelWriter(filename, engine='xlsxwriter', engine_kwargs={'options': {'strings_to_urls': False}}) as writer:
                    # 先写入标题行（修复字段名缺失问题）
                    df.to_excel(writer, index=False,
                                sheet_name='招标数据', startrow=1, header=False)

                    workbook = writer.book
                    worksheet = writer.sheets['招标数据']

                    # 添加标题格式
                    header_format = workbook.add_format({
                        'bold': True,
                        'text_wrap': True,
                        'align': 'center',
                        'valign': 'vcenter',
                        'border': 1
                    })

                    # 写入标题行（带格式）
                    for col_num, value in enumerate(df.columns.values):
                        worksheet.write(0, col_num, value, header_format)

                    # 设置超链接格式
                    link_format = workbook.add_format({
                        'font_color': 'blue',
                        'underline': 1,
                        'align': 'left',
                        'valign': 'vcenter'
                    })

                    # 动态获取列索引
                    attachment_col = df.columns.get_loc("附件")
                    print(f"附件列位置：第{attachment_col+1}列")

                    # 设置列宽自适应
                    for idx, col in enumerate(df.columns):
                        # 计算最大列宽（数据+标题）- 使用更兼容的编码方式
                        try:
                            # 尝试使用UTF-8编码计算长度，如果失败则使用简单长度计算
                            max_len = max(
                                df[col].astype(str).apply(lambda x: len(
                                    x.encode('utf-8', errors='replace'))).max(),
                                len(col.encode('utf-8', errors='replace'))
                            )
                        except (UnicodeEncodeError, AttributeError):
                            # 如果编码失败，使用简单的字符串长度计算
                            try:
                                max_len = max(
                                    df[col].astype(str).str.len().max(),
                                    len(col)  # 列标题长度
                                )
                            except:
                                # 如果所有方法都失败，使用默认值
                                max_len = max(20, len(col))

                        # 限制列宽范围，避免过宽或过窄
                        max_len = max(10, min(50, max_len))
                        # 设置列宽（+2字符缓冲）
                        worksheet.set_column(idx, idx, max_len + 2)

                    # 特殊处理附件列格式和宽度
                    worksheet.set_column(
                        attachment_col, attachment_col, 25)  # 固定附件列宽度

                    # 处理附件超链接
                    # 获取PDF文件夹的相对路径
                    pdf_folder_name = os.path.basename(self.pdf_dir)

                    for row_idx in range(1, len(df)+1):
                        cell_value = df.iloc[row_idx-1]['附件']
                        if isinstance(cell_value, str) and cell_value.startswith('PDF:'):
                            parts = cell_value.split(':')
                            if len(parts) >= 4:
                                pdf_filename = parts[1]
                                display_name = parts[2]
                                file_size = parts[3]

                                # 构建超链接，指向同级目录下的PDF文件夹
                                display_text = f"{display_name}"

                                # 构建相对路径 - 使用同级目录下的PDF文件夹
                                pdf_relative_path = f"{pdf_folder_name}/{pdf_filename}"

                                # 创建超链接（XlsxWriter 需要 external: 前缀，并统一正斜杠）
                                pdf_relative_path_sanitized = pdf_relative_path.replace(
                                    "\\", "/")
                                link_url = f"external:{pdf_relative_path_sanitized}"
                                worksheet.write_url(
                                    row_idx,
                                    attachment_col,
                                    link_url,
                                    link_format,
                                    display_text
                                )
                            else:
                                worksheet.write(
                                    row_idx, attachment_col, "链接格式错误")
                        else:
                            worksheet.write(
                                row_idx, attachment_col, cell_value)

                print(f"成功保存{len(final_data)}条数据到 {filename}")

                # 发送邮件（附带Excel和压缩包）
                if final_data:
                    self._send_email(filename)

            except Exception as e:
                print(f"保存失败：{str(e)}")
                try:
                    # 检查浏览器驱动是否仍然可用
                    if hasattr(self, 'driver') and self.driver:
                        try:
                            # 尝试获取当前窗口句柄来验证驱动状态
                            current_handle = self.driver.current_window_handle
                            self.driver.save_screenshot("save_error.png")
                            print("已保存错误截图")
                        except Exception as driver_e:
                            print(f"浏览器驱动已断开，无法截图：{str(driver_e)}")
                    else:
                        print("浏览器驱动不可用，跳过截图")
                except Exception as ss_e:
                    print(f"截图操作失败：{str(ss_e)}")
        else:
            print("无有效数据需要保存")

    def _find_matching_pdf(self, title, pdf_files):
        """
        根据标题在PDF文件列表中查找匹配的PDF文件
        :param title: 招标标题
        :param pdf_files: PDF文件名列表
        :return: 匹配的PDF文件名列表
        """
        if not title or not pdf_files:
            return []

        # 标准化处理标题
        clean_title = self._normalize_text_for_matching(title)
        print(f"匹配标题: 原始='{title}', 标准化='{clean_title}'")

        # 对每个文件计算匹配分数
        matches = []
        for pdf_file in pdf_files:
            # 标准化处理PDF文件名
            clean_pdf = self._normalize_text_for_matching(pdf_file)

            # 计算匹配分数 - 使用多种策略
            score = 0
            keyword_score = 0
            number_score = 0
            common_word_score = 0

            # 1. 关键词匹配
            title_keywords = [word for word in re.split(
                r'\W+', clean_title) if len(word) > 1]
            pdf_keywords = [word for word in re.split(
                r'\W+', clean_pdf) if len(word) > 1]

            matched_keywords = []
            for kw in title_keywords:
                if kw in clean_pdf:
                    # 数字匹配得分更高
                    if re.match(r'^\d+$', kw):
                        keyword_score += len(kw) * 1.5
                    else:
                        keyword_score += len(kw)
                    matched_keywords.append(kw)

            # 2. 处理数字连续与分隔的情况
            # 从标题中提取纯数字
            title_numbers = re.findall(r'\d+', clean_title)
            pdf_numbers = re.findall(r'\d+', clean_pdf)

            matched_numbers = []
            for t_num in title_numbers:
                for p_num in pdf_numbers:
                    if t_num == p_num:  # 完全匹配
                        number_score += len(t_num) * 2
                        matched_numbers.append(f"{t_num}={p_num}")
                    elif t_num in p_num or p_num in t_num:  # 部分匹配
                        number_score += min(len(t_num), len(p_num))
                        matched_numbers.append(f"{t_num}~{p_num}")
                    # 特殊处理年份连续和分隔的情况 (如20252026与2025-2026)
                    elif len(t_num) == 8 and len(p_num) == 4:
                        if t_num[:4] == p_num:
                            number_score += 5
                            matched_numbers.append(f"{t_num[:4]}={p_num}")
                        elif t_num[4:] == p_num:
                            number_score += 5
                            matched_numbers.append(f"{t_num[4:]}={p_num}")
                    elif len(p_num) == 8 and len(t_num) == 4:
                        if p_num[:4] == t_num:
                            number_score += 5
                            matched_numbers.append(f"{t_num}={p_num[:4]}")
                        elif p_num[4:] == t_num:
                            number_score += 5
                            matched_numbers.append(f"{t_num}={p_num[4:]}")

            # 3. 计算共有词汇数量
            common_words = set(title_keywords) & set(pdf_keywords)
            if common_words:
                common_word_score = len(common_words) * 3

            # 总分
            score = keyword_score + number_score + common_word_score

            if score > 0:
                # 计算加权匹配度 - 根据标题长度进行归一化
                normalized_score = score / \
                    (len(clean_title) + 1) * 100  # 转换为百分比
                matches.append((pdf_file, score, normalized_score, {
                    'keywords': matched_keywords,
                    'numbers': matched_numbers,
                    'common_words': list(common_words)
                }))

        # 按匹配分数从高到低排序
        matches.sort(key=lambda x: x[1], reverse=True)

        # 打印匹配结果
        if matches:
            print(f"找到{len(matches)}个可能匹配:")
            # 只显示前3个
            for i, (pdf, score, norm_score, details) in enumerate(matches[:3]):
                print(f"  {i+1}. {pdf} (得分:{score}, 归一化:{norm_score:.1f}%)")
                print(f"     - 关键词匹配: {', '.join(details['keywords'])}")
                print(f"     - 数字匹配: {', '.join(details['numbers'])}")
                print(f"     - 共有词: {', '.join(details['common_words'])}")

        # 降低匹配阈值，只要有10%的匹配度就接受
        min_score = len(clean_title) * 0.1
        result = [m[0] for m in matches if m[1] >= min_score]

        if not result and matches:
            # 如果没有满足阈值的匹配，但有其他匹配，则取最高分的那个
            best_match = matches[0][0]
            best_score = matches[0][2]  # 使用归一化分数
            if best_score >= 15:  # 降低门槛，归一化分数至少15%就接受
                print(f"采用低阈值匹配: {best_match} (匹配度: {best_score:.1f}%)")
                return [best_match]

        if result:
            print(f"最终匹配: {result[0]}")
        else:
            print(f"未找到匹配PDF")

        return result


if __name__ == "__main__":
    # 创建 EnhancedBidSpider 类的实例
    spider = EnhancedBidSpider()
    # 调用 crawl 方法开始采集数据
    spider.crawl()
