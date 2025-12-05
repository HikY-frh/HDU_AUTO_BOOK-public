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

# 两天后日期

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
        #判断是否到了预约时间
        # 阅览室晚上9点开始预约，自习室晚上8点半开始预约
        the_day_after_tomorrow = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][(datetime.now().weekday() + 2) % 7]
        seat_type = seat_config[user_config[the_day_after_tomorrow]['name']]["type"]
        if seat_type == "自习室":
            start_time = datetime.now().replace(hour=20-time_zone, minute=0, second=0, microsecond=0)
            end_time = datetime.now().replace(hour=20-time_zone, minute=15, second=0, microsecond=0)
        else:
            start_time = datetime.now().replace(hour=21-time_zone, minute=0, second=0, microsecond=0)
            end_time = datetime.now().replace(hour=21-time_zone, minute=15, second=0, microsecond=0)
        start_time = start_time - timedelta(minutes=self.cfg["cron-delta-minutes"])
        if datetime.now() < start_time or datetime.now() > end_time:
            return -1, "未到预约时间"
        logging.info('Booking favorite seat')
        retry_sleep_time = timedelta(minutes=self.cfg["cron-delta-minutes"]).seconds*2/(self.cfg["max-retry"]-2) - 10
        for tried_times in range(self.cfg["max-retry"]):
            try:
                return self._book_favorite_seat(user_config, seat_config, tried_times)
            except Exception as e:
                logging.exception(e)
                print(e.__class__, "尝试第{}次".format(tried_times))
                time.sleep(retry_sleep_time)

    def _book_favorite_seat(self, user_config, seat_config, tried_times=0):
        logging.info('Entering _book_favorite_seat method')
        the_day_after_tomorrow = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][(datetime.now().weekday() + 2) % 7]
        date_config = user_config[the_day_after_tomorrow]
        seats = get_seats_with_config(user_config, date_config, seat_config)
        today_0_clock = datetime.strptime(datetime.now().strftime("%Y-%m-%d 00:00:00"), "%Y-%m-%d %H:%M:%S")
        book_time = today_0_clock + timedelta(days=2) + timedelta(hours=date_config['开始时间'])
        delta = book_time - self.cfg["start-time"]
        total_seconds = delta.days * 24 * 3600 + delta.seconds
        if date_config['name'] == '自定义' and tried_times<self.cfg["max-retry"]/3*2:
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
    
    # 尝试多种选择器，提高兼容性
      password_selectors = [
        # 原始选择器
        """//*[@id="react-root"]/div/div/div[1]/div[2]/div/div[1]/div[2]/div/div/div/div/div[1]/div[2]/div/div[3]/div/div[2]/input""",
        # 尝试其他可能的选择器
        "//input[@type='password']",
        "//input[contains(@name, 'password')]",
        "//input[contains(@placeholder, '密码')]",
        "//input[contains(@class, 'password')]",
      ]
    
      button_selectors = [
        # 原始选择器
        """//*[@id="react-root"]/div/div/div[1]/div[2]/div/div[1]/div[2]/div/div/div/div/div[1]/div[3]""",
        # 尝试其他可能的选择器
        "//button[contains(text(), '登录')]",
        "//button[contains(text(), '登入')]",
        "//button[contains(text(), 'Login')]",
        "//button[@type='submit']",
        "//div[contains(text(), '登录')]",
        "//span[contains(text(), '登录')]",
      ]

      try:
          logging.info('打开网站...')
          self.driver.get("https://hdu.huitu.zhishulib.com/")
        
        # 等待页面加载
          time.sleep(3)
        
        # 保存页面截图，用于调试
          self.driver.save_screenshot('login_page.png')
          print("已保存登录页面截图: login_page.png")
        
        # 打印页面标题和URL，用于调试
          print(f"页面标题: {self.driver.title}")
          print(f"页面URL: {self.driver.current_url}")
        
        # 尝试查找用户名输入框
          logging.info('查找用户名输入框...')
          username_input = None
        
          try:
            # 先尝试原来的方式
              username_input = self.wait.until(
                  EC.presence_of_element_located((By.NAME, "login_name"))
              )
              print("使用By.NAME找到用户名输入框")
          except:
            # 如果找不到，尝试其他方式
              try:
                  username_input = self.wait.until(
                      EC.presence_of_element_located((By.XPATH, "//input[contains(@placeholder, '学号') or contains(@placeholder, '账号') or contains(@placeholder, '用户名')]"))
                  )
                  print("使用placeholder找到用户名输入框")
              except:
                  try:
                      username_input = self.wait.until(
                          EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text']"))
                      )
                      print("使用CSS选择器找到用户名输入框")
                  except Exception as e:
                      print(f"未找到用户名输入框: {e}")
                    # 保存当前页面状态以便调试
                      with open('page_source.html', 'w', encoding='utf-8') as f:
                          f.write(self.driver.page_source)
                      return -1
        
          if username_input:
              username_input.clear()
              username_input.send_keys(self.un)
              print(f"已输入用户名: {self.un}")
              logging.info('输入用户名成功')
        
        # 尝试查找密码输入框
          logging.info('查找密码输入框...')
          password_input = None
        
          for selector in password_selectors:
              try:
                  password_input = self.driver.find_element(By.XPATH, selector)
                  print(f"使用选择器找到密码输入框: {selector}")
                  break
              except:
                  continue
        
          if not password_input:
            # 如果以上选择器都找不到，尝试通用方式
              try:
                  password_input = self.driver.find_element(By.XPATH, "//input[@type='password']")
                  print("使用type='password'找到密码输入框")
              except:
                  try:
                    # 尝试找到所有输入框，取第二个（假设第一个是用户名）
                      all_inputs = self.driver.find_elements(By.TAG_NAME, "input")
                      if len(all_inputs) >= 2:
                          password_input = all_inputs[1]  # 假设第二个是密码
                          print("使用第二个input作为密码输入框")
                  except Exception as e:
                      print(f"未找到密码输入框: {e}")
                      return -1
        
          if password_input:
              password_input.clear()
              password_input.send_keys(self.pd)
              print("已输入密码")
              logging.info('输入密码成功')
        
        # 尝试查找登录按钮
          logging.info('查找登录按钮...')
          login_button = None
        
          for selector in button_selectors:
              try:
                  login_button = self.driver.find_element(By.XPATH, selector)
                  print(f"使用选择器找到登录按钮: {selector}")
                  break
              except:
                  continue
        
          if not login_button:
            # 尝试其他方式找到登录按钮
              try:
                # 查找所有按钮
                  all_buttons = self.driver.find_elements(By.TAG_NAME, "button")
                  for btn in all_buttons:
                      if '登录' in btn.text or '登入' in btn.text or 'login' in btn.text.lower():
                          login_button = btn
                          print(f"通过按钮文本找到登录按钮: {btn.text}")
                          break
              except:
                  pass
        
          if not login_button:
              print("未找到登录按钮")
              return -1
        
        # 点击登录按钮
          logging.info('点击登录按钮...')
          login_button.click()
          print("已点击登录按钮")
        
        # 等待登录完成
          time.sleep(5)
        
        # 保存登录后的页面截图
          self.driver.save_screenshot('after_login.png')
          print("已保存登录后页面截图: after_login.png")
        
        # 检查是否登录成功
          print(f"登录后页面标题: {self.driver.title}")
          print(f"登录后页面URL: {self.driver.current_url}")
        
        # 检查页面内容是否包含登录成功的关键词
          page_text = self.driver.page_source.lower()
          if any(keyword in page_text for keyword in ['座位', '我的', '预约', 'dashboard', 'home']):
              print("检测到登录成功的关键词")
            # 获取cookie
              cookie_list = self.driver.get_cookies()
              self.cookie = ";".join([item["name"] + "=" + item["value"] for item in cookie_list])
              self.cfg["headers"]['Cookie'] = self.cookie
              logging.info("登录成功！")
              return 0
          else:
              print("未检测到登录成功的关键词")
            # 保存页面源码以便调试
              with open('failed_login_page.html', 'w', encoding='utf-8') as f:
                  f.write(self.driver.page_source)
              return -1
            
      except Exception as e:
          logging.error(f"登录失败：{e}")
          import traceback
          traceback.print_exc()
        
        # 保存错误时的页面状态
          try:
              self.driver.save_screenshot('login_error.png')
              print("已保存错误页面截图: login_error.png")
              with open('error_page_source.html', 'w', encoding='utf-8') as f:
                  f.write(self.driver.page_source)
          except:
              pass
        
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
    if not s.login() == 0:
        s.driver.quit()
        logging.info('Login unsuccessful')
        exit(-1)
    if not s.get_user_info() == 0:
        s.driver.quit()
        logging.info('Getting user info unsuccessful')
        exit(-1)
    s.book_favorite_seat(user_config=user_config, seat_config=seat_config)
    s.driver.quit()
    logging.info('End of the program')
