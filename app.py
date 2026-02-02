import streamlit as st
import time
import os
import logging
from datetime import datetime, timedelta, timezone
import extra_streamlit_components as stx  # 【恢復】引入 Cookie 管理套件

# 引入自定義模組
from utils import auth, db, config_loader

# 引入頁面視圖
from views import (
    price_query,
    daily_report,
    report_overview,
    crm_overview,
    user_settings,
    admin_panel
)

# ==========================================
#  安全性與 Log 設定
# ==========================================
logging.basicConfig(
    filename='app_security.log',
    level=logging.WARNING,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ==========================================
#  頁面基本設定
# ==========================================
st.set_page_config(
    page_title="士電業務整合系統 V2.0",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==========================================
#  🛡️ 強力喚醒模式 (Hold the Door)
# ==========================================
if "wake_up" in st.query_params:
    print("⏰ Wake up signal received. Holding connection...")
    st.title("🤖 System is Waking Up...")
    st.write("Holding the door open for 30 seconds...")
    time.sleep(30)
    st.write("Done. System is live.")
    st.stop()

# ==========================================
#  HTTPS 強制檢查
# ==========================================
if 'https_checked' not in st.session_state:
    st.session_state.https_checked = False
if not st.session_state.https_checked:
    st.session_state.https_checked = True

# ==========================================
#  CSS 樣式優化
# ==========================================
st.markdown("""
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: visible !important;}
[data-testid="stDecoration"] {display: none;}
[data-testid="stElementToolbar"] { display: none; }
.stAppDeployButton {display: none;}
[data-testid="stManageAppButton"] {display: none;}

div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlockBorderWrapper"] {
    border: 1px solid rgba(128, 128, 128, 0.2);
    border-radius: 18px;
    padding: 20px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    background-color: var(--secondary-background-color); 
    margin-bottom: 16px;
}

input, select, textarea { font-size: 16px !important; }
button { min-height: 48px !important; }
</style>
""", unsafe_allow_html=True)

# ==========================================
#  Session State 初始化
# ==========================================
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'user_info' not in st.session_state: st.session_state.user_info = {}
if 'login_attempts' not in st.session_state: st.session_state.login_attempts = 0

# 忘記密碼相關 State
if 'reset_stage' not in st.session_state: st.session_state.reset_stage = 0 
if 'reset_otp' not in st.session_state: st.session_state.reset_otp = ""
if 'reset_target_email' not in st.session_state: st.session_state.reset_target_email = ""
if 'reset_otp_time' not in st.session_state: st.session_state.reset_otp_time = 0

# 自動執行備份檢查 (雙數月1號)
db.check_and_run_backup()

# ==========================================
#  【恢復】問候語功能
# ==========================================
def get_greeting():
    # 轉換為台灣時間 (UTC+8)
    tw_now = datetime.utcnow() + timedelta(hours=8)
    h = tw_now.hour
    if h >= 22 or h < 5: return "夜深了，早點休息 🛌"
    elif 5 <= h < 11: return "早安！祝你活力滿滿 ☀️"
    elif 11 <= h < 14: return "午安！記得吃飯休息 🍱"
    elif 14 <= h < 18: return "下午好！繼續加油 💪"
    else: return "晚上好！辛苦了 🌙"

# ==========================================
#  頁面封裝 (Wrapper Functions)
# ==========================================
def run_price_query(): price_query.show(st.session_state.user_info)
def run_daily_report(): daily_report.show(st.session_state.user_info)
def run_report_overview(): report_overview.show(st.session_state.user_info)
def run_crm_overview(): crm_overview.show(st.session_state.user_info)
def run_user_settings(): user_settings.show(st.session_state.user_info)
def run_admin_panel(): admin_panel.show(st.session_state.user_info)

def logout():
    # 【恢復】登出時寫入 Log (Report_DB -> Session Logs)
    u = st.session_state.user_info
    if u:
        auth.write_session_log(u.get("Email"), u.get("Name"), "LOGOUT")

    st.session_state.logged_in = False
    st.session_state.user_info = {}
    st.session_state.reset_stage = 0 # 重置狀態
    st.rerun()

# ==========================================
#  主程式邏輯
# ==========================================
def main():
    # 【恢復】初始化 Cookie 管理器 (需放在最外層)
    cookie_manager = stx.CookieManager()

    # 1. 未登入狀態
    if not st.session_state.logged_in:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown("<br><br>", unsafe_allow_html=True)
            st.header("🔒 士林電機FA 業務系統 V2.0")
            
            # 使用 Tab 分流登入與忘記密碼
            tab_login, tab_reset = st.tabs(["會員登入", "忘記密碼"])
            
            # --- 分頁 1: 登入 ---
            with tab_login:
                # 【恢復】嘗試讀取 Cookie
                last_email = cookie_manager.get("last_email") or ""

                with st.form("login_form"):
                    # 若有 Cookie 則帶入預設值
                    email = st.text_input("Email", value=last_email, placeholder="請輸入 Email")
                    password = st.text_input("密碼", type="password", placeholder="請輸入密碼")
                    
                    # 【恢復】記住帳號勾選框
                    remember_email = st.checkbox("記住帳號", value=True)
                    
                    submit = st.form_submit_button("登入", use_container_width=True)
                    
                    if submit:
                        if not email or not password:
                            st.error("請輸入帳號與密碼")
                        else:
                            success, user_data, msg = auth.login_user(email, password)
                            if success:
                                st.session_state.logged_in = True
                                st.session_state.user_info = user_data
                                st.session_state.login_attempts = 0
                                
                                # 【恢復】處理 Cookie 寫入或刪除
                                if remember_email:
                                    try:
                                        # 設定過期時間為 365 天後
                                        expires = datetime.now(timezone(timedelta(hours=8))) + timedelta(days=365)
                                        cookie_manager.set("last_email", email, expires_at=expires, key="set_last_email_cookie")
                                    except Exception as e:
                                        logging.warning(f"Cookie set failed: {e}")
                                else:
                                    try:
                                        cookie_manager.delete("last_email", key="del_last_email_cookie")
                                    except:
                                        pass
                                
                                st.success(msg)
                                time.sleep(0.5) # 等待 Cookie 寫入
                                st.rerun()
                            else:
                                st.session_state.login_attempts += 1
                                st.error(msg)
                                if st.session_state.login_attempts >= 3:
                                    st.warning("⚠️ 連續失敗多次，帳號可能已被鎖定。")

            # --- 分頁 2: 忘記密碼 ---
            with tab_reset:
                if st.session_state.reset_stage == 0:
                    st.info("輸入您的 Email，系統將發送驗證碼給您。")
                    r_email = st.text_input("註冊 Email", key="reset_email_input")
                    
                    if st.button("發送驗證碼", use_container_width=True):
                        if not r_email:
                            st.error("請輸入 Email")
                        else:
                            with st.spinner("正在發送郵件..."):
                                success, otp, msg = auth.send_otp_email(r_email)
                                if success:
                                    st.session_state.reset_otp = otp
                                    st.session_state.reset_target_email = r_email
                                    st.session_state.reset_otp_time = time.time()
                                    st.session_state.reset_stage = 1
                                    st.success("✅ 驗證碼已發送，10 分鐘內有效")
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.error(msg)
                
                elif st.session_state.reset_stage == 1:
                    st.info(f"驗證碼已發送至 {st.session_state.reset_target_email}")
                    
                    # 檢查逾時 (10分鐘)
                    if time.time() - st.session_state.get('reset_otp_time', 0) > 600:
                        st.error("⏰ 驗證碼已過期，請重新發送")
                        if st.button("返回重試"):
                            st.session_state.reset_stage = 0
                            st.rerun()
                    else:
                        otp_in = st.text_input("輸入驗證碼 (6碼)", max_chars=6)
                        new_pw = st.text_input("設定新密碼", type="password")
                        new_pw_chk = st.text_input("確認新密碼", type="password")
                        
                        if st.button("確認重置", use_container_width=True):
                            if otp_in != st.session_state.reset_otp:
                                st.error("❌ 驗證碼錯誤")
                            elif new_pw != new_pw_chk:
                                st.error("❌ 兩次密碼不一致")
                            elif auth.is_password_weak(new_pw):
                                st.error("❌ 密碼強度不足 (需8碼且含英數字)")
                            else:
                                with st.spinner("更新密碼中..."):
                                    success, msg = auth.update_password_in_db(st.session_state.reset_target_email, new_pw)
                                    if success:
                                        st.success("✅ 密碼已重置！請切換至登入分頁重新登入。")
                                        st.session_state.reset_stage = 0
                                        st.session_state.reset_otp = ""
                                        time.sleep(3)
                                        st.rerun()
                                    else:
                                        st.error(f"重置失敗: {msg}")
                        
                        if st.button("← 返回"):
                            st.session_state.reset_stage = 0
                            st.rerun()

        # 系統資訊 footer
        st.markdown("---")
        st.caption(f"System Boot: {db.get_tw_time().strftime('%Y-%m-%d %H:%M:%S')}")
        return

    # 2. 已登入狀態
    user = st.session_state.user_info
    role = user.get("Role", "sales").lower()
    force_change = user.get("ForceChange", False)

    # 側邊欄資訊
    with st.sidebar:
        st.write(f"👤 **{user.get('Name')}**")
        # 【恢復】問候語
        st.caption(get_greeting())
        st.caption(f"部門: {user.get('Dept')} | 權限: {role}")
        
        if force_change:
            st.error("⚠️ 請立即修改密碼。")
        
        st.markdown("---")
        if st.button("👋 登出系統", use_container_width=True):
            logout()
        
        st.markdown("---")
        # 【恢復】檔案版本時間顯示
        try:
            f_time = datetime.fromtimestamp(os.path.getmtime(__file__)) + timedelta(hours=8)
            ver_str = f_time.strftime('%Y-%m-%d %H:%M')
        except:
            ver_str = "Latest"
        st.caption(f"Ver: {ver_str} (V2.0)")

    # 3. 路由定義 (st.navigation)
    pg_price = st.Page(run_price_query, title="💰 牌價查詢", icon="💰", default=True)
    pg_report = st.Page(run_daily_report, title="📝 填寫日報", icon="📝")
    pg_settings = st.Page(run_user_settings, title="🔑 修改密碼", icon="🔑")
    pg_overview_rpt = st.Page(run_report_overview, title="📊 日報總覽", icon="📊")
    pg_overview_crm = st.Page(run_crm_overview, title="📈 CRM 商機", icon="📈")
    pg_admin = st.Page(run_admin_panel, title="⚙️ 後台管理", icon="⚙️")

    # 4. 權限路由邏輯 (Role-Based Access Control)
    if force_change:
        # 強制改密碼模式：鎖定只能看修改密碼頁
        pg = st.navigation([pg_settings])
    else:
        common_pages = [pg_price, pg_report, pg_settings]
        manager_pages = [pg_overview_rpt, pg_overview_crm]
        admin_pages = [pg_admin]

        nav_structure = {}
        nav_structure["一般功能"] = common_pages
        nav_structure["報表中心"] = manager_pages # Sales 也能看到，由 View 內部控管資料
        
        if role == "admin":
            nav_structure["系統管理"] = admin_pages

        pg = st.navigation(nav_structure)

    # 執行頁面
    pg.run()

if __name__ == "__main__":
    main()