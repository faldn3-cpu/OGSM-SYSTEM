import streamlit as st
import time
from utils import auth, db

def change_password_in_db(email, new_password):
    """寫入新密碼至 Users 表"""
    sh, msg = db.get_db_connection("price")
    if not sh: return False, "DB 連線失敗"
    
    try:
        ws = sh.worksheet("Users")
        # 尋找使用者列
        cell = ws.find(email)
        if cell:
            # 加密
            hashed = auth.hash_password(new_password)
            # 結構: Email(1), Name(2), Password(3)...
            ws.update_cell(cell.row, 3, hashed)
            return True, "修改成功"
        else:
            return False, "找不到使用者帳號"
    except Exception as e:
        return False, str(e)

def show(user_info):
    st.title("🔑 修改密碼")
    user_email = user_info.get("Email", "")
    
    # 判斷是否為強制修改狀態
    force_mode = user_info.get("ForceChange", False)
    
    if force_mode:
        st.error("⚠️ 您的帳號目前使用預設密碼或安全性不足，請設定新密碼以繼續使用系統。")
        st.info("💡 密碼規則：至少 8 碼，且必須包含英文字母與數字。")

    with st.form("pwd_change_form"):
        # 強制模式下不顯示舊密碼欄位
        if not force_mode:
            old_pw = st.text_input("舊密碼", type="password")
        
        p1 = st.text_input("新密碼", type="password")
        p2 = st.text_input("確認新密碼", type="password")
        
        submit = st.form_submit_button("確認修改", use_container_width=True)
        
        if submit:
            # 1. 驗證輸入
            if not p1 or not p2:
                st.error("請輸入完整資訊")
                return

            if p1 != p2:
                st.error("兩次密碼輸入不一致")
                return
            
            # 2. 驗證強度
            if auth.is_password_weak(p1):
                st.error("❌ 密碼強度不足！需至少 8 碼且包含英數字。")
                return
            
            # 3. 執行修改
            with st.spinner("正在更新密碼..."):
                success, msg = change_password_in_db(user_email, p1)
                
                if success:
                    # 【新增】寫入 Log
                    db.write_log("PASSWORD_CHANGE", user_email, "User changed password")
                    
                    st.success("✅ 密碼已修改！")
                    
                    if force_mode:
                        # 解除強制狀態
                        st.session_state.user_info["ForceChange"] = False
                        st.success("🔒 安全鎖定已解除，即將進入系統...")
                    else:
                        st.info("請下次登入時使用新密碼。")
                    
                    time.sleep(2)
                    st.rerun()
                else:
                    st.error(f"修改失敗: {msg}")