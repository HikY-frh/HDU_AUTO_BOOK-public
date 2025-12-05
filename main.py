import requests
import yaml
import random
from datetime import datetime, timedelta
import json
import os
import logging

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
import time


logging.basicConfig(
    format='%(asctime)s,%(msecs)d %(name)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
    level=logging.DEBUG)

time_zone = 8  # 时区


def get_seats_with_config(user_config, date_config, seat_config):
    # 二楼东/二楼西/四楼/三楼大厅/守正书院/求新书院/自定义
    seat_name = date_config['name']
    if seat_name == "自定义":
        return user_config['自定义']
    return list(range(seat_config[seat_name]['begin'], seat_config[seat_name]['end']))


class SeatAutoBooker:
    def __init__(self, booker_config):
        self.json = None
        self.resp = None
        self.user_data = None

        logging.info('Creating SeatAutoBooker object')

        self.un = os.environ["SCHOOL_ID"].strip()  # 学号
        print("使用用户：{}".format(self.un))
        self.pd = os.environ["PASSWORD"].strip()  # 密码
        self.SCKey = None
        try:
            self.SCKey = os.environ["SCKEY"]
        except KeyError:
            print("没有Server酱的key,将不会推送消息")

        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        self.driver = webdriver.Chrome(service=Service('/usr/local/bin/chromedriver'), options=chrome_options)
        self.wait = WebDriverWait(self.driver, 10, 0.5)
        self.cookie = None

        self.cfg = booker_config

    def book_favorite_seat(self, user_config, seat_config):
        # 判断是否到了预约时间
        # 阅览室晚上9点开始预约，自习室晚上8点半开始预约
        the_day_after_tomorrow = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][
            (datetime.now().weekday() + 2) % 7]
        seat_type = seat_config[user_config[the_day_after_tomorrow]['name']]["type"]
        if seat_type == "自习室":
            start_time = datetime.now().replace(hour=20 - time_zone, minute=0, second=0, microsecond=0)
            end_time = datetime.now().replace(hour=20 - time_zone, minute=15, second=0, microsecond=0)
        else:
            start_time = datetime.now().replace(hour=21 - time_zone, minute=0, second=0, microsecond=0)
            end_time = datetime.now().replace(hour=21 - time_zone, minute=15, second=0, microsecond=0)
        start_time = start_time - timedelta(minutes=self.cfg["cron-delta-minutes"])
        if datetime.now() < start_time or datetime.now() > end_time:
            return -1, "未到预约时间"
        logging.info('Booking favorite seat')
        retry_sleep_time = timedelta(minutes=self.cfg["cron-delta-minutes"]).seconds * 2 / (
                    self.cfg["max-retry"] - 2) - 10
        for tried_times in range(self.cfg["max-retry"]):
            try:
                return self._book_favorite_seat(user_config, seat_config, tried_times)
            except Exception as e:
                logging.exception(e)
                print(e.__class__, "尝试第{}次".format(tried_times))
                time.sleep(retry_sleep_time)

    def _book_favorite_seat(self, user_config, seat_config, tried_times=0):
        logging.info('Entering _book_favorite_seat method')
        the_day_after_tomorrow = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][
            (datetime.now().weekday() + 2) % 7]
        date_config = user_config[the_day_after_tomorrow]
        seats = get_seats_with_config(user_config, date_config, seat_config)
        today_0_clock = datetime.strptime(datetime.now().strftime("%Y-%m-%d 00:00:00"), "%Y-%m-%d %H:%M:%S")
        book_time = today_0_clock + timedelta(days=2) + timedelta(hours=date_config['开始时间'])
        delta = book_time - self.cfg["start-time"]
        total_seconds = delta.days * 24 * 3600 + delta.seconds
        if date_config['name'] == '自定义' and tried_times < self.cfg["max-retry"] / 3 * 2:
            seat = seats[0]
        else:
            seat = random.choice(seats)
        data = f"beginTime={total_seconds}&duration={3600 * date_config['持续小时数']}&&seats[0]={seat}&seatBookers[0]={self.user_data['uid']}"

        headers = self.cfg["headers"]
        headers['Cookie'] = self.cookie
        print(data)
        self.resp = requests.post(self.cfg["target"], data=data, headers=headers)
        self.json = json.loads(self.resp.text)
        return self.json["CODE"], self.json["MESSAGE"] + " 座位:{}".format(seat)

    def login(self):
        logging.info('开始登录...')
        
        try:
            logging.info('打开图书馆网站...')
            self.driver.get("https://hdu.huitu.zhishulib.com/")
            
            # 等待页面加载（可能会被重定向到SSO）
            time.sleep(3)
            
            # 保存页面截图，用于调试
            self.driver.save_screenshot('initial_page.png')
            print("已保存初始页面截图: initial_page.png")
            
            print(f"当前页面标题: {self.driver.title}")
            print(f"当前页面URL: {self.driver.current_url}")
            
            # 检查是否被重定向到SSO统一身份认证
            current_url = self.driver.current_url
            if 'sso.hdu.edu.cn' in current_url:
                print("检测到已重定向到统一身份认证平台(SSO)")
                return self._login_sso()
            else:
                print("使用原登录页面")
                return self._login_original()
                
        except Exception as e:
            logging.error(f"登录失败：{e}")
            import traceback
            traceback.print_exc()
            return -1

    def _login_sso(self):
        """处理统一身份认证(SSO)登录"""
        print("开始处理SSO登录...")
        
        try:
            # 等待SSO页面加载
            time.sleep(2)
            
            # 保存SSO页面截图
            self.driver.save_screenshot('sso_page.png')
            print("已保存SSO页面截图: sso_page.png")
            
            # 打印页面HTML结构的前500字符用于调试
            page_source = self.driver.page_source[:500]
            print(f"页面HTML前500字符: {page_source}")
            
            # 尝试查找SSO页面的用户名输入框
            # SSO页面可能有多种选择器，我们尝试多个
            username_input = None
            password_input = None
            login_button = None
            
            # 尝试多种用户名输入框选择器
            username_selectors = [
                (By.ID, "username"),  # 最常见
                (By.NAME, "username"),
                (By.CSS_SELECTOR, "input[name='username']"),
                (By.CSS_SELECTOR, "input[type='text']"),
                (By.XPATH, "//input[contains(@placeholder, '学号') or contains(@placeholder, '用户名') or contains(@placeholder, '工号')]"),
            ]
            
            # 尝试多种密码输入框选择器
            password_selectors = [
                (By.ID, "password"),  # 最常见
                (By.NAME, "password"),
                (By.CSS_SELECTOR, "input[type='password']"),
                (By.XPATH, "//input[@type='password']"),
            ]
            
            # 尝试多种登录按钮选择器
            button_selectors = [
                (By.CSS_SELECTOR, "button[type='submit']"),
                (By.XPATH, "//button[contains(text(), '登录')]"),
                (By.XPATH, "//input[@type='submit']"),
                (By.CSS_SELECTOR, "input[type='submit']"),
                (By.XPATH, "//button[@type='button' and contains(text(), '登录')]"),
            ]
            
            # 尝试查找用户名输入框
            for selector_type, selector in username_selectors:
                try:
                    username_input = self.driver.find_element(selector_type, selector)
                    print(f"使用 {selector_type}:{selector} 找到用户名输入框")
                    break
                except:
                    continue
            
            if not username_input:
                print("未找到用户名输入框，尝试查找所有输入框")
                # 查找所有输入框
                all_inputs = self.driver.find_elements(By.TAG_NAME, "input")
                print(f"找到 {len(all_inputs)} 个输入框")
                
                for i, inp in enumerate(all_inputs):
                    inp_type = inp.get_attribute("type")
                    inp_name = inp.get_attribute("name")
                    inp_id = inp.get_attribute("id")
                    print(f"输入框 {i}: type={inp_type}, name={inp_name}, id={inp_id}")
                    
                    # 尝试判断哪个是用户名输入框
                    if inp_type == "text" or inp_name == "username" or inp_id == "username":
                        username_input = inp
                        print(f"选择第 {i} 个输入框作为用户名输入框")
                        break
            
            # 尝试查找密码输入框
            for selector_type, selector in password_selectors:
                try:
                    password_input = self.driver.find_element(selector_type, selector)
                    print(f"使用 {selector_type}:{selector} 找到密码输入框")
                    break
                except:
                    continue
            
            if not password_input:
                # 查找所有type为password的输入框
                try:
                    password_inputs = self.driver.find_elements(By.CSS_SELECTOR, "input[type='password']")
                    if password_inputs:
                        password_input = password_inputs[0]
                        print("使用CSS选择器找到密码输入框")
                except:
                    pass
            
            # 尝试查找登录按钮
            for selector_type, selector in button_selectors:
                try:
                    login_button = self.driver.find_element(selector_type, selector)
                    print(f"使用 {selector_type}:{selector} 找到登录按钮")
                    break
                except:
                    continue
            
            if not login_button:
                # 尝试查找所有按钮
                try:
                    all_buttons = self.driver.find_elements(By.TAG_NAME, "button")
                    for btn in all_buttons:
                        btn_text = btn.text
                        if '登录' in btn_text or '登入' in btn_text or 'Login' in btn_text or 'LOGIN' in btn_text:
                            login_button = btn
                            print(f"通过按钮文本找到登录按钮: {btn_text}")
                            break
                except:
                    pass
            
            # 检查是否找到了所有必要元素
            if not username_input:
                print("错误：未找到用户名输入框")
                # 保存完整的页面源码用于调试
                with open('sso_page_source.html', 'w', encoding='utf-8') as f:
                    f.write(self.driver.page_source)
                return -1
            
            if not password_input:
                print("错误：未找到密码输入框")
                return -1
            
            if not login_button:
                print("错误：未找到登录按钮")
                return -1
            
            # 输入用户名和密码
            print(f"输入用户名: {self.un}")
            username_input.clear()
            username_input.send_keys(self.un)
            
            print("输入密码")
            password_input.clear()
            password_input.send_keys(self.pd)
            
            # 点击登录按钮
            print("点击登录按钮")
            login_button.click()
            
            # 等待登录完成和重定向
            time.sleep(5)
            
            # 检查是否登录成功
            self.driver.save_screenshot('after_sso_login.png')
            print("已保存SSO登录后截图: after_sso_login.png")
            
            print(f"登录后页面标题: {self.driver.title}")
            print(f"登录后页面URL: {self.driver.current_url}")
            
            # 检查是否重定向回图书馆网站
            if 'huitu.zhishulib.com' in self.driver.current_url:
                print("成功重定向回图书馆网站")
                # 获取cookie
                cookie_list = self.driver.get_cookies()
                self.cookie = ";".join([item["name"] + "=" + item["value"] for item in cookie_list])
                self.cfg["headers"]['Cookie'] = self.cookie
                print("登录成功！")
                return 0
            else:
                print("未成功重定向回图书馆网站")
                # 检查是否有错误信息
                page_text = self.driver.page_source
                if '错误' in page_text or '错误' in page_text or '失败' in page_text:
                    print("检测到错误信息")
                    # 保存错误页面
                    with open('sso_error_page.html', 'w', encoding='utf-8') as f:
                        f.write(self.driver.page_source)
                return -1
                
        except Exception as e:
            print(f"SSO登录过程中出现异常: {str(e)}")
            import traceback
            traceback.print_exc()
            return -1

    def _login_original(self):
        """原来的登录方式（备用）"""
        print("使用原登录页面...")
        
        # 原有的登录代码
        pwd_path_selector = """//*[@id="react-root"]/div/div/div[1]/div[2]/div/div[1]/div[2]/div/div/div/div/div[1]/div[2]/div/div[3]/div/div[2]/input"""
        button_path_selector = """//*[@id="react-root"]/div/div/div[1]/div[2]/div/div[1]/div[2]/div/div/div/div/div[1]/div[3]"""

        try:
            self.wait.until(EC.presence_of_element_located((By.NAME, "login_name")))
            print("找到用户名输入框")

            self.driver.find_element(By.NAME, 'login_name').clear()
            self.driver.find_element(By.NAME, 'login_name').send_keys(self.un)
            print(f"输入用户名: {self.un}")

            self.driver.find_element(By.XPATH, pwd_path_selector).clear()
            self.driver.find_element(By.XPATH, pwd_path_selector).send_keys(self.pd)
            print("输入密码")

            self.driver.find_element(By.XPATH, button_path_selector).click()
            print("点击登录按钮")
            
            time.sleep(5)
            
            cookie_list = self.driver.get_cookies()
            self.cookie = ";".join([item["name"] + "=" + item["value"] + "" for item in cookie_list])
            self.cfg["headers"]['Cookie'] = self.cookie

            print("登录成功！")
            return 0
        except Exception as e:
            print(f"原登录方式失败: {e}")
            return -1

    def get_user_info(self):
        logging.info('Getting user info')

        headers = self.cfg["headers"]
        headers['Cookie'] = self.cookie
        try:
            resp = requests.get("https://hdu.huitu.zhishulib.com/Seat/Index/searchSeats?LAB_JSON=1",
                                headers=headers)
            self.user_data = resp.json()['DATA']
            _ = self.user_data['uid']
        except Exception as e:
            logging.exception(e)
            print(self.user_data)
            print(e.__class__.__name__ + ",获取用户数据失败")
            return -1
        print("获取用户数据成功")
        return 0

    def wechatNotice(self, message, desp=None):
        logging.info('Sending WeChat notice')

        if self.SCKey != '':
            url = 'https://sctapi.ftqq.com/{0}.send'.format(self.SCKey)
            data = {
                'title': message,
                desp: desp,
            }
            try:
                r = requests.post(url, data=data)
                if r.json()["data"]["error"] == 'SUCCESS':
                    print("Server酱通知成功")
                else:
                    print("Server酱通知失败")
            except Exception as e:
                logging.exception(e)
                print(e.__class__, "推送服务配置错误")


def is_booking_enable(date_cfg):
    if date_cfg['启用']:
        return True
    return False


if __name__ == "__main__":
    logging.info('Start of the program')
    
    with open("user_config.yml", 'r') as f_obj:
        user_config = yaml.safe_load(f_obj)
    with open("config/basic_config.yml", 'r') as f_obj:
        basic_config = yaml.safe_load(f_obj)
    with open("config/seat_config.yml", 'r') as f_obj:
        seat_config = yaml.safe_load(f_obj)

    the_day_after_tomorrow = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][(datetime.now().weekday() + 2) % 7]
    if not is_booking_enable(user_config[the_day_after_tomorrow]):
        logging.info('预约未启用')
        print("预约未启用")
        exit(0)

    s = SeatAutoBooker(basic_config["SeatAutoBooker"])
    
    # 添加登录重试机制
    max_login_attempts = 3
    login_success = False
    
    for attempt in range(max_login_attempts):
        print(f"登录尝试 {attempt + 1}/{max_login_attempts}")
        if s.login() == 0:
            login_success = True
            print("登录成功！")
            break
        else:
            print(f"登录失败，尝试 {attempt + 1}/{max_login_attempts}")
            if attempt < max_login_attempts - 1:
                print("等待3秒后重试...")
                time.sleep(3)
    
    if not login_success:
        s.driver.quit()
        print("登录失败，已达到最大重试次数")
        exit(-1)
    
    if not s.get_user_info() == 0:
        s.driver.quit()
        logging.info('Getting user info unsuccessful')
        exit(-1)
    
    s.book_favorite_seat(user_config=user_config, seat_config=seat_config)
    s.driver.quit()
    logging.info('End of the program')
