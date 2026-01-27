import streamlit as st
from datetime import date, datetime, timezone, timedelta
import pandas as pd
import gspread 
import time
from functools import wraps
import logging

# ==========================================
#  設定：客戶關係表單 (CRM) 選項與參數
# ==========================================
CRM_DB_NAME = "客戶關係表單 (回覆)"

# 通路商選項
CRM_OPT_CHANNEL = ["直販", "二次店", "上控廠商", "經銷商", "其他"]
# 競爭通路
CRM_OPT_COMP_CHANNEL = ["無", "能麒", "上菱", "強力", "日遠", "耀毅", "三菱其他通路(瀚衛、惠控、雙象)", "羅昇", "友士", "碁電", "其他"]
# 行動方案
CRM_OPT_ACTION = ["出差到客戶端拜訪", "電話聯繫", "報價事宜", "其他"]
# 是否為流失客戶取回
CRM_OPT_LOST_RECOVERY = [
    "無",
    "曾仁君 - 新林電機", "曾仁君 - 新碩自動",
    "溫達仁 - 崇翌科技", "溫達仁 - 台銨科技", "溫達仁 - 全美自動", "溫達仁 - 泓發機電", "溫達仁 - 協易機械", "溫達仁 - 鑫詮科技", "溫達仁 - 迎傑機電", "溫達仁 - 由田新技", "溫達仁 - 祥侑企業", "溫達仁 - 梭特科技",
    "楊家豪 - 順瀅企業", "楊家豪 - 宇貫企業",
    "謝瑞騏 - 福星機電", "謝瑞騏 - 德世達科", "謝瑞騏 - 磊登自動", "謝瑞騏 - 睿明科技", "謝瑞騏 - 碩聯自動",
    "莊富丞 - 東佑達奈", "莊富丞 - 叡億機械", "莊富丞 - 理豐智動", "莊富丞 - 東典科技",
    "張書偉 - 鴻績工業", "張書偉 - 汎得自動", "張書偉 - 達詳自動", "張書偉 - 捷惠自動", "張書偉 - 威光自動"
]
# 產業別
CRM_OPT_INDUSTRY = [
    "電子產業 (半導體產業 & PCB產業 & AI產業...)", 
    "自動化設備產業(工具機 & 輸送設備 & 廠房設備...)", 
    "節能產業(風車 & 水泵 & 空調 & 工程案...)", 
    "通路商 (經銷商 & 二次店 & 上控...)", 
    "盤廠 & 機械廠", 
    "其他"
]
# 販售或推廣產品
CRM_OPT_PRODUCTS = ["士林品", "三菱品", "松下品", "開關類", "太陽能", "其他"]
# 預計產出日期
CRM_OPT_EST_DATE = [
    "1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月",
    "Q1", "Q2", "Q3", "Q4", "H1", "H2"
]
# 競爭品牌
CRM_OPT_COMP_BRAND = ["台灣品牌", "日系品牌", "歐系品牌", "其他品牌"]
# 客戶所屬
CRM_OPT_OWNER = [
    "曾維崧", "張何達", "曾仁君", "溫達仁", "楊家豪", "莊富丞", "謝瑞騏", "何宛茹", "張書偉", "周柏翰", "葉仁豪", "其他"
]

# ==========================================
#  安全性設定：速率限制
# ==========================================
save_rate_limits = {}

def rate_limit_save(max_calls=5, period=60):
    """針對儲存操作的速率限制 (每分鐘最多 5 次)"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user_email = st.session_state.get('user_email', 'anonymous')
            now = time.time()
            
            if user_email not in save_rate_limits:
                save_rate_limits[user_email] = []
            
            # 清除過期記錄
            save_rate_limits[user_email] = [
                t for t in save_rate_limits[user_email] if now - t < period
            ]
            
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
    """標準系統時間格式 (YYYY-MM-DD HH:MM:SS)"""
    tw_tz = timezone(timedelta(hours=8))
    return datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")

def get_crm_time_str():
    """
    CRM 專用時間格式
    格式範例: 2026/1/26 下午 4:15:05
    """
    tw_tz = timezone(timedelta(hours=8))
    now = datetime.now(tw_tz)
    
    year = now.year
    month = now.month
    day = now.day
    hour = now.hour
    minute = now.minute
    second = now.second
    
    # 判斷上午/下午
    ampm = "上午" if hour < 12 else "下午"
    
    # 轉換為 12 小時制
    display_hour = hour % 12
    if display_hour == 0:
        display_hour = 12
        
    # 格式化: 2026/1/26 下午 4:15:05 (注意月份與日期不補零)
    return f"{year}/{month}/{day} {ampm} {display_hour}:{minute:02d}:{second:02d}"

def format_crm_date(date_val):
    """
    CRM 專用日期格式
    輸入: 2026-01-26 (字串或物件)
    輸出: 2026/1/26 (字串)
    """
    if not date_val: return ""
    try:
        # 如果已經是字串，先解析
        if isinstance(date_val, str):
            # 處理可能的時間格式
            date_val = date_val.split(" ")[0] # 取出日期部分
            d = datetime.strptime(date_val, "%Y-%m-%d")
        elif isinstance(date_val, (date, datetime)):
            d = date_val
        else:
            return str(date_val)
            
        return f"{d.year}/{d.month}/{d.day}"
    except:
        return str(date_val)

def get_default_range(today):
    weekday_idx = today.weekday()
    start = today - timedelta(days=weekday_idx)
    end = today + timedelta(days=1) # 自動顯示到明天
    return start, end

def get_weekday_str(date_obj):
    if not isinstance(date_obj, (date, datetime)): return ""
    weekdays_map = {0:"(一)", 1:"(二)", 2:"(三)", 3:"(四)", 4:"(五)", 5:"(六)", 6:"(日)"}
    try: return weekdays_map.get(date_obj.weekday(), "")
    except: return ""

def get_or_create_user_sheet(client, db_name, real_name):
    try:
        sh = client.open(db_name)
    except Exception as e:
        st.error(f"找不到 Google Sheet:{db_name}")
        logging.error(f"Failed to open sheet: {e}")
        return None

    HEADERS = ["項次", "日期", "星期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]

    try:
        ws = sh.worksheet(real_name)
        return ws
    except gspread.WorksheetNotFound:
        try:
            ws = sh.add_worksheet(title=real_name, rows=1000, cols=10)
            ws.append_row(HEADERS)
            logging.info(f"Created new worksheet for {real_name}")
            return ws
        except Exception as e:
            logging.error(f"Failed to create worksheet: {e}")
            return None

# 【強化修正】Session State 快取讀取函式 (含格式驗證)
def load_data_by_range_cached(ws, start_date, end_date):
    """
    快取版讀取函式
    """
    cache_key = f"data_{start_date}_{end_date}"
    
    if "daily_data_cache" not in st.session_state:
        st.session_state.daily_data_cache = None
    if "daily_data_key" not in st.session_state:
        st.session_state.daily_data_key = ""

    # 1. 嘗試讀取快取
    cache_valid = False
    cached_obj = st.session_state.daily_data_cache
    
    if (cached_obj is not None and 
        st.session_state.daily_data_key == cache_key and 
        isinstance(cached_obj, tuple) and 
        len(cached_obj) == 2):
        cache_valid = True

    if cache_valid:
        return cached_obj

    # 2. 重新讀取
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
        # 1. 整理 current_df
        current_df["日期"] = pd.to_datetime(current_df["日期"], errors='coerce').dt.date
        current_df = current_df.dropna(subset=["日期"])
        current_df["星期"] = current_df["日期"].apply(lambda x: get_weekday_str(x))
        current_df["最後更新時間"] = get_tw_time()
        
        # 2. 整理 all_df
        if not all_df.empty and "日期" in all_df.columns:
            all_df["日期"] = pd.to_datetime(all_df["日期"], errors='coerce').dt.date
            mask_keep = (all_df["日期"] < start_date) | (all_df["日期"] > end_date)
            remaining_df = all_df.loc[mask_keep].copy()
        else:
            remaining_df = pd.DataFrame()

        # 3. 合併
        final_df = pd.concat([remaining_df, current_df], ignore_index=True)
        final_df = final_df.sort_values(by=["日期"], ascending=True)

        # 4. 重新編號
        if "項次" in final_df.columns: final_df = final_df.drop(columns=["項次"])
        final_df.insert(0, "項次", range(1, len(final_df) + 1))

        # 5. 確保欄位順序
        cols_order = ["項次", "日期", "星期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]
        for c in cols_order:
            if c not in final_df.columns: final_df[c] = ""
        final_df = final_df[cols_order]

        final_df = final_df.fillna("")
        final_df["日期"] = final_df["日期"].astype(str)

        # 6. 寫入
        val_list = [final_df.columns.values.tolist()] + final_df.values.tolist()
        ws.clear()
        ws.update(values=val_list, range_name='A1')
        
        if "daily_data_cache" in st.session_state:
            del st.session_state.daily_data_cache

        logging.info(f"Data saved successfully: {len(final_df)} rows")
        return True, "儲存成功"
    except Exception as e:
        logging.error(f"Save failed: {e}")
        return False, str(e)

# ==========================================
#  新增函式：儲存至客戶關係表單
# ==========================================
def save_to_crm_sheet(client, data_dict):
    """將資料寫入客戶關係表單 (回覆)"""
    try:
        sh = client.open(CRM_DB_NAME)
        try:
            ws = sh.worksheet("表單回應 1")
        except:
            ws = sh.sheet1
        
        # 使用專用的格式轉換函式
        timestamp_str = get_crm_time_str()             # 格式: 2026/1/26 下午 4:15:05
        date_str = format_crm_date(data_dict.get("拜訪日期", "")) # 格式: 2026/1/22
        
        row_data = [
            timestamp_str,                  # A1 時間戳記
            data_dict.get("填寫人", ""),     # B1
            data_dict.get("客戶名稱", ""),   # C1
            data_dict.get("通路商", ""),     # D1
            data_dict.get("競爭通路", ""),   # E1
            data_dict.get("行動方案", ""),   # F1
            data_dict.get("客戶性質", ""),   # G1
            data_dict.get("流失取回", ""),   # H1
            data_dict.get("產業別", ""),     # I1
            date_str,                       # J1 拜訪日期
            data_dict.get("推廣產品", ""),   # K1
            data_dict.get("工作內容", ""),   # L1
            data_dict.get("產出日期", ""),   # M1
            data_dict.get("總金額", ""),     # N1
            data_dict.get("依賴事項", ""),   # O1
            data_dict.get("實際行程", ""),   # P1
            data_dict.get("競爭品牌", ""),   # Q1
            data_dict.get("客戶所屬", "")    # R1
        ]
        
        ws.append_row(row_data)
        return True, "上傳成功"
    except Exception as e:
        logging.error(f"Save to CRM failed: {e}")
        return False, f"上傳失敗: {e}"

# ==========================================
#  輸入驗證與清理
# ==========================================
MAX_FIELD_LENGTH = 5000

def sanitize_input(text, max_length=MAX_FIELD_LENGTH):
    if not text: return ""
    text = str(text).strip()
    if len(text) > max_length:
        return text[:max_length]
    return text

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

    cached_current_df, all_df = load_data_by_range_cached(ws, start_date, end_date)
    current_df = cached_current_df.copy()

    # 3. 處理「選取」欄位
    if not current_df.empty:
        if "選取" in current_df.columns:
            current_df = current_df.drop(columns=["選取"])
        current_df.insert(0, "選取", False)
        
        try:
            date_col = pd.to_datetime(current_df["日期"]).dt.date
            tomorrow = today + timedelta(days=1)
            mask_auto_select = (date_col == today) | (date_col == tomorrow)
            current_df.loc[mask_auto_select, "選取"] = True
        except:
            pass

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
                    elif msg == "速率限制":
                        pass
                    else:
                        st.error(f"儲存失敗: {msg}")

    # ==========================================
    #  Part 2: 檢視與編輯清單
    # ==========================================
    st.write("")
    st.subheader(f"📋 工作清單 ({start_date} ~ {end_date})")
    
    edited_df = st.data_editor(
        current_df,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        column_config={
            "選取": st.column_config.CheckboxColumn("選取", width="small", help="勾選以進行操作 (LINE日報或CRM上傳)"),
            "日期": st.column_config.DateColumn("日期", format="YYYY-MM-DD", width="small"),
            "客戶名稱": st.column_config.TextColumn("客戶名稱", width="medium"),
            "客戶分類": st.column_config.SelectboxColumn("客戶分類", width="small", 
                options=["(A) 直賣A級", "(B) 直賣B級", "(C) 直賣C級", "(D-A) 經銷A級", "(D-B) 經銷B級", "(D-C) 經銷C級", "(O) 其它"]),
            "工作內容": st.column_config.TextColumn("工作內容", width="large"),
            "實際行程": st.column_config.TextColumn("實際行程", width="large"),
            "最後更新時間": st.column_config.TextColumn("更新時間", disabled=True, width="small")
        },
        key="data_editor_grid_v3" 
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
            elif msg == "速率限制":
                pass
            else:
                st.error(f"儲存失敗: {msg}")

    st.markdown("---")

    # ==========================================
    #  Part 2.5: 同步至客戶關係表單
    # ==========================================
    st.subheader("🔗 同步至客戶關係表單")

    if "選取" in edited_df.columns:
        selected_crm_rows = edited_df[edited_df["選取"] == True].copy()
    else:
        selected_crm_rows = pd.DataFrame()

    if selected_crm_rows.empty:
        st.info("💡 請在上方表格勾選 **一筆** 資料，即可開啟同步填寫介面。")
    elif len(selected_crm_rows) > 1:
        st.warning("⚠️ 為了確保資料完整性，一次請只勾選 **一筆** 資料進行詳細同步。")
    else:
        row = selected_crm_rows.iloc[0]
        st.success(f"已選取：{row['日期']} - {row['客戶名稱']}")
        
        with st.expander("📝 填寫補充資料並上傳", expanded=True):
            with st.form("crm_sync_form"):
                st.caption("以下資料部分已自動帶入，請補齊剩餘欄位：")
                
                c1, c2, c3 = st.columns(3)
                with c1:
                    f_user = st.text_input("填寫人", value=real_name, disabled=True)
                with c2:
                    f_date = st.text_input("拜訪日期", value=str(row["日期"]), disabled=True)
                with c3:
                    f_client = st.text_input("客戶名稱", value=str(row["客戶名稱"]), disabled=True)
                
                c1, c2 = st.columns(2)
                with c1:
                    f_type = st.text_input("客戶性質 (自動帶入)", value=str(row["客戶分類"]), disabled=True)
                with c2:
                    f_content = st.text_area("拜訪目的/案件/設備 (自動帶入)", value=str(row["工作內容"]), height=68)
                    f_status_desc = st.text_area("案件狀況說明 (自動帶入)", value=str(row["實際行程"]), height=68, help="對應：實際行程")

                st.markdown("---")
                st.markdown("##### 📍 請補填以下資訊")

                col_a, col_b = st.columns(2)
                with col_a:
                    f_owner = st.selectbox("客戶所屬 (偕同拜訪/擔當)", options=CRM_OPT_OWNER, index=0)
                    f_channel = st.selectbox("通路商", options=CRM_OPT_CHANNEL)
                    f_comp_channel = st.selectbox("競爭通路 (選填)", options=CRM_OPT_COMP_CHANNEL)
                    f_action = st.selectbox("行動方案", options=CRM_OPT_ACTION)
                    
                with col_b:
                    f_industry = st.selectbox("產業別", options=CRM_OPT_INDUSTRY)
                    f_products = st.multiselect("推廣產品 (可複選)", options=CRM_OPT_PRODUCTS)
                    f_est_date = st.selectbox("案件預計產出日期", options=CRM_OPT_EST_DATE)
                    f_comp_brand = st.selectbox("競爭品牌", options=CRM_OPT_COMP_BRAND)

                # === 自動判斷流失客戶索引 (新增邏輯，含防呆) ===
                current_client_name = str(row.get("客戶名稱", "")).strip()
                default_lost_idx = 0 # 預設為 "無"
                
                if current_client_name: # 只有當客戶名稱不為空時才進行比對
                    expected_opt = f"{real_name} - {current_client_name}"
                    if expected_opt in CRM_OPT_LOST_RECOVERY:
                        default_lost_idx = CRM_OPT_LOST_RECOVERY.index(expected_opt)
                # ===============================================

                f_lost_rec = st.selectbox(
                    "是否為流失客戶取回 (選填)", 
                    options=CRM_OPT_LOST_RECOVERY,
                    index=default_lost_idx
                )
                
                c_money, c_dep = st.columns([1, 2])
                with c_money:
                    f_amount = st.number_input("案件總金額 (單位: 萬)", min_value=0.0, step=0.1, format="%.1f")
                with c_dep:
                    f_dependency = st.text_input("依賴事項 (選填)")

                submitted = st.form_submit_button("🚀 確認上傳至客戶關係表單", type="primary", use_container_width=True)
                
                if submitted:
                    crm_data = {
                        "填寫人": f_user,
                        "客戶名稱": f_client,
                        "通路商": f_channel,
                        "競爭通路": f_comp_channel if f_comp_channel != "無" else "",
                        "行動方案": f_action,
                        "客戶性質": f_type,
                        "流失取回": f_lost_rec if f_lost_rec != "無" else "",
                        "產業別": f_industry,
                        "拜訪日期": f_date,
                        "推廣產品": ", ".join(f_products),
                        "工作內容": f_content,
                        "產出日期": f_est_date,
                        "總金額": str(f_amount),
                        "依賴事項": f_dependency,
                        "實際行程": f_status_desc,
                        "競爭品牌": f_comp_brand,
                        "客戶所屬": f_owner
                    }
                    
                    with st.spinner("正在上傳資料..."):
                        success, msg = save_to_crm_sheet(client, crm_data)
                        if success:
                            st.success(f"✅ 上傳成功！已寫入「{CRM_DB_NAME}」。")
                        else:
                            st.error(msg)
    
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
        st.info("💡 請在上方表格勾選要傳送的項目 (預設已勾選今天與明天)。")
    else:
        selected_rows = selected_rows.sort_values(by="日期")
        msg_lines = [f"【{real_name} 業務匯報】"]
        unique_dates = selected_rows["日期"].unique()
        
        for d in unique_dates:
            d_str = str(d)
            day_rows = selected_rows[selected_rows["日期"] == d]
            
            header_suffix = ""
            try:
                if d == today + timedelta(days=1): 
                    header_suffix = " (明日計畫)"
                elif d == today: 
                    header_suffix = " (今日實際行程)"
            except: 
                pass

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