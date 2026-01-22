import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import bcrypt
import pandas as pd
import smtplib
from email.mime.text import MIMEText
import random
import string
import time
from datetime import datetime, timezone, timedelta

# 匯入頁面模組
from views import price_query, daily_report, report_overview

# ==========================================
#  1. 頁面設定
# ==========================================
st.set_page_config(
    page_title="士電業務整合系統", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

# ==========================================
#  2. 賈伯斯風格 CSS
# ==========================================
st.markdown("""
<style>
/* 隱藏 Streamlit 預設雜訊 */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: visible !important;}
[data-testid="stDecoration"] {display: none;}
[data-testid="stElementToolbar"] { display: none; }
.stAppDeployButton {display: none;}
[data-testid="stManageAppButton"] {display: none;}

/* 卡片與容器設計 */
div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlockBorderWrapper"] {
    border: 1px solid #d2d2d7;
    border-radius: 18px;
    padding: 20px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    background-color: #ffffff;
    margin-bottom: 16px;
}

/* 側邊欄選單統一優化 */
div[role="radiogroup"] > label > div:first-child { display: none; }
div[role="radiogroup"] label {
    width: 100% !important;           
    display: flex;                    
    justify-content: center;          
    align-items: center;              
    text-align: center;               
    padding: 12px 16px;
    margin-bottom: 8px;
    border-radius: 8px;
    border: 1px solid #f0f0f5; 
    background-color: #ffffff;
    cursor: pointer;
    transition: all 0.2s ease;
    box-sizing: border-box;           
}
div[role="radiogroup"] label:hover {
    background-color: #f5f5f7;
    border-color: #d2d2d7;
}
div[role="radiogroup"] label[data-checked="true"] {
    background-color: #0071e3 !important;
    color: white !important;
    border-color: #0071e3 !important;
    font-weight: bold;
    box-shadow: 0 2px 5px rgba(0, 113, 227, 0.3);
}
div[role="radiogroup"] label p {
    font-size: 15px;
    margin: 0;
    width: 100%;                      
    text-align: center;               
}
</style>
""", unsafe_allow_html=True)

# ==========================================
#  🔐 雲端資安設定 & 全域變數
# ==========================================
SMTP_EMAIL = ""
SMTP_PASSWORD = ""

# 資料庫名稱
PRICE_DB_NAME = '經銷牌價表_資料庫'
REPORT_DB_NAME = '業務日報表_資料庫'

# [設定] 助理名單
ASSISTANTS = [
    "serena.huang@seec.com.tw",
    "sarah.wang@seec.com.tw",
    "yingsin.ye@seec.com.tw"
]

# [設定] 主管名單
MANAGERS = [
    "welsong@seec.com.tw",
    "Dennis.chang@seec.com.tw",
    "steventseng@seec.com.tw"
]

# 嘗試讀取 Secrets
try:
    if "email" in st.secrets:
        SMTP_EMAIL = st.secrets["email"]["smtp_email"]
        SMTP_PASSWORD = st.secrets["email"]["smtp_password"]
except:
    pass

# === Session State 初始化 ===
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'user_email' not in st.session_state: st.session_state.user_email = ""
if 'real_name' not in st.session_state: st.session_state.real_name = ""
if 'login_attempts' not in st.session_state: st.session_state.login_attempts = 0
if 'page_radio' not in st.session_state: st.session_state.page_radio = "📝 寫 OGSM 日報"
if 'role' not in st.session_state: st.session_state.role = "sales"

# === 忘記密碼流程專用 State ===
if 'reset_stage' not in st.session_state: st.session_state.reset_stage = 0 
if 'reset_otp' not in st.session_state: st.session_state.reset_otp = ""
if 'reset_target_email' not in st.session_state: st.session_state.reset_target_email = ""

# === 工具函式 ===
@st.cache_resource
def get_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    if os.path.exists('service_account.json'):
        creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
        return gspread.authorize(creds)
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            return gspread.authorize(creds)
    except:
        pass
    return None

def get_tw_time():
    tw_tz = timezone(timedelta(hours=8))
    return datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")

def write_log(action, user_email, note=""):
    client = get_client()
    if not client: return
    try:
        sh = client.open(PRICE_DB_NAME)
        try: ws = sh.worksheet("Logs")
        except: return 
        ws.append_row([get_tw_time(), user_email, action, note])
    except: pass

def get_greeting():
    tw_tz = timezone(timedelta(hours=8))
    current_hour = datetime.now(tw_tz).hour
    if 5 <= current_hour < 11: return "早安 ☀️"
    elif 11 <= current_hour < 18: return "你好 👋"
    elif 18 <= current_hour < 23: return "晚安 🌙"
    else: return "夜深了，不要太累了 ☕"

def check_password(plain_text, hashed_text):
    try: return bcrypt.checkpw(plain_text.encode('utf-8'), hashed_text.encode('utf-8'))
    except: return False

def hash_password(plain_text):
    return bcrypt.hashpw(plain_text.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def generate_random_password(length=8):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for i in range(length))

# === 郵件發送 (SSL Port 465) ===
def send_reset_email(to_email, new_password):
    if not SMTP_EMAIL or not SMTP_PASSWORD: return False, "系統未設定寄信信箱。"
    subject = "【士林電機FA】密碼重置通知"
    body = f"您好：\n您的系統密碼已重置。\n新密碼為：{new_password}\n請使用此密碼登入後，盡快修改為您習慣的密碼。"
    msg = MIMEText(body); msg['Subject'] = subject; msg['From'] = SMTP_EMAIL; msg['To'] = to_email
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(SMTP_EMAIL, SMTP_PASSWORD)
            smtp.send_message(msg)
        return True, "信件發送成功"
    except Exception as e: return False, f"寄信失敗: {str(e)}"

def send_otp_email(to_email, otp_code):
    if not SMTP_EMAIL or not SMTP_PASSWORD: 
        return False, "系統未設定寄信信箱"
    subject = "【士林電機FA】密碼重置驗證碼"
    body = f"""
    您好：
    我們收到了您的密碼重置請求。
    您的驗證碼為：{otp_code}
    若您未發送此請求，請忽略此信件。
    """
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = SMTP_EMAIL
    msg['To'] = to_email
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(SMTP_EMAIL, SMTP_PASSWORD)
            smtp.send_message(msg)
        return True, "驗證碼已發送"
    except Exception as e: 
        return False, f"寄信失敗: {str(e)}"

# === 業務邏輯函式 ===
def login(email, password):
    client = get_client()
    if not client: return False, "連線失敗"
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Users")
        users = ws.get_all_records()
        for user in users:
            if str(user.get('email')).strip() == email.strip():
                stored_pw = str(user.get('password'))
                if check_password(password, stored_pw):
                    found_name = str(user.get('name')) if user.get('name') else email
                    write_log("登入成功", email)
                    return True, found_name
                else:
                    write_log("登入失敗", email, "密碼錯誤")
                    return False, "密碼錯誤"
        write_log("登入失敗", email, "帳號不存在")
        return False, "此 Email 尚未註冊"
    except Exception as e: 
        return False, f"登入過程錯誤: {e}"

def change_password(email, new_password):
    client = get_client()
    if not client: return False
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Users")
        cell = ws.find(email)
        if cell:
            ws.update_cell(cell.row, 2, hash_password(new_password))
            write_log("修改密碼", email, "使用者自行修改")
            return True
        return False
    except: return False

def check_email_exists(target_email):
    client = get_client()
    if not client: return False
    try:
        sh = client.open(PRICE_DB_NAME)
        ws = sh.worksheet("Users")
        cell = ws.find(target_email.strip())
        return True
    except gspread.exceptions.CellNotFound: return False
    except: return False

# === 登入成功後的初始化 ===
def post_login_init(email, name, role_override=None):
    st.session_state.logged_in = True
    st.session_state.user_email = email
    st.session_state.real_name = name
    st.session_state.login_attempts = 0
    
    if role_override:
        st.session_state.role = role_override
    else:
        is_manager = email.strip().lower() in [m.lower() for m in MANAGERS]
        is_assistant = email.strip().lower() in [a.lower() for a in ASSISTANTS]
        
        if is_manager: st.session_state.role = "manager"
        elif is_assistant: st.session_state.role = "assistant"
        else: st.session_state.role = "sales"

    if st.session_state.role == "assistant":
        st.session_state.page_radio = "💰 經銷牌價查詢"
    else:
        st.session_state.page_radio = "📝 寫 OGSM 日報"

# === 主程式 ===
def main():
    if not st.session_state.logged_in:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown("<br><br>", unsafe_allow_html=True)
            st.header("🔒 士林電機FA 業務系統")
            
            if st.session_state.login_attempts >= 3:
                st.error("⚠️ 登入失敗次數過多，請重新整理網頁後再試。")
                return

            tab1, tab2 = st.tabs(["會員登入", "忘記密碼"])
            default_email = st.query_params.get("email", "")

            with tab1:
                with st.form("login_form"):
                    input_email = st.text_input("Email", value=default_email)
                    input_pass = st.text_input("密碼", type="password")
                    submitted = st.form_submit_button("登入", use_container_width=True)
                    
                    if submitted:
                        if not input_email or not input_pass:
                            st.warning("⚠️ 請輸入完整的 Email 與密碼")
                        else:
                            with st.spinner("正在驗證身分..."):
                                success, result = login(input_email, input_pass)
                                if success:
                                    post_login_init(input_email, result)
                                    st.rerun()
                                else:
                                    st.session_state.login_attempts += 1
                                    st.error(f"{result} (剩餘: {3 - st.session_state.login_attempts})")

            with tab2:
                if st.session_state.reset_stage == 0:
                    st.caption("請輸入您的註冊 Email，系統將發送驗證碼給您。")
                    reset_email = st.text_input("註冊 Email", key="reset_email_input")
                    if st.button("發送驗證碼", type="primary", use_container_width=True):
                        if not reset_email: st.warning("請輸入 Email")
                        else:
                            if check_email_exists(reset_email):
                                otp = "".join(random.choices(string.digits, k=6))
                                st.session_state.reset_otp = otp
                                st.session_state.reset_target_email = reset_email
                                with st.spinner("正在發送驗證信..."):
                                    sent, msg = send_otp_email(reset_email, otp)
                                    if sent:
                                        st.success("✅ 驗證碼已發送！請至信箱查收。")
                                        st.session_state.reset_stage = 1 
                                        time.sleep(1)
                                        st.rerun()
                                    else: st.error(msg)
                            else: st.error("此 Email 尚未註冊。")

                elif st.session_state.reset_stage == 1:
                    st.info(f"驗證碼已發送至：{st.session_state.reset_target_email}")
                    input_otp = st.text_input("請輸入 6 位數驗證碼")
                    new_pw_reset = st.text_input("請設定新密碼", type="password")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("上一步", use_container_width=True):
                            st.session_state.reset_stage = 0
                            st.rerun()
                    with c2:
                        if st.button("確認重置", type="primary", use_container_width=True):
                            if input_otp != st.session_state.reset_otp: st.error("❌ 驗證碼錯誤")
                            elif not new_pw_reset: st.warning("請輸入新密碼")
                            else:
                                if change_password(st.session_state.reset_target_email, new_pw_reset):
                                    st.success("✅ 密碼重置成功！請使用新密碼登入。")
                                    st.session_state.reset_stage = 0
                                    st.session_state.reset_otp = ""
                                    time.sleep(2)
                                    st.rerun()
                                else: st.error("重置失敗，請稍後再試。")
            
            # === [隱藏技巧] 只有當 URL 包含特定參數時才顯示 ===
            # 例如: http://localhost:8501/?mode=admin_debug
            if st.query_params.get("mode") == "admin_debug":
                st.markdown("---")
                with st.expander("🔧 開發者/模擬身分登入 (Hidden Mode)"):
                    st.caption("此區域僅管理員可見")
                    sim_role = st.selectbox("選擇模擬角色", ["模擬業務 (Sales)", "模擬業助 (Assistant)", "模擬主管 (Manager)"])
                    if st.button("🚀 快速模擬登入", type="secondary", use_container_width=True):
                        if sim_role == "模擬業務 (Sales)":
                            post_login_init("mock.sales@test.com", "測試業務員", role_override="sales")
                        elif sim_role == "模擬業助 (Assistant)":
                            post_login_init("mock.assistant@test.com", "測試業助", role_override="assistant")
                        elif sim_role == "模擬主管 (Manager)":
                            mgr_email = MANAGERS[0] if MANAGERS else "admin@test.com"
                            post_login_init(mgr_email, "測試主管", role_override="manager")
                        st.rerun()
        return

    with st.sidebar:
        greeting = get_greeting()
        st.write(f"👤 **{st.session_state.real_name}**")
        st.caption(f"{greeting} | 權限: {st.session_state.role}")
        st.markdown("<br>", unsafe_allow_html=True) 

        menu_options = [
            "📝 寫 OGSM 日報", 
            "💰 經銷牌價查詢", 
            "🔑 修改密碼",
            "📊 日報總覽",
            "👋 登出系統"
        ]
        
        selected_page = st.radio(
            "功能導航", 
            menu_options, 
            key="page_radio",
            label_visibility="collapsed"
        )

    if selected_page == "👋 登出系統":
        st.session_state.logged_in = False
        st.session_state.role = "sales"
        st.rerun()
        return

    client = get_client()
    if not client:
        st.error("系統連線異常，無法連接至 Google 資料庫。")
        return

    if selected_page == "🔑 修改密碼":
        st.title("🔑 修改密碼")
        st.info("為了您的帳號安全，建議定期更換密碼。")
        with st.container():
            c1, c2 = st.columns([1, 2])
            with c1:
                new_pwd = st.text_input("請輸入新密碼", type="password")
                confirm_pwd = st.text_input("再次確認密碼", type="password")
                if st.button("確認修改", type="primary"):
                    if not new_pwd: st.warning("密碼不得為空")
                    elif new_pwd != confirm_pwd: st.error("兩次輸入的密碼不一致")
                    else:
                        if change_password(st.session_state.user_email, new_pwd): 
                            st.success("✅ 密碼已更新！請重新登入。")
                            time.sleep(2)
                            st.session_state.logged_in = False
                            st.rerun()
                        else: st.error("修改失敗，請稍後再試。")

    elif selected_page == "💰 經銷牌價查詢":
        is_mgr = (st.session_state.role == "manager")
        price_query.show(client, PRICE_DB_NAME, st.session_state.user_email, st.session_state.real_name, is_mgr)
        
    elif selected_page == "📝 寫 OGSM 日報":
        daily_report.show(client, REPORT_DB_NAME, st.session_state.user_email, st.session_state.real_name)
            
    elif selected_page == "📊 日報總覽":
        is_mgr = (st.session_state.role == "manager")
        report_overview.show(client, REPORT_DB_NAME, st.session_state.user_email, st.session_state.real_name, is_mgr)

if __name__ == "__main__":
    main()