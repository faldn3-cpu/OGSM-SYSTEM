import streamlit as st
import pandas as pd
import time
from datetime import datetime
from utils import db, holiday_parser

# ==========================================
#  工具函式
# ==========================================
def get_all_users():
    """從 Users 表取得所有使用者清單"""
    sh, msg = db.get_db_connection("price")
    if not sh: return []
    try:
        ws = sh.worksheet("Users")
        df = pd.DataFrame(ws.get_all_records())
        return df
    except:
        return pd.DataFrame()

def switch_identity(target_name, target_dept, original_email):
    """
    切換當前 Session 的顯示身分 (保持 Role=admin 以觸發唯讀)
    並記錄操作日誌
    """
    # 儲存原始身分 (如果還沒存過)
    if "real_identity" not in st.session_state:
        st.session_state.real_identity = {
            "Name": st.session_state.user_info.get("Name"),
            "Dept": st.session_state.user_info.get("Dept")
        }
    
    # 更新當前偽裝身分
    st.session_state.user_info["Name"] = target_name
    st.session_state.user_info["Dept"] = target_dept
    
    # 【新增】寫入 Log
    db.write_log("GOD_MODE_SWITCH", original_email, f"Switched to view as {target_name}")
    
    # 顯示提示並重整
    st.success(f"👁️ 已切換視角為：{target_name} (唯讀模式)")
    time.sleep(1)
    st.rerun()

def restore_identity(original_email):
    """
    還原為原始管理員身分
    並記錄操作日誌
    """
    if "real_identity" in st.session_state:
        real = st.session_state.real_identity
        st.session_state.user_info["Name"] = real["Name"]
        st.session_state.user_info["Dept"] = real["Dept"]
        del st.session_state.real_identity
        
        # 【新增】寫入 Log
        db.write_log("GOD_MODE_RESTORE", original_email, "Restored admin identity")
        
        st.success("🔙 已還原為管理員身分")
        time.sleep(1)
        st.rerun()

def run_manual_backup(user_email):
    """
    執行手動備份 (強制執行)
    """
    client = db.get_client()
    if not client: return False, "Client Init Failed"
    
    now_str = db.get_tw_time().strftime('%Y%m%d_%H%M')
    backup_folder_id = db.BACKUP_FOLDER_ID
    
    log_msgs = []
    try:
        # 備份 CRM 與 Report DB
        targets = [("report", "業務日報表_資料庫"), ("crm", "客戶關係表單 (回覆)")]
        for key, db_name in targets:
            try:
                sh = client.open(db_name)
                backup_name = f"{db_name}_ManualBackup_{now_str}"
                client.copy(sh.id, title=backup_name, folder_id=backup_folder_id)
                log_msgs.append(f"✅ {db_name} 備份成功")
            except Exception as e:
                log_msgs.append(f"❌ {db_name} 備份失敗: {e}")
        
        final_msg = "\n".join(log_msgs)
        
        # 【新增】寫入 Log
        db.write_log("MANUAL_BACKUP", user_email, f"Result: {final_msg}")
        
        return True, final_msg
    except Exception as e:
        db.write_log("MANUAL_BACKUP_ERROR", user_email, str(e))
        return False, str(e)

def update_holidays_to_config(date_list, user_email):
    """將解析出的假日寫入 System_Config"""
    sh, msg = db.get_db_connection("price")
    if not sh: return False, msg
    
    try:
        ws = sh.worksheet("System_Config")
        # 讀取現有資料
        records = ws.get_all_values()
        header = records[0]
        data = records[1:]
        
        # 過濾掉舊的 Holiday 設定
        new_data = [row for row in data if row[0] != "Holiday"]
        
        # 加入新的 Holiday
        for d_str in date_list:
            # Format: Category, Value, Memo
            new_data.append(["Holiday", d_str, "Manual Upload"])
            
        # 寫回
        ws.clear()
        ws.update(values=[header] + new_data, range_name='A1')
        
        # 【新增】寫入 Log
        db.write_log("UPDATE_HOLIDAYS", user_email, f"Updated {len(date_list)} holidays")
        
        return True, f"已更新 {len(date_list)} 筆假日資料"
    except Exception as e:
        return False, str(e)

# ==========================================
#  主顯示函式
# ==========================================
def show(user_info):
    # 安全檢查: 僅 Admin 可進入
    if user_info.get("Role") != "admin":
        st.error("⛔ 權限不足")
        return

    st.title("⚙️ 系統後台管理")
    user_email = user_info.get("Email", "admin")

    tab1, tab2, tab3 = st.tabs(["👁️ 上帝視角", "📅 行事曆設定", "💾 資料庫維護"])

    # --- Tab 1: 身分切換 ---
    with tab1:
        st.subheader("模擬使用者視角")
        st.info("說明：切換後您將以該業務員的身分瀏覽「日報」與「牌價」。\n系統將強制進入 **唯讀模式**，防止誤改資料。")
        
        # 檢查是否正在模擬中
        if "real_identity" in st.session_state:
            real_name = st.session_state.real_identity["Name"]
            curr_name = user_info.get("Name")
            st.warning(f"⚠️ 目前正在模擬：{curr_name} (原始身分: {real_name})")
            
            if st.button("🔙 結束模擬，還原身分", type="primary"):
                restore_identity(user_email)
        else:
            # 載入使用者清單
            df_users = get_all_users()
            if not df_users.empty:
                # 排除自己
                my_email = user_info.get("Email")
                options = df_users[df_users["Email"] != my_email].to_dict('records')
                
                # 選單顯示格式
                user_map = {f"{u['Name']} ({u['Dept']})": u for u in options}
                selected_label = st.selectbox("選擇模擬對象", options=list(user_map.keys()))
                
                if st.button("開始模擬"):
                    target = user_map[selected_label]
                    switch_identity(target["Name"], target["Dept"], user_email)
            else:
                st.warning("無法讀取使用者清單")

    # --- Tab 2: 行事曆 ---
    with tab2:
        st.subheader("匯入公司行事曆")
        st.markdown("""
        請上傳 Excel 檔案 (.xlsx)，系統將自動解析：
        - **偶數欄**：日期
        - **奇數欄**：備註 (若有內容則視為假日)
        """)
        
        uploaded_file = st.file_uploader("選擇 Excel 檔案", type=["xlsx"])
        if uploaded_file:
            if st.button("解析並更新資料庫"):
                with st.spinner("正在解析..."):
                    holidays = holiday_parser.parse_holiday_excel(uploaded_file)
                    if holidays:
                        st.write(f"預覽 ({len(holidays)} 筆):", holidays[:10], "..." if len(holidays)>10 else "")
                        
                        success, msg = update_holidays_to_config(holidays, user_email)
                        if success:
                            st.success(msg)
                            st.cache_data.clear() # 清除 Config 快取
                        else:
                            st.error(f"更新失敗: {msg}")
                    else:
                        st.warning("未解析到任何假日資料，請檢查 Excel 格式。")

    # --- Tab 3: 備份 ---
    with tab3:
        st.subheader("手動觸發備份")
        st.markdown(f"備份目標資料夾 ID: `{db.BACKUP_FOLDER_ID}`")
        
        if st.button("🚀 立即執行備份 (Report + CRM)"):
            with st.spinner("正在備份中，請勿關閉視窗..."):
                success, msg = run_manual_backup(user_email)
                if success:
                    st.success("備份完成！")
                    st.text(msg)
                else:
                    st.error(f"備份發生錯誤: {msg}")