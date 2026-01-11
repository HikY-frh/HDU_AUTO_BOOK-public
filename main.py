import requests
import yaml
import random
from datetime import datetime, timedelta
import json
import os
import logging
import pytz  # 需要安装 pytz 包

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

# 定义时区
UTC_TZ = pytz.UTC
BEIJING_TZ = pytz.timezone('Asia/Shanghai')  # 北京时间


def get_seats_with_config(user_config, date_config, seat_config):
    """获取座位列表，支持自定义座位"""
    # 检查是否有自定义字段（直接指定的座位号）
    if '自定义' in date_config:
        custom_seat = date_config['自定义']
        print(f"使用自定义座位: {custom_seat}")
        if isinstance(custom_seat, list):
            return custom_seat
        elif isinstance(custom_seat, int):
            return [custom_seat]
        else:
            return []
    
    # 二楼东/二楼西/四楼/三楼大厅/守正书院/求新书院/自定义
    seat_name = date_config.get('name', '')
    if not seat_name:
        return []
    
    if seat_name == "自定义":
        return user_config.get('自定义', [])
    
    if seat_name in seat_config:
        return list(range(seat_config[seat_name].get('begin', 0), 
                         seat_config[seat_name].get('end', 0)))
    return []


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
        self.wait = WebDriverWait(self.driver, 15, 0.5)
        self.cookie = None

        self.cfg = booker_config

    def get_current_beijing_time(self):
        """获取当前北京时间"""
        utc_now = datetime.utcnow().replace(tzinfo=UTC_TZ)
        beijing_now = utc_now.astimezone(BEIJING_TZ)
        return beijing_now

    def book_favorite_seat(self, user_config, seat_config):
        """预约座位"""
        # 判断是否到了预约时间
        the_day_after_tomorrow = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][
            (datetime.now().weekday() + 2) % 7]
        
        print(f"后天是: {the_day_after_tomorrow}")
        
        # 获取后天的配置
        date_config = user_config.get(the_day_after_tomorrow, {})
        if not date_config:
            return -1, f"未找到{the_day_after_tomorrow}的配置"
        
        # 检查配置是否启用
        if not date_config.get('启用', False):
            return -1, f"{the_day_after_tomorrow}的预约未启用"
        
        # 判断座位类型
        seat_name = date_config.get('name', '')
        if '自定义' in date_config:
            # 有自定义座位，默认使用自习室类型
            seat_type = "自习室"
            print("使用自定义座位，类型: 自习室")
        elif seat_name and seat_name in seat_config:
            seat_type = seat_config[seat_name].get("type", "自习室")
            print(f"座位名称: {seat_name}, 类型: {seat_type}")
        else:
            seat_type = "自习室"  # 默认类型
            print(f"未找到座位配置，使用默认类型: {seat_type}")
        
        # 获取当前北京时间
        beijing_now = self.get_current_beijing_time()
        print(f"当前北京时间: {beijing_now.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 设置预约时间（根据座位类型）
        if seat_type == "自习室":
            # 自习室：晚上20:00-20:15（北京时间）
            booking_start = beijing_now.replace(hour=20, minute=0, second=0, microsecond=0)
            booking_end = beijing_now.replace(hour=20, minute=15, second=0, microsecond=0)
        else:
            # 阅览室：晚上21:00-21:15（北京时间）
            booking_start = beijing_now.replace(hour=21, minute=0, second=0, microsecond=0)
            booking_end = beijing_now.replace(hour=21, minute=15, second=0, microsecond=0)
        
        # 考虑提前量
        booking_start = booking_start - timedelta(minutes=self.cfg.get("cron-delta-minutes", 0))
        
        print(f"预约窗口开始: {booking_start.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"预约窗口结束: {booking_end.strftime('%Y-%m-%d %H:%M:%S')}")
        
        if beijing_now < booking_start:
            wait_seconds = (booking_start - beijing_now).seconds
            wait_minutes = wait_seconds // 60
            return -1, f"未到预约时间，还需等待{wait_minutes}分{wait_seconds%60}秒"
        if beijing_now > booking_end:
            return -1, "已超过预约时间"
        
        print("✅ 在预约时间窗口内，开始预约...")
        
        logging.info('开始预约座位')
        retry_sleep_time = timedelta(minutes=self.cfg.get("cron-delta-minutes", 5)).seconds * 2 / (
                    self.cfg.get("max-retry", 5) - 2) - 10
        
        for tried_times in range(self.cfg.get("max-retry", 5)):
            try:
                result_code, result_message = self._book_favorite_seat(user_config, seat_config, tried_times)
                print(f"第{tried_times+1}次尝试: {result_message}")
                if result_code == 0:
                    return result_code, result_message
                time.sleep(retry_sleep_time)
            except Exception as e:
                logging.exception(e)
                print(e.__class__, f"尝试第{tried_times+1}次失败")
                time.sleep(retry_sleep_time)
        
        return -1, "达到最大重试次数，预约失败"

    def _book_favorite_seat(self, user_config, seat_config, tried_times=0):
        logging.info('预约座位详细方法')
        the_day_after_tomorrow = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][
            (datetime.now().weekday() + 2) % 7]
        
        date_config = user_config.get(the_day_after_tomorrow, {})
        
        # 获取座位列表
        seats = get_seats_with_config(user_config, date_config, seat_config)
        if not seats:
            return -1, "没有可用的座位"
        
        print(f"可用座位列表: {seats}")
        
        # 计算预约时间（使用北京时间）
        beijing_now = self.get_current_beijing_time()
        today_0_clock = beijing_now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # 使用配置文件中的开始时间
        start_hour = date_config.get('开始时间', 0)
        book_time = today_0_clock + timedelta(days=2) + timedelta(hours=start_hour)
        
        # 计算从基准时间开始的秒数
        # 注意：基准时间也需要转换为北京时间
        if isinstance(self.cfg["start-time"], str):
            # 如果基准时间是字符串，解析它
            base_time = datetime.strptime(self.cfg["start-time"], "%Y-%m-%d %H:%M:%S")
            base_time = BEIJING_TZ.localize(base_time)
        else:
            # 如果已经是datetime对象
            base_time = self.cfg["start-time"]
            if base_time.tzinfo is None:
                base_time = BEIJING_TZ.localize(base_time)
        
        delta = book_time - base_time
        total_seconds = delta.days * 24 * 3600 + delta.seconds
        
        print(f"预约使用时间: {book_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"基准时间: {base_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"时间差(秒): {total_seconds}")
        
        # 选择座位
        if '自定义' in date_config and tried_times < self.cfg.get("max-retry", 5) / 3 * 2:
            seat = seats[0] if seats else 0
        else:
            seat = random.choice(seats) if seats else 0
        
        print(f"选择座位: {seat}")
        
        # 构建预约请求数据
        duration_hours = date_config.get('持续小时数', 2)
        data = f"beginTime={total_seconds}&duration={3600 * duration_hours}&&seats[0]={seat}&seatBookers[0]={self.user_data['uid']}"

        headers = self.cfg["headers"]
        headers['Cookie'] = self.cookie
        print(f"预约请求数据: {data}")
        
        try:
            self.resp = requests.post(self.cfg["target"], data=data, headers=headers, timeout=10)
            self.json = json.loads(self.resp.text)
            print(f"预约响应: {self.json}")
            
            # 检查预约结果
            if self.json.get("CODE") == 0:
                return 0, self.json.get("MESSAGE", "预约成功") + f" 座位:{seat}"
            else:
                return self.json.get("CODE", -1), self.json.get("MESSAGE", "预约失败") + f" 座位:{seat}"
        except Exception as e:
            print(f"请求失败: {str(e)}")
            return -1, f"请求失败: {str(e)}"

    # 以下方法保持不变（login, get_user_info等）
    def login(self):
        logging.info('开始登录...')
        
        try:
            logging.info('打开图书馆网站...')
            self.driver.get("https://hdu.huitu.zhishulib.com/")
            
            time.sleep(5)
            
            print(f"当前页面标题: {self.driver.title}")
            print(f"当前页面URL: {self.driver.current_url}")
            
            self.driver.save_screenshot('initial_page.png')
            
            current_url = self.driver.current_url
            
            if 'huitu.zhishulib.com' in current_url and 'sso.hdu.edu.cn' not in current_url:
                print("已在图书馆网站，检查是否已登录...")
                
                try:
                    cookie_list = self.driver.get_cookies()
                    if cookie_list:
                        self.cookie = ";".join([item["name"] + "=" + item["value"] for item in cookie_list])
                        self.cfg["headers"]['Cookie'] = self.cookie
                        print(f"获取到 {len(cookie_list)} 个Cookie")
                        
                        if self._test_login_status():
                            print("✅ 已自动登录成功！")
                            return 0
                except Exception as e:
                    print(f"获取Cookie失败: {e}")
            
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
            for i in range(10):
                time.sleep(1)
                current_url = self.driver.current_url
                print(f"等待自动跳转... {i+1}/10, 当前URL: {current_url}")
                
                if 'huitu.zhishulib.com' in current_url and 'sso.hdu.edu.cn' not in current_url:
                    print("检测到自动跳转回图书馆网站")
                    
                    cookie_list = self.driver.get_cookies()
                    self.cookie = ";".join([item["name"] + "=" + item["value"] for item in cookie_list])
                    self.cfg["headers"]['Cookie'] = self.cookie
                    
                    if self._test_login_status():
                        print("✅ SSO自动登录成功！")
                        return 0
                    break
            
            print("未自动跳转，尝试手动登录...")
            self.driver.save_screenshot('sso_page.png')
            
            return self._try_sso_manual_login()
                
        except Exception as e:
            print(f"SSO登录处理失败: {e}")
            import traceback
            traceback.print_exc()
            return -1
    
    def _try_sso_manual_login(self):
        """尝试SSO手动登录"""
        try:
            all_inputs = self.driver.find_elements(By.TAG_NAME, "input")
            print(f"找到 {len(all_inputs)} 个input元素")
            
            all_buttons = self.driver.find_elements(By.TAG_NAME, "button")
            print(f"找到 {len(all_buttons)} 个button元素")
            
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
            
            if not username_input and all_inputs:
                username_input = all_inputs[0]
                print("使用第一个input作为用户名输入框")
            
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
            
            if not password_input and len(all_inputs) > 1:
                password_input = all_inputs[1]
                print("使用第二个input作为密码输入框")
            
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
            
            if not login_button and all_buttons:
                login_button = all_buttons[0]
                print("使用第一个button作为登录按钮")
            
            if not username_input:
                print("❌ 未找到用户名输入框")
                return -1
            
            if not password_input:
                print("❌ 未找到密码输入框")
                return -1
            
            if not login_button:
                print("❌ 未找到登录按钮")
                return -1
            
            print(f"输入用户名: {self.un}")
            try:
                username_input.clear()
                username_input.send_keys(self.un)
            except:
                self.driver.execute_script("arguments[0].value = arguments[1];", username_input, self.un)
            
            print("输入密码")
            try:
                password_input.clear()
                password_input.send_keys(self.pd)
            except:
                self.driver.execute_script("arguments[0].value = arguments[1];", password_input, self.pd)
            
            self.driver.save_screenshot('before_sso_login.png')
            
            print("点击登录按钮")
            try:
                login_button.click()
            except:
                self.driver.execute_script("arguments[0].click();", login_button)
            
            for i in range(15):
                time.sleep(1)
                current_url = self.driver.current_url
                print(f"等待登录完成... {i+1}/15, 当前URL: {current_url}")
                
                if 'huitu.zhishulib.com' in current_url and 'sso.hdu.edu.cn' not in current_url:
                    print("✅ 成功重定向到图书馆网站")
                    
                    cookie_list = self.driver.get_cookies()
                    self.cookie = ";".join([item["name"] + "=" + item["value"] for item in cookie_list])
                    self.cfg["headers"]['Cookie'] = self.cookie
                    
                    if self._test_login_status():
                        print("✅ SSO手动登录成功！")
                        return 0
                    break
            
            self.driver.save_screenshot('after_sso_login.png')
            
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
    if isinstance(date_cfg, dict) and date_cfg.get('启用', False):
        return True
    return False


if __name__ == "__main__":
    logging.info('Start of the program')
    
    # 获取当前北京时间
    beijing_now = datetime.utcnow().replace(tzinfo=pytz.UTC).astimezone(pytz.timezone('Asia/Shanghai'))
    print(f"脚本开始北京时间: {beijing_now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"今天是: {beijing_now.strftime('%Y-%m-%d %A')}")
    
    the_day_after_tomorrow = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][(beijing_now.weekday() + 2) % 7]
    print(f"后天是: {the_day_after_tomorrow}")
    
    try:
        with open("user_config.yml", 'r') as f_obj:
            user_config = yaml.safe_load(f_obj)
        print(f"user_config 加载成功，后天({the_day_after_tomorrow})的配置: {user_config.get(the_day_after_tomorrow, {})}")
        
        with open("config/basic_config.yml", 'r') as f_obj:
            basic_config = yaml.safe_load(f_obj)
        print(f"basic_config 加载成功")
        
        with open("config/seat_config.yml", 'r') as f_obj:
            seat_config = yaml.safe_load(f_obj)
        print(f"seat_config 加载成功，可用座位类型: {list(seat_config.keys())}")
        
    except Exception as e:
        print(f"配置文件加载失败: {e}")
        exit(1)

    # 检查后天的预约是否启用
    day_config = user_config.get(the_day_after_tomorrow, {})
    if not day_config:
        print(f"错误: user_config.yml 中没有找到 {the_day_after_tomorrow} 的配置")
        exit(1)
    
    if not is_booking_enable(day_config):
        logging.info('预约未启用')
        print(f"{the_day_after_tomorrow}的预约未启用")
        exit(0)
    
    s = SeatAutoBooker(basic_config["SeatAutoBooker"])
    
    print("\n=== 尝试直接获取用户信息 ===")
    try:
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
    
    print("\n=== 开始预约 ===")
    result_code, result_message = s.book_favorite_seat(user_config=user_config, seat_config=seat_config)
    
    print(f"\n预约结果: 代码={result_code}, 消息={result_message}")
    
    if result_code == 0:
        s.wechatNotice("预约成功", result_message)
    else:
        s.wechatNotice("预约失败", result_message)
    
    s.driver.quit()
    logging.info('End of the program')
    print("脚本执行完成")
