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
        # 添加防止被检测为自动化的参数
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        self.driver = webdriver.Chrome(service=Service('/usr/local/bin/chromedriver'), options=chrome_options)
        self.wait = WebDriverWait(self.driver, 15, 0.5)  # 增加等待时间
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
            
            # 等待页面加载
            time.sleep(5)
            
            print(f"当前页面标题: {self.driver.title}")
            print(f"当前页面URL: {self.driver.current_url}")
            
            # 保存当前页面截图
            self.driver.save_screenshot('initial_page.png')
            print("已保存初始页面截图: initial_page.png")
            
            # 检查是否已经登录成功
            current_url = self.driver.current_url
            page_source = self.driver.page_source
            
            # 如果已经在图书馆网站且不是登录页面，可能已自动登录
            if 'huitu.zhishulib.com' in current_url and 'sso.hdu.edu.cn' not in current_url:
                print("已在图书馆网站，检查是否已登录...")
                
                # 尝试获取cookie
                try:
                    cookie_list = self.driver.get_cookies()
                    if cookie_list:
                        self.cookie = ";".join([item["name"] + "=" + item["value"] for item in cookie_list])
                        self.cfg["headers"]['Cookie'] = self.cookie
                        print(f"获取到 {len(cookie_list)} 个Cookie")
                        
                        # 验证登录状态
                        if self._test_login_status():
                            print("✅ 已自动登录成功！")
                            return 0
                except Exception as e:
                    print(f"获取Cookie失败: {e}")
            
            # 检查是否在SSO页面
            if 'sso.hdu.edu.cn' in current_url:
                print("检测到在统一身份认证平台(SSO)页面")
                return self._handle_sso_login()
            else:
                print("使用原登录页面")
                return self._login_original()
                
        except Exception as e:
            logging.error(f"登录失败：{e}")
            import traceback
            traceback.print_exc()
            return -1
    
    def _test_login_status(self):
        """测试是否已经登录"""
        try:
            headers = self.cfg["headers"]
            headers['Cookie'] = self.cookie
            resp = requests.get("https://hdu.huitu.zhishulib.com/Seat/Index/searchSeats?LAB_JSON=1",
                                headers=headers, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if 'DATA' in data and 'uid' in data['DATA']:
                    self.user_data = data['DATA']
                    print(f"✅ 用户信息获取成功，用户ID: {self.user_data['uid']}")
                    return True
                else:
                    print("❌ 获取的用户信息格式不正确")
                    return False
            else:
                print(f"❌ 请求用户信息失败，状态码: {resp.status_code}")
                return False
        except Exception as e:
            print(f"❌ 测试登录状态失败: {e}")
            return False
    
    def _handle_sso_login(self):
        """处理SSO登录"""
        print("开始处理SSO登录...")
        
        try:
            # 先等待几秒，看是否会自动跳转（可能已经有session）
            for i in range(10):
                time.sleep(1)
                current_url = self.driver.current_url
                print(f"等待自动跳转... {i+1}/10, 当前URL: {current_url}")
                
                if 'huitu.zhishulib.com' in current_url and 'sso.hdu.edu.cn' not in current_url:
                    print("检测到自动跳转回图书馆网站")
                    
                    # 获取cookie
                    cookie_list = self.driver.get_cookies()
                    self.cookie = ";".join([item["name"] + "=" + item["value"] for item in cookie_list])
                    self.cfg["headers"]['Cookie'] = self.cookie
                    
                    # 验证登录状态
                    if self._test_login_status():
                        print("✅ SSO自动登录成功！")
                        return 0
                    break
            
            # 如果未自动跳转，尝试手动登录
            print("未自动跳转，尝试手动登录...")
            self.driver.save_screenshot('sso_page.png')
            
            # 尝试查找并填写表单
            return self._try_sso_manual_login()
                
        except Exception as e:
            print(f"SSO登录处理失败: {e}")
            import traceback
            traceback.print_exc()
            return -1
    
    def _try_sso_manual_login(self):
        """尝试SSO手动登录"""
        try:
            # 查找所有input元素
            all_inputs = self.driver.find_elements(By.TAG_NAME, "input")
            print(f"找到 {len(all_inputs)} 个input元素")
            
            # 查找所有button元素
            all_buttons = self.driver.find_elements(By.TAG_NAME, "button")
            print(f"找到 {len(all_buttons)} 个button元素")
            
            # 查找所有表单元素
            all_forms = self.driver.find_elements(By.TAG_NAME, "form")
            print(f"找到 {len(all_forms)} 个form元素")
            
            # 尝试找到用户名输入框（通常第一个文本输入框）
            username_input = None
            for inp in all_inputs:
                try:
                    inp_type = inp.get_attribute("type")
                    if inp_type in ["text", "email", "tel"]:
                        username_input = inp
                        print(f"找到用户名输入框，type={inp_type}")
                        break
                except:
                    continue
            
            # 如果没找到，尝试第一个input
            if not username_input and all_inputs:
                username_input = all_inputs[0]
                print("使用第一个input作为用户名输入框")
            
            # 尝试找到密码输入框
            password_input = None
            for inp in all_inputs:
                try:
                    inp_type = inp.get_attribute("type")
                    if inp_type == "password":
                        password_input = inp
                        print("找到密码输入框")
                        break
                except:
                    continue
            
            # 如果没找到，尝试第二个input
            if not password_input and len(all_inputs) > 1:
                password_input = all_inputs[1]
                print("使用第二个input作为密码输入框")
            
            # 查找登录按钮
            login_button = None
            for btn in all_buttons:
                try:
                    btn_text = btn.text.lower()
                    if '登录' in btn_text or '登入' in btn_text or 'login' in btn_text or 'sign in' in btn_text:
                        login_button = btn
                        print(f"找到登录按钮: {btn.text}")
                        break
                except:
                    continue
            
            # 如果没找到，尝试第一个button
            if not login_button and all_buttons:
                login_button = all_buttons[0]
                print("使用第一个button作为登录按钮")
            
            # 检查是否找到必要元素
            if not username_input:
                print("❌ 未找到用户名输入框")
                return -1
            
            if not password_input:
                print("❌ 未找到密码输入框")
                return -1
            
            if not login_button:
                print("❌ 未找到登录按钮")
                return -1
            
            # 输入用户名和密码
            print(f"输入用户名: {self.un}")
            try:
                username_input.clear()
                username_input.send_keys(self.un)
            except:
                # 尝试JavaScript方式
                self.driver.execute_script("arguments[0].value = arguments[1];", username_input, self.un)
            
            print("输入密码")
            try:
                password_input.clear()
                password_input.send_keys(self.pd)
            except:
                # 尝试JavaScript方式
                self.driver.execute_script("arguments[0].value = arguments[1];", password_input, self.pd)
            
            # 保存登录前截图
            self.driver.save_screenshot('before_sso_login.png')
            
            # 点击登录按钮
            print("点击登录按钮")
            try:
                login_button.click()
            except:
                # 尝试JavaScript方式
                self.driver.execute_script("arguments[0].click();", login_button)
            
            # 等待登录完成
            for i in range(15):
                time.sleep(1)
                current_url = self.driver.current_url
                print(f"等待登录完成... {i+1}/15, 当前URL: {current_url}")
                
                if 'huitu.zhishulib.com' in current_url and 'sso.hdu.edu.cn' not in current_url:
                    print("✅ 成功重定向到图书馆网站")
                    
                    # 获取cookie
                    cookie_list = self.driver.get_cookies()
                    self.cookie = ";".join([item["name"] + "=" + item["value"] for item in cookie_list])
                    self.cfg["headers"]['Cookie'] = self.cookie
                    
                    # 验证登录
                    if self._test_login_status():
                        print("✅ SSO手动登录成功！")
                        return 0
                    break
            
            # 检查是否登录成功
            self.driver.save_screenshot('after_sso_login.png')
            
            # 最终检查
            if self._test_login_status():
                print("✅ 登录成功！")
                return 0
            else:
                print("❌ 登录失败")
                return -1
                
        except Exception as e:
            print(f"SSO手动登录失败: {e}")
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
    
    # 添加时间信息
    print(f"脚本开始时间: {datetime.now()}")
    
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
    
    # 尝试直接获取用户信息（可能已经自动登录）
    print("\n=== 尝试直接获取用户信息 ===")
    try:
        # 先获取当前cookie
        cookie_list = s.driver.get_cookies()
        if cookie_list:
            s.cookie = ";".join([item["name"] + "=" + item["value"] for item in cookie_list])
            s.cfg["headers"]['Cookie'] = s.cookie
            print(f"获取到 {len(cookie_list)} 个初始Cookie")
    except:
        pass
    
    if s.get_user_info() == 0:
        print("✅ 直接获取用户信息成功，跳过登录步骤")
    else:
        print("❌ 直接获取用户信息失败，开始登录流程")
        
        # 登录重试机制
        max_login_attempts = 3
        login_success = False
        
        for attempt in range(max_login_attempts):
            print(f"\n{'='*50}")
            print(f"登录尝试 {attempt + 1}/{max_login_attempts}")
            print(f"{'='*50}")
            
            if s.login() == 0:
                login_success = True
                print("✅ 登录成功！")
                break
            else:
                print(f"❌ 登录失败，尝试 {attempt + 1}/{max_login_attempts}")
                if attempt < max_login_attempts - 1:
                    wait_time = 5 * (attempt + 1)
                    print(f"等待{wait_time}秒后重试...")
                    time.sleep(wait_time)
        
        if not login_success:
            s.driver.quit()
            print("❌ 登录失败，已达到最大重试次数")
            exit(-1)
    
    # 执行预约
    print("\n=== 开始预约 ===")
    result_code, result_message = s.book_favorite_seat(user_config=user_config, seat_config=seat_config)
    
    # 输出结果
    print(f"\n预约结果: 代码={result_code}, 消息={result_message}")
    
    # 发送通知（如果有配置）
    if result_code == 0:
        s.wechatNotice("预约成功", result_message)
    else:
        s.wechatNotice("预约失败", result_message)
    
    s.driver.quit()
    logging.info('End of the program')
    print("脚本执行完成")
