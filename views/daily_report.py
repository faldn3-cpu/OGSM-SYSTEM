import streamlit as st
from datetime import date, datetime, timezone, timedelta
import pandas as pd
import gspread 
import time
from functools import wraps
import logging

# ==========================================
#  安全性設定：速率限制
# ==========================================
save_rate_limits = {}

def rate_limit_save(max_calls=5, period=60):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user_email = st.session_state.get('user_email', 'anonymous')
            now = time.time()
            if user_email not in save_rate_limits: save_rate_limits[user_email] = []
            save_rate_limits[user_email] = [t for t in save_rate_limits[user_email] if now - t < period]
            if len(save_rate_limits[user_email]) >= max_calls:
                st.error(f"⚠️ 儲存過於頻繁，請 {period} 秒後再試")
                logging.warning(f"Rate limit exceeded for {user_email} on {func.__name__}")
                return False, "速率限制"
            save_rate_limits[user_email].append(now)
            return func(*args, **kwargs)
        return wrapper
    return decorator

# ==========================================
#  工具函式
# ==========================================
def get_tw_time():
    tw_tz = timezone(timedelta(hours=8))
    return datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")

def get_default_range(today):
    # 【優化】自動顯示到明天，方便輸入明日計畫
    weekday_idx = today.weekday()
    start = today - timedelta(days=weekday_idx)
    end = today + timedelta(days=1) 
    return start, end

def get_weekday_str(date_obj):
    if not isinstance(date_obj, (date, datetime)): return ""
    weekdays_map = {0:"(一)", 1:"(二)", 2:"(三)", 3:"(四)", 4:"(五)", 5:"(六)", 6:"(日)"}
    try: return weekdays_map.get(date_obj.weekday(), "")
    except: return ""

def get_or_create_user_sheet(client, db_name, real_name):
    try: sh = client.open(db_name)
    except Exception as e:
        st.error(f"找不到 Google Sheet:{db_name}")
        return None
    HEADERS = ["項次", "日期", "星期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]
    try: return sh.worksheet(real_name)
    except gspread.WorksheetNotFound:
        try:
            ws = sh.add_worksheet(title=real_name, rows=1000, cols=10)
            ws.append_row(HEADERS)
            return ws
        except Exception: return None

# 【修正三】Session State 快取機制
def load_data_by_range_cached(ws, start_date, end_date):
    """
    快取版讀取函式：
    如果 Session State 中已有該區間的資料，直接回傳，避免一直讀取 Google Sheets。
    """
    cache_key = f"data_{start_date}_{end_date}"
    
    if "daily_data_cache" not in st.session_state:
        st.session_state.daily_data_cache = None
    if "daily_data_key" not in st.session_state:
        st.session_state.daily_data_key = ""

    # 使用快取條件
    if st.session_state.daily_data_cache is not None and st.session_state.daily_data_key == cache_key:
        return st.session_state.daily_data_cache

    # 執行實際讀取
    try:
        data = ws.get_all_records()
        ui_columns = ["日期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]
        if not data: 
            result = (pd.DataFrame(columns=ui_columns), pd.DataFrame())
        else:
            df = pd.DataFrame(data)
            if "項次" in df.columns: df = df.drop(columns=["項次"])
            df = df.fillna("")
            
            for col in ["客戶名稱", "工作內容", "實際行程", "客戶分類", "最後更新時間"]:
                if col in df.columns: df[col] = df[col].astype(str)

            df["日期"] = pd.to_datetime(df["日期"], errors='coerce').dt.date
            mask = (df["日期"] >= start_date) & (df["日期"] <= end_date)
            filtered_df = df.loc[mask].copy().sort_values(by=["日期"], ascending=True).reset_index(drop=True)
            
            display_df = filtered_df[ui_columns].copy() if not filtered_df.empty else pd.DataFrame(columns=ui_columns)
            result = (display_df, df)

        # 寫入快取
        st.session_state.daily_data_cache = result
        st.session_state.daily_data_key = cache_key
        return result
    except Exception as e:
        logging.error(f"Failed to load data: {e}")
        return pd.DataFrame(columns=["日期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]), pd.DataFrame()

@rate_limit_save(max_calls=5, period=60)
def save_to_google_sheet(ws, all_df, current_df, start_date, end_date):
    """將目前的 DataFrame 完整存回 Google Sheet，並清除快取"""
    try:
        current_df["日期"] = pd.to_datetime(current_df["日期"], errors='coerce').dt.date
        current_df = current_df.dropna(subset=["日期"])
        current_df["星期"] = current_df["日期"].apply(lambda x: get_weekday_str(x))
        current_df["最後更新時間"] = get_tw_time()
        
        if not all_df.empty and "日期" in all_df.columns:
            all_df["日期"] = pd.to_datetime(all_df["日期"], errors='coerce').dt.date
            mask_keep = (all_df["日期"] < start_date) | (all_df["日期"] > end_date)
            remaining_df = all_df.loc[mask_keep].copy()
        else:
            remaining_df = pd.DataFrame()

        final_df = pd.concat([remaining_df, current_df], ignore_index=True)
        final_df = final_df.sort_values(by=["日期"], ascending=True)

        if "項次" in final_df.columns: final_df = final_df.drop(columns=["項次"])
        final_df.insert(0, "項次", range(1, len(final_df) + 1))

        cols_order = ["項次", "日期", "星期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]
        for c in cols_order:
            if c not in final_df.columns: final_df[c] = ""
        final_df = final_df[cols_order]
        final_df = final_df.fillna("")
        final_df["日期"] = final_df["日期"].astype(str)

        val_list = [final_df.columns.values.tolist()] + final_df.values.tolist()
        ws.clear()
        ws.update(values=val_list, range_name='A1')
        
        # 儲存後清除快取
        if "daily_data_cache" in st.session_state:
            del st.session_state.daily_data_cache
        
        return True, "儲存成功"
    except Exception as e:
        return False, str(e)

# ==========================================
#  輸入驗證與清理
# ==========================================
MAX_FIELD_LENGTH = 5000 
def sanitize_input(text, max_length=MAX_FIELD_LENGTH):
    if not text: return ""
    text = str(text).strip()
    return text[:max_length] if len(text) > max_length else text

# ==========================================
#  主顯示函式
# ==========================================
def show(client, db_name, user_email, real_name):
    st.title(f"📝 {real_name} 的業務日報")
    ws = get_or_create_user_sheet(client, db_name, real_name)
    if not ws: return

    today = date.today()
    def_start, def_end = get_default_range(today)
    
    with st.expander("📅 切換資料日期區間", expanded=False):
        date_range = st.date_input("選擇區間", (def_start, def_end))
    
    if isinstance(date_range, tuple) and len(date_range) == 2: start_date, end_date = date_range
    elif isinstance(date_range, tuple) and len(date_range) == 1: start_date = end_date = date_range[0]
    else: start_date = end_date = today

    # 【修正三】使用快取讀取
    current_df, all_df = load_data_by_range_cached(ws, start_date, end_date)

    if not current_df.empty:
        current_df.insert(0, "選取", False)
        # 【討論實作】自動勾選今天(實績)與明天(計畫)
        try:
            date_col = pd.to_datetime(current_df["日期"]).dt.date
            tomorrow = today + timedelta(days=1)
            mask_auto_select = (date_col == today) | (date_col == tomorrow)
            current_df.loc[mask_auto_select, "選取"] = True
        except: pass

    # ==========================================
    #  Part 1: 新增工作
    # ==========================================
    st.markdown("### ➕ 新增工作")
    
    with st.container(border=True):
        c1, c2 = st.columns([1, 1])
        with c1:
            inp_date = st.date_input("日期", today)
        with c2:
            inp_type = st.selectbox("客戶分類", 
                ["請選擇", "(A) 直賣A級", "(B) 直賣B級", "(C) 直賣C級", "(D-A) 經銷A級", "(D-B) 經銷B級", "(D-C) 經銷C級", "(O) 其它"],
                index=0
            )
        
        # 【修正二】 Placeholder 文字更新
        inp_client = st.text_input("客戶名稱", placeholder="客戶名稱", max_chars=MAX_FIELD_LENGTH)
        inp_content = st.text_area("工作內容", placeholder="輸入預計行程", height=100, max_chars=MAX_FIELD_LENGTH)
        inp_result = st.text_area("實際行程", placeholder="輸入當日實際行程", height=100, max_chars=MAX_FIELD_LENGTH)

        if st.button("➕ 加入清單", type="primary", use_container_width=True):
            inp_client = sanitize_input(inp_client)
            inp_content = sanitize_input(inp_content)
            inp_result = sanitize_input(inp_result)
            
            if not inp_client:
                st.warning("⚠️ 請輸入客戶名稱")
            else:
                new_row = pd.DataFrame([{
                    "日期": inp_date,
                    "客戶名稱": inp_client,
                    "客戶分類": inp_type if inp_type != "請選擇" else "",
                    "工作內容": inp_content,
                    "實際行程": inp_result,
                    "最後更新時間": get_tw_time()
                }])
                
                if "選取" in current_df.columns:
                    df_to_save = current_df.drop(columns=["選取"])
                else:
                    df_to_save = current_df

                df_to_save = pd.concat([df_to_save, new_row], ignore_index=True)
                
                with st.spinner("正在儲存..."):
                    success, msg = save_to_google_sheet(ws, all_df, df_to_save, start_date, end_date)
                    if success:
                        st.success("✅ 已新增並儲存!")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(f"儲存失敗: {msg}")

    # ==========================================
    #  Part 2: 檢視與編輯清單
    # ==========================================
    st.write("")
    st.subheader(f"📋 工作清單 ({start_date} ~ {end_date})")
    
    # 【修正二】欄位標題更新
    edited_df = st.data_editor(
        current_df,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        column_config={
            "選取": st.column_config.CheckboxColumn("LINE日報", width="small", help="勾選以產生 LINE 報表"),
            "日期": st.column_config.DateColumn("日期", format="YYYY-MM-DD", width="small"),
            "客戶名稱": st.column_config.TextColumn("客戶名稱", width="medium"),
            "客戶分類": st.column_config.SelectboxColumn("客戶分類", width="small", 
                options=["(A) 直賣A級", "(B) 直賣B級", "(C) 直賣C級", "(D-A) 經銷A級", "(D-B) 經銷B級", "(D-C) 經銷C級", "(O) 其它"]),
            "工作內容": st.column_config.TextColumn("工作內容", width="large"),
            "實際行程": st.column_config.TextColumn("實際行程", width="large"),
            "最後更新時間": st.column_config.TextColumn("更新時間", disabled=True, width="small")
        },
        key="data_editor_grid"
    )

    if st.button("💾 儲存修改 (表格編輯後請按我)", type="secondary", use_container_width=True):
         with st.spinner("儲存變更中..."):
            df_to_save = edited_df.drop(columns=["選取"]) if "選取" in edited_df.columns else edited_df
            
            for col in ["客戶名稱", "工作內容", "實際行程"]:
                if col in df_to_save.columns:
                    df_to_save[col] = df_to_save[col].apply(lambda x: sanitize_input(x))
            
            success, msg = save_to_google_sheet(ws, all_df, df_to_save, start_date, end_date)
            if success:
                st.success("✅ 修改已儲存!")
                time.sleep(1)
                st.rerun()
            else:
                st.error(f"儲存失敗: {msg}")

    st.markdown("---")
    
    # ==========================================
    #  Part 3: 產生 LINE 文字
    # ==========================================
    st.subheader("📤 產生 LINE 日報文字")

    if "選取" in edited_df.columns:
        selected_rows = edited_df[edited_df["選取"] == True].copy()
    else:
        selected_rows = pd.DataFrame()
    
    if selected_rows.empty:
        st.info("💡 請在上方表格勾選要傳送的項目。")
    else:
        selected_rows = selected_rows.sort_values(by="日期")
        msg_lines = [f"【{real_name} 業務匯報】"]
        unique_dates = selected_rows["日期"].unique()
        
        for d in unique_dates:
            d_str = str(d)
            day_rows = selected_rows[selected_rows["日期"] == d]
            
            header_suffix = ""
            try:
                if d == today + timedelta(days=1): header_suffix = " (明日計畫)"
                elif d == today: header_suffix = " (今日實績)"
            except: pass

            msg_lines.append(f"\n📅 {d_str}{header_suffix}")
            msg_lines.append("--------------")
            
            for idx, row in day_rows.iterrows():
                c_name = str(row.get("客戶名稱", "")).strip()
                job = str(row.get("工作內容", "")).strip()
                result = str(row.get("實際行程", "")).strip()
                cat = str(row.get("客戶分類", "")).strip()
                
                if not c_name and not job and not result: continue

                msg_lines.append(f"🏢 {c_name} {cat}")
                if job: msg_lines.append(f"📋 計畫：{job}")
                if result: msg_lines.append(f"✅ 實績：{result}")
                msg_lines.append("---")
            
        final_msg = "\n".join(msg_lines)
        st.code(final_msg, language="text")
        st.caption("👆 點擊右上角的「複製圖示」,即可貼到 LINE 群組。")