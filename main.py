# main.py
import requests
from web3 import Web3
import schedule
import time
import random
from fake_useragent import UserAgent
from colorama import init, Fore, Style
from banner import show_banner
import logging
import json
from datetime import datetime, timezone, timedelta
import sys
import signal

# 初始化colorama和日志
init(autoreset=True)
logging.basicConfig(filename='log.txt', level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s', encoding='utf-8')

# 初始化UserAgent
ua = UserAgent()

# 北京时间偏移
BEIJING_OFFSET = timedelta(hours=8)

# 优雅退出的标志
should_exit = False

def get_beijing_time():
    """获取当前北京时间"""
    return datetime.now(timezone.utc) + BEIJING_OFFSET

def load_keys_and_proxies():
    """加载私钥和代理"""
    try:
        with open('wallet_key.txt', 'r') as f:
            keys = [line.strip() for line in f if line.strip()]
        with open('proxy.txt', 'r') as f:
            proxies_list = [line.strip() for line in f if line.strip()]
        
        if not keys or not proxies_list:
            raise ValueError("私钥或代理文件为空！")
        if len(keys) != len(proxies_list):
            raise ValueError("私钥数量与代理数量不匹配！")
        return keys, proxies_list
    except FileNotFoundError as e:
        logging.error(f"文件未找到：{e}")
        print(Fore.RED + f"错误：未找到文件 {e}")
        sys.exit(1)
    except ValueError as e:
        logging.error(f"文件错误：{e}")
        print(Fore.RED + f"错误：{e}")
        sys.exit(1)
    except Exception as e:
        logging.error(f"加载文件失败：{e}")
        print(Fore.RED + f"错误：加载文件失败 {e}")
        sys.exit(1)

def sign_message(wallet_address, private_key, message):
    """使用私钥对消息进行签名"""
    try:
        w3 = Web3()
        account = w3.eth.account.from_key(private_key)
        signed_message = w3.eth.account.sign_message(
            w3.eth.account.encode_defunct(text=message),
            private_key=private_key
        )
        return signed_message.signature.hex()
    except Exception as e:
        logging.error(f"签名失败：{e}")
        return None

def login_xink(wallet_address, private_key, proxy):
    """登录x.ink并返回token"""
    try:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": ua.random
        }
        proxies = {"http": proxy, "https": proxy}

        url = f"https://api.x.ink/v1/get-sign-message2?walletAddress={wallet_address}"
        response = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        if response.status_code != 200:
            raise Exception(f"获取签名消息失败，状态码：{response.status_code}")

        sign_message = response.json().get("data", {}).get("message")
        if not sign_message:
            raise Exception("未获取到签名消息")

        signature = sign_message(wallet_address, private_key, sign_message)
        if not signature:
            raise Exception("签名失败")

        payload = {
            "walletAddress": wallet_address,
            "signMessage": sign_message,
            "signature": signature,
            "referrer": None
        }
        url = "https://api.x.ink/v1/verify-signature2"
        response = requests.post(url, headers=headers, json=payload, proxies=proxies, timeout=15)
        if response.status_code != 200:
            raise Exception(f"登录失败，状态码：{response.status_code}")
        
        token = response.json().get("data", {}).get("token")
        if not token:
            raise Exception("未获取到token")
        
        return token
    except Exception as e:
        logging.error(f"账户 {wallet_address} 登录失败: {str(e)}")
        return None

def get_user_info(wallet_address, token, proxy):
    """获取用户信息，判断是否已签到"""
    try:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {token}",
            "User-Agent": ua.random
        }
        proxies = {"http": proxy, "https": proxy}
        url = "https://api.x.ink/v1/me"
        
        response = requests.get(url, headers=headers, proxies=proxies, timeout=15)
        if response.status_code != 200:
            raise Exception(f"获取用户信息失败，状态码：{response.status_code}")
        
        user_data = response.json().get("data", {})
        last_check_in = user_data.get("lastCheckIn")
        if not last_check_in:
            return False, 0
        
        last_check_in_dt = datetime.fromisoformat(last_check_in.replace("Z", "+00:00"))
        today = get_beijing_time().replace(hour=0, minute=0, second=0, microsecond=0)
        last_check_in_beijing = last_check_in_dt + BEIJING_OFFSET
        is_checked_in = last_check_in_beijing.date() == today.date()
        points = user_data.get("points", 0)
        return is_checked_in, points
    except Exception as e:
        logging.error(f"账户 {wallet_address} 获取用户信息失败: {str(e)}")
        return False, 0

def check_in(wallet_address, token, proxy):
    """执行签到"""
    try:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": ua.random
        }
        proxies = {"http": proxy, "https": proxy}
        url = "https://api.x.ink/v1/check-in"
        
        response = requests.post(url, headers=headers, json={}, proxies=proxies, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data.get("success", False):
                points_earned = data.get("pointsEarned", 0)
                return f"签到成功，获得积分：{points_earned}"
            elif data.get("error") == "Unauthorized":
                return "今日已签到"
            else:
                raise Exception(f"签到失败，响应：{data}")
        elif response.status_code in (401, 403):
            return "今日已签到"  # 假设401/403表示已签到或token无效
        else:
            raise Exception(f"签到失败，状态码：{response.status_code}")
    except Exception as e:
        logging.error(f"账户 {wallet_address} 签到失败: {str(e)}")
        return f"签到失败: {str(e)}"

def process_account(wallet_address, private_key, proxy, retry_count=5):
    """处理单个账户的登录和签到"""
    print(f"{Fore.CYAN}正在处理账户: {wallet_address} | 代理: {proxy}{Style.RESET_ALL}")
    logging.info(f"开始处理账户: {wallet_address} | 代理: {proxy}")

    token = None
    attempts = 0
    while attempts < retry_count and not token and not should_exit:
        token = login_xink(wallet_address, private_key, proxy)
        if not token:
            attempts += 1
            if attempts < retry_count:
                print(f"{Fore.YELLOW}账户 {wallet_address} 登录失败，第 {attempts} 次重试...{Style.RESET_ALL}")
                logging.warning(f"账户 {wallet_address} 登录失败，第 {attempts} 次重试")
                time.sleep(10)
            else:
                print(f"{Fore.RED}账户 {wallet_address} 登录失败，已达最大重试次数{Style.RESET_ALL}")
                logging.error(f"账户 {wallet_address} 登录失败，已达最大重试次数")
                return False, 0

    if should_exit:
        return False, 0

    is_checked_in, points = get_user_info(wallet_address, token, proxy)
    if is_checked_in or should_exit:
        if is_checked_in:
            print(f"{Fore.YELLOW}账户 {wallet_address} 今日已签到，当前积分：{points}{Style.RESET_ALL}")
            logging.info(f"账户 {wallet_address} 今日已签到，当前积分：{points}")
        return True, points

    result = check_in(wallet_address, token, proxy)
    if should_exit:
        return False, points

    if "签到成功" in result:
        points_earned = int(result.split("：")[1]) if "获得积分" in result else 0
        new_points = points + points_earned if points > 0 else points_earned
        print(f"{Fore.GREEN}账户 {wallet_address} {result}，当前积分：{new_points}{Style.RESET_ALL}")
        logging.info(f"账户 {wallet_address} {result}，当前积分：{new_points}")
        return True, new_points
    else:
        print(f"{Fore.RED}账户 {wallet_address} {result}{Style.RESET_ALL}")
        logging.error(f"账户 {wallet_address} {result}")
        return False, points

def run_check_in():
    """运行签到任务"""
    try:
        show_banner()
        keys, proxies = load_keys_and_proxies()
        if not keys or not proxies:
            return

        failed_accounts = []
        for i, (key, proxy) in enumerate(zip(keys, proxies)):
            if should_exit:
                break
            w3 = Web3()
            account = w3.eth.account.from_key(key)
            wallet_address = account.address
            success, points = process_account(wallet_address, key, proxy, retry_count=1)
            if not success:
                failed_accounts.append((wallet_address, key, proxy))
            time.sleep(random.uniform(1, 5))  # 随机延迟避免请求过快

        # 重试失败的账户
        for attempt in range(5):
            if should_exit or not failed_accounts:
                break
            print(f"{Fore.YELLOW}第 {attempt + 1} 次重试失败的账户...{Style.RESET_ALL}")
            still_failed = []
            for wallet_address, key, proxy in failed_accounts:
                if should_exit:
                    break
                success, points = process_account(wallet_address, key, proxy, retry_count=1)
                if not success:
                    still_failed.append((wallet_address, key, proxy))
                time.sleep(10)
            failed_accounts = still_failed
    except Exception as e:
        logging.error(f"运行签到任务失败：{str(e)}")
        print(Fore.RED + f"错误：运行签到任务失败 {str(e)}{Style.RESET_ALL}")

def signal_handler(signum, frame):
    """处理信号（如Ctrl+C）以实现优雅退出"""
    global should_exit
    should_exit = True
    print(Fore.YELLOW + "\n正在优雅退出，请稍候...{Style.RESET_ALL}")
    logging.info("接收到退出信号，正在关闭脚本...")
    # 清理资源（如关闭日志、停止任务等）
    try:
        schedule.clear()  # 清除所有定时任务
    except Exception as e:
        logging.error(f"清除定时任务失败：{e}")
    sys.exit(0)

def schedule_task():
    """设置每天10点到12点随机时间运行"""
    try:
        # 注册信号处理程序
        signal.signal(signal.SIGINT, signal_handler)  # 处理Ctrl+C
        signal.signal(signal.SIGTERM, signal_handler)  # 处理终止信号

        hour = 10 + random.randint(0, 1)
        minute = random.randint(0, 59)
        schedule.every().day.at(f"{hour:02d}:{minute:02d}").do(run_check_in)
        print(f"{Fore.GREEN}已安排任务，每天 {hour:02d}:{minute:02d} (北京时间) 执行签到{Style.RESET_ALL}")
        logging.info(f"已安排任务，每天 {hour:02d}:{minute:02d} 执行签到")

        while not should_exit:
            try:
                schedule.run_pending()
                time.sleep(60)
            except KeyboardInterrupt:
                print(Fore.YELLOW + "脚本已手动停止{Style.RESET_ALL}")
                logging.info("脚本手动停止")
                break
            except Exception as e:
                logging.error(f"定时任务循环失败：{str(e)}")
                print(Fore.RED + f"错误：定时任务循环失败 {str(e)}{Style.RESET_ALL}")
                if should_exit:
                    break
                time.sleep(60)  # 短暂暂停后重试
    except Exception as e:
        logging.error(f"设置定时任务失败：{str(e)}")
        print(Fore.RED + f"错误：设置定时任务失败 {str(e)}{Style.RESET_ALL}")

if __name__ == "__main__":
    try:
        # 设置北京时间校准
        schedule_task()
    except Exception as e:
        logging.error(f"启动脚本失败：{str(e)}")
        print(Fore.RED + f"错误：启动脚本失败 {str(e)}{Style.RESET_ALL}")
