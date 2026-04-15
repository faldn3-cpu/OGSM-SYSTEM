import streamlit as st
import requests
from datetime import date, datetime, timezone, timedelta
import pandas as pd
import gspread 
import time
from functools import wraps
import logging
import streamlit.components.v1 as components 

# === CRM 設定與選項 ===
CRM_DB_NAME = "客戶關係表單 (回覆)"
CRM_OPT_CHANNEL = ["直販", "二次店", "上控廠商", "經銷商", "其他"]
CRM_OPT_COMP_CHANNEL = ["無", "能麒", "上菱", "強力", "日遠", "耀毅", "三菱其他通路(瀚衛、惠控、雙象)", "羅昇", "友士", "碁電", "其他"]
CRM_OPT_ACTION = ["出差到客戶端拜訪", "電話聯繫 & 報價事宜 & 其他"]
CRM_OPT_LOST_RECOVERY = [
    "無", "曾仁君 - 新林電機、新碩自動", "溫達仁 - 崇翌科技、台銨科技、全美自動、泓發機電、協易機械、鑫詮科技、迎傑機電、由田新技、祥侑企業、梭特科技",
    "楊家豪 - 順瀅企業、宇貫企業", "謝瑞騏 - 福星機電、德世達科、磊登自動、睿明科技、碩聯自動",
    "莊富丞 - 東佑達奈、叡億機械、理豐智動、東典科技", "張書偉 - 鴻績工業、汎得自動、達詳自動、捷惠自動"
]
CRM_OPT_INDUSTRY = ["電子產業 (半導體產業 & PCB產業 & AI產業...)", "自動化設備產業(工具機 & 輸送設備 & 廠房設備...)", "節能產業(風車 & 水泵 & 空調 & 工程案...)", "通路商 (經銷商 & 二次店 & 上控...)", "盤廠 & 機械廠", "其他"]
CRM_OPT_PRODUCTS = ["士林品(變頻器、伺服、小型PLC、人機介面、溫控器、SD-INV)", "三菱品(變頻器、伺服、小型PLC、大型PLC、人機介面、運動控制器、機械手臂)", "松下品(感測器、雷射雕刻機)、IDEC、台灣氣立CHELIC", "減速機(主推Nidec、利茗、松品)", "開關類(士林品牌)", "太陽能", "其他(溫控器、警示燈)"]
CRM_OPT_EST_DATE = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月", "Q1(第一季)", "Q2(第二季)", "Q3(第三季)", "Q4(第四季)", "H1(上半年)", "H2(下半年)", "明年"]
CRM_OPT_COMP_BRAND = ["台灣品牌", "日系品牌", "歐系品牌", "其他品牌"]
CRM_OPT_OWNER = ["張何達", "曾仁君", "邱文輝", "葉仁豪", "溫達仁", "楊家豪", "莊富丞", "謝瑞騏", "何宛茹", "張書偉"]

TW_HOLIDAYS = ["2026-01-01"]
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
                return False, "速率限制"
            save_rate_limits[user_email].append(now)
            return func(*args, **kwargs)
        return wrapper
    return decorator

def get_tw_time():
    tw_tz = timezone(timedelta(hours=8))
    return datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")

def get_default_range(today):
    weekday_idx = today.weekday()
    start = today - timedelta(days=weekday_idx)
    end = today + timedelta(days=6)
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
        except Exception as e: return None

def load_data_by_range_cached(ws, start_date, end_date):
    cache_key = f"data_{ws.title}_{start_date}_{end_date}"
    if "daily_data_cache" not in st.session_state: st.session_state.daily_data_cache = None
    if "daily_data_key" not in st.session_state: st.session_state.daily_data_key = ""
    
    cached_obj = st.session_state.daily_data_cache
    if (cached_obj is not None and st.session_state.daily_data_key == cache_key and isinstance(cached_obj, tuple) and len(cached_obj) == 2):
        return cached_obj

    try:
        data = ws.get_all_records()
        ui_columns = ["日期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]
        if not data: result = (pd.DataFrame(columns=ui_columns), pd.DataFrame())
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
        return pd.DataFrame(columns=["日期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]), pd.DataFrame()

def sanitize_csv_field(value):
    if not isinstance(value, str): return value
    val_str = str(value).strip()
    dangerous_chars = ['=', '+', '-', '@']
    if val_str and val_str[0] in dangerous_chars: return "'" + val_str
    return value

@rate_limit_save(max_calls=5, period=60)
def save_to_google_sheet(ws, all_df, current_df, start_date, end_date):
    try:
        current_df["日期"] = pd.to_datetime(current_df["日期"], errors='coerce').dt.date
        current_df = current_df.dropna(subset=["日期"])
        current_df["星期"] = current_df["日期"].apply(lambda x: get_weekday_str(x))
        current_df["最後更新時間"] = get_tw_time()
        
        if not all_df.empty and "日期" in all_df.columns:
            all_df["日期"] = pd.to_datetime(all_df["日期"], errors='coerce').dt.date
            mask_keep = (all_df["日期"] < start_date) | (all_df["日期"] > end_date)
            remaining_df = all_df.loc[mask_keep].copy()
        else: remaining_df = pd.DataFrame()

        final_df = pd.concat([remaining_df, current_df], ignore_index=True)
        final_df = final_df.sort_values(by=["日期"], ascending=True)

        if "項次" in final_df.columns: final_df = final_df.drop(columns=["項次"])
        final_df.insert(0, "項次", range(1, len(final_df) + 1))

        cols_order = ["項次", "日期", "星期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]
        for c in cols_order:
            if c not in final_df.columns: final_df[c] = ""
        final_df = final_df[cols_order].fillna("")
        final_df["日期"] = final_df["日期"].astype(str)

        if hasattr(final_df, 'map') and callable(getattr(pd.DataFrame, 'map', None)): final_df = final_df.map(sanitize_csv_field)
        else: final_df = final_df.applymap(sanitize_csv_field)

        val_list = [final_df.columns.values.tolist()] + final_df.values.tolist()
        ws.clear()
        ws.update(values=val_list, range_name='A1')
        
        if "daily_data_cache" in st.session_state: del st.session_state.daily_data_cache
        if "daily_data_key" in st.session_state: del st.session_state.daily_data_key
        return True, "儲存成功"
    except Exception as e: return False, str(e)

def ensure_list(data):
    if not data: return []
    if isinstance(data, list): return data
    if isinstance(data, str): return [x.strip() for x in data.split(",")] if "," in data else [data]
    return list(data)

def save_to_crm_sheet(client, crm_data):
    url = "https://docs.google.com/forms/d/e/1FAIpQLSdb1oeYmCenAjvRjFzYfKVWkIBzW105wb2K-JTj4YgJCFwkJQ/formResponse"
    visit_date = crm_data.get("拜訪日期")
    visit_date_str = visit_date.strftime("%Y-%m-%d") if hasattr(visit_date, 'strftime') else str(visit_date)

    payload_list = [
        ("entry.96119068", crm_data.get("填寫人")), ("entry.2111504476", crm_data.get("客戶名稱")),
        ("entry.1357642524", crm_data.get("通路商")), ("entry.1714915871", crm_data.get("行動方案")),
        ("entry.934052072", crm_data.get("客戶性質")), ("entry.1451405577", crm_data.get("產業別")),
        ("entry.516181115", visit_date_str), ("entry.783279195", crm_data.get("工作內容")),
        ("entry.1781871147", crm_data.get("產出日期", "")), ("entry.1117419766", str(crm_data.get("總金額", ""))),
        ("entry.1488606205", crm_data.get("實際行程")), ("entry.850004033", crm_data.get("客戶所屬"))
    ]

    lost_rec = crm_data.get("流失取回", "")
    if lost_rec and lost_rec != "無": payload_list.append(("entry.152392267", lost_rec))
    comp_chan = crm_data.get("競爭通路", "")
    if comp_chan and comp_chan != "無": payload_list.append(("entry.1890292749", comp_chan))
    dependency = crm_data.get("依賴事項", "")
    if dependency: payload_list.append(("entry.847639223", dependency))

    for p in ensure_list(crm_data.get("推廣產品_list")): payload_list.append(("entry.1642331636", p))
    for b in ensure_list(crm_data.get("競爭品牌_list")): payload_list.append(("entry.1280930959", b))

    try:
        response = requests.post(url, data=payload_list)
        if response.status_code == 200: return True, "✅ 上傳成功！"
        else: return False, f"上傳失敗 (400)"
    except Exception as e: return False, f"發生連線錯誤: {str(e)}"

def sanitize_input(text, max_length=5000):
    if not text: return ""
    text = str(text).strip()
    return text[:max_length] if len(text) > max_length else text

def render_copy_button(text_to_copy):
    safe_text = text_to_copy.replace("`", "\`").replace("\\", "\\\\").replace("$", "\\$")
    html_code = f"""
    <div style="margin-top: 5px; margin-bottom: 10px;">
        <button onclick="copyToClipboard()" style="background-color: #00C851; color: white; border: none; padding: 10px 20px; font-size: 16px; cursor: pointer; border-radius: 8px; width: 100%;">📋 點擊複製 LINE 日報文字</button>
        <div id="copy_status" style="color: green; font-size: 14px; margin-top: 5px;"></div>
    </div>
    <script>
    function copyToClipboard() {{
        const text = `{safe_text}`;
        const statusDiv = document.getElementById("copy_status");
        if (navigator.clipboard && window.isSecureContext) {{
            navigator.clipboard.writeText(text).then(()=>{{statusDiv.innerText = "✅ 複製成功！"; setTimeout(()=>statusDiv.innerText="",3000);}}, ()=>fallbackCopyTextToClipboard(text));
        }} else fallbackCopyTextToClipboard(text);
    }}
    function fallbackCopyTextToClipboard(text) {{
        const statusDiv = document.getElementById("copy_status");
        var textArea = document.createElement("textarea");
        textArea.value = text; textArea.style.position = "fixed"; document.body.appendChild(textArea);
        textArea.focus(); textArea.select();
        try {{ document.execCommand('copy'); statusDiv.innerText = '✅ 複製成功！'; }} catch (err) {{ statusDiv.innerText = '❌ 無法複製'; }}
        document.body.removeChild(textArea); setTimeout(()=>statusDiv.innerText="",3000);
    }}
    </script>
    """
    components.html(html_code, height=80)

def show(client, db_name, user_email, real_name):
    st.markdown("""
        <style>
        ::-webkit-scrollbar { width: 12px; height: 12px; }
        ::-webkit-scrollbar-thumb { background: #bbb; border-radius: 6px; }
        </style>
    """, unsafe_allow_html=True)
    
    if "dr_mode" not in st.session_state: st.session_state.dr_mode = "main" 
    if "dr_sync_data" not in st.session_state: st.session_state.dr_sync_data = None 

    ws = get_or_create_user_sheet(client, db_name, real_name)
    if not ws: return

    today = date.today()
    def_start, def_end = get_default_range(today)
    
    if st.session_state.dr_mode == "main":
        with st.expander("📅 切換資料日期區間", expanded=False):
            date_range = st.date_input("選擇區間", (def_start, def_end))
        if isinstance(date_range, tuple) and len(date_range) == 2: start_date, end_date = date_range
        elif isinstance(date_range, tuple) and len(date_range) == 1: start_date = end_date = date_range[0]
        else: start_date = end_date = today
    else:
        start_date, end_date = def_start, def_end

    cached_current_df, all_df = load_data_by_range_cached(ws, start_date, end_date)
    current_df = cached_current_df.copy()

    if not current_df.empty:
        for col in ["選取", "同步"]:
            if col in current_df.columns: current_df = current_df.drop(columns=[col])
        current_df.insert(0, "選取", False) 
        current_df["同步"] = False          
        try:
            def get_next_work_day(sd):
                nd = sd + timedelta(days=1)
                while nd.weekday() >= 5 or str(nd) in TW_HOLIDAYS: nd += timedelta(days=1)
                return nd
            target_next_day = get_next_work_day(today)
            date_col = pd.to_datetime(current_df["日期"]).dt.date
            current_df.loc[(date_col == today) | (date_col == target_next_day), "選取"] = True
        except: pass

    # ==========================================
    #  狀態 A: 主畫面 (工作清單 & LINE 日報標籤化)
    # ==========================================
    if st.session_state.dr_mode == "main":
        tab_list, tab_line = st.tabs(["📋 工作清單", "📤 LINE 匯報"])
        
        with tab_list:
            col_title, col_add_btn = st.columns([2, 1])
            with col_title:
                st.write("📱 **快速概覽 (手機專用視角)**")
            with col_add_btn:
                if st.button("➕ 新增工作", type="primary", use_container_width=True):
                    st.session_state.dr_mode = "add"
                    st.rerun()

            if not current_df.empty:
                for idx, row in current_df.iterrows():
                    with st.container(border=True):
                        c1, c2 = st.columns([3, 1])
                        with c1:
                            st.markdown(f"#### {row['客戶名稱']}")
                            st.caption(f"📅 {row['日期']} | 🏷️ {row['客戶分類']}")
                        with c2:
                            # 🌟 【新增】卡片獨立修改按鈕
                            if st.button("✏️ 修改", key=f"edit_btn_card_{idx}", use_container_width=True):
                                st.session_state.dr_edit_data = row.to_dict()
                                st.session_state.dr_edit_idx = idx
                                st.session_state.dr_mode = "edit"
                                st.rerun()
                            # 保留原本的同步按鈕
                            if st.button("🔄 同步", key=f"sync_btn_card_{idx}", use_container_width=True):
                                st.session_state.dr_sync_data = row.to_dict()
                                st.session_state.dr_mode = "sync"
                                st.rerun()
                        
                        st.info(f"**計畫：** {row['工作內容'] if row['工作內容'] else '無'}")
                        if row['實際行程']:
                            st.success(f"**實際：** {row['實際行程']}")
            else:
                st.info("💡 今日尚無工作安排")

            with st.expander("🛠️ 展開完整編輯表格 (電腦操作 / 勾選 LINE 日報建議用此處)", expanded=False):
                edited_df = st.data_editor(
                    current_df, num_rows="dynamic", hide_index=True, use_container_width=True, height=400, 
                    column_config={
                        "選取": st.column_config.CheckboxColumn("LINE日報", width=80),
                        "同步": st.column_config.CheckboxColumn("同步", width=60),
                        "日期": st.column_config.DateColumn("日期", format="YYYY-MM-DD", width=100),
                        "客戶名稱": st.column_config.TextColumn("客戶名稱", width=150),
                        "客戶分類": st.column_config.SelectboxColumn("客戶分類", width=120, options=["(A) 直賣A級", "(B) 直賣B級", "(C) 直賣C級", "(D-A) 經銷A級", "(D-B) 經銷B級", "(D-C) 經銷C級", "(O) 其它"]),
                        "工作內容": st.column_config.TextColumn("工作內容", width="large"),
                        "實際行程": st.column_config.TextColumn("實際行程", width="large"),
                        "最後更新時間": st.column_config.TextColumn("更新時間", disabled=True, width=100)
                    }, key="data_editor_main"
                )

                if st.button("💾 儲存修改表格", type="secondary", use_container_width=True):
                    with st.spinner("儲存變更中..."):
                        df_to_save = edited_df.drop(columns=["選取", "同步"], errors='ignore')
                        for col in ["客戶名稱", "工作內容", "實際行程"]:
                            if col in df_to_save.columns: df_to_save[col] = df_to_save[col].apply(lambda x: sanitize_input(x))
                        success, msg = save_to_google_sheet(ws, all_df, df_to_save, start_date, end_date)
                        if success:
                            st.success("✅ 修改已儲存!")
                            time.sleep(0.5)
                            st.rerun()
                        else: st.error(f"儲存失敗: {msg}")

                if "同步" in edited_df.columns:
                    sync_rows = edited_df[edited_df["同步"] == True]
                    if not sync_rows.empty:
                        target_row = sync_rows.iloc[0]
                        with st.spinner("🔄 正在跳轉至 CRM 表單..."):
                            df_to_save_auto = edited_df.drop(columns=["選取", "同步"], errors='ignore')
                            for col in ["客戶名稱", "工作內容", "實際行程"]:
                                if col in df_to_save_auto.columns: df_to_save_auto[col] = df_to_save_auto[col].apply(lambda x: sanitize_input(x))
                            success, msg = save_to_google_sheet(ws, all_df, df_to_save_auto, start_date, end_date)
                            if success:
                                st.session_state.dr_sync_data = target_row.to_dict()
                                st.session_state.dr_mode = "sync"
                                time.sleep(0.5)
                                st.rerun()

        with tab_line:
            st.info("💡 請在「工作清單」內的「展開完整表格」中勾選欲匯出的項目。")
            final_msg = ""
            # 若剛剛還沒展開 expander，edited_df 可能不存在，需做防呆
            try:
                if "選取" in edited_df.columns:
                    selected_rows = edited_df[edited_df["選取"] == True].copy()
                    if not selected_rows.empty:
                        selected_rows = selected_rows.sort_values(by="日期")
                        msg_lines = [f"【{real_name} 業務匯報】"]
                        for d in selected_rows["日期"].unique():
                            day_rows = selected_rows[selected_rows["日期"] == d]
                            header_suffix = " (今日實際)" if d == today else (" (明日預計)" if d > today else "")
                            msg_lines.extend([f"\n{d}{header_suffix}", "--------------"])
                            for _, row in day_rows.iterrows():
                                c_name, job, result, cat = str(row.get("客戶名稱", "")).strip(), str(row.get("工作內容", "")).strip(), str(row.get("實際行程", "")).strip(), str(row.get("客戶分類", "")).strip()
                                if not c_name and not job and not result: continue
                                msg_lines.append(f"客戶：{c_name} ，分類：{cat}")
                                if job: msg_lines.append(f"計畫：{job}")
                                if result: msg_lines.append(f"實際：{result}")
                                msg_lines.append("--------------")
                        final_msg = "\n".join(msg_lines)
            except: pass

            if final_msg:
                render_copy_button(final_msg)
                st.text_area("預覽內容", value=final_msg, height=400)

    # ==========================================
    #  狀態 B: 新增工作模式
    # ==========================================
    elif st.session_state.dr_mode == "add":
        st.subheader("➕ 新增工作")
        with st.form("add_work_form", border=True):
            c1, c2 = st.columns(2)
            with c1: inp_date = st.date_input("日期", today)
            with c2: inp_type = st.selectbox("客戶分類", ["請選擇", "(A) 直賣A級", "(B) 直賣B級", "(C) 直賣C級", "(D-A) 經銷A級", "(D-B) 經銷B級", "(D-C) 經銷C級", "(O) 其它"])
            inp_client = st.text_input("客戶名稱", placeholder="客戶名稱")
            inp_content = st.text_area("工作內容", placeholder="輸入預計行程", height=100)
            inp_result = st.text_area("實際行程", placeholder="輸入當日實際行程", height=100)

            c_sub, c_cancel = st.columns(2)
            with c_sub: submitted = st.form_submit_button("💾 儲存並返回", type="primary", use_container_width=True)
            with c_cancel: canceled = st.form_submit_button("❌ 取消返回", use_container_width=True)

        if canceled:
            st.session_state.dr_mode = "main"
            st.rerun()
        if submitted:
            inp_client, inp_content, inp_result = sanitize_input(inp_client), sanitize_input(inp_content), sanitize_input(inp_result)
            if not inp_client and inp_type != "(O) 其它": st.warning("⚠️ 請輸入客戶名稱")
            else:
                new_row = pd.DataFrame([{"日期": inp_date, "客戶名稱": inp_client if inp_client else "-", "客戶分類": inp_type if inp_type != "請選擇" else "", "工作內容": inp_content, "實際行程": inp_result, "最後更新時間": get_tw_time()}])
                df_base = current_df.drop(columns=["選取", "同步"], errors='ignore') if current_df is not None else pd.DataFrame()
                df_to_save = pd.concat([df_base, new_row], ignore_index=True)
                with st.spinner("儲存中..."):
                    success, msg = save_to_google_sheet(ws, all_df, df_to_save, start_date, end_date)
                    if success:
                        st.session_state.dr_mode = "main"
                        st.rerun()
                    else: st.error(f"儲存失敗: {msg}")
    
    # ==========================================
    #  狀態 C: 同步模式 (三步驟精靈)
    # ==========================================
    elif st.session_state.dr_mode == "sync":
        row_data = st.session_state.dr_sync_data
        if not row_data:
            st.session_state.dr_mode = "main"
            st.rerun()

        if "sync_step" not in st.session_state: st.session_state.sync_step = 1
        if "crm_draft" not in st.session_state: st.session_state.crm_draft = {}

        st.markdown(f"### 🔗 同步至 CRM (步驟 {st.session_state.sync_step}/3)")
        st.progress(st.session_state.sync_step / 3.0)

        if st.session_state.sync_step == 1:
            st.caption("1️⃣ 基本資訊")
            with st.form("step1_form", border=True):
                st.text_input("填寫人", value=real_name, disabled=True)
                f_date = st.text_input("拜訪日期", value=str(row_data.get("日期", "")), disabled=True)
                f_client = st.text_input("客戶名稱", value=str(row_data.get("客戶名稱", "")), disabled=True)
                
                f_type = st.selectbox("客戶性質", options=["A客戶 - 大手客戶 & 既有客戶", "B客戶 - 前一年新成交", "C客戶 - 預計開發及今年新成交", "D-A客戶 - 經銷商 大手客戶 & 既有客戶", "D-B客戶 - 經銷商 前一年新成交", "D-C客戶 - 經銷商 預計開發及今年新成交"])
                f_owner = st.selectbox("客戶所屬", options=CRM_OPT_OWNER, index=CRM_OPT_OWNER.index(real_name) if real_name in CRM_OPT_OWNER else 0)
                f_channel = st.selectbox("通路商", options=CRM_OPT_CHANNEL)
                f_action = st.selectbox("行動方案", options=CRM_OPT_ACTION)
                f_content = st.text_area("拜訪目的/案件", value=str(row_data.get("工作內容", "")))
                f_status_desc = st.text_area("案件狀況說明", value=str(row_data.get("實際行程", "")))

                c_back, c_next = st.columns(2)
                with c_back:
                    if st.form_submit_button("❌ 取消"):
                        st.session_state.dr_mode = "main"; st.session_state.sync_step = 1; st.rerun()
                with c_next:
                    if st.form_submit_button("下一步 ➔", type="primary"):
                        st.session_state.crm_draft.update({
                            "填寫人": real_name, "拜訪日期": f_date, "客戶名稱": f_client, "客戶性質": f_type, 
                            "客戶所屬": f_owner, "通路商": f_channel, "行動方案": f_action, "工作內容": f_content, "實際行程": f_status_desc
                        })
                        st.session_state.sync_step = 2; st.rerun()

        elif st.session_state.sync_step == 2:
            st.caption("2️⃣ 案件細節與選填")
            with st.form("step2_form", border=True):
                f_industry = st.selectbox("產業別", options=CRM_OPT_INDUSTRY)
                f_products = st.multiselect("推廣產品", options=CRM_OPT_PRODUCTS)
                f_est_date = st.selectbox("案件預計產出日期", options=CRM_OPT_EST_DATE)
                f_amount = st.number_input("案件總金額 (萬)", min_value=0.0, step=0.1)

                st.markdown("---")
                st.caption("選填項目")
                f_comp_channel = st.selectbox("競爭通路", options=CRM_OPT_COMP_CHANNEL)
                f_comp_brand = st.selectbox("競爭品牌", options=CRM_OPT_COMP_BRAND)
                
                curr_client = str(row_data.get("客戶名稱", ""))
                def_lost_idx = 0
                for idx, opt in enumerate(CRM_OPT_LOST_RECOVERY):
                    if real_name in opt and curr_client in opt: def_lost_idx = idx; break
                f_lost_rec = st.selectbox("流失客戶取回", options=CRM_OPT_LOST_RECOVERY, index=def_lost_idx)
                f_dependency = st.text_input("依賴事項")

                c_back, c_next = st.columns(2)
                with c_back:
                    if st.form_submit_button("⬅️ 上一步"):
                        st.session_state.sync_step = 1; st.rerun()
                with c_next:
                    if st.form_submit_button("下一步 ➔", type="primary"):
                        st.session_state.crm_draft.update({
                            "產業別": f_industry, "推廣產品_list": f_products, "產出日期": f_est_date, "總金額": str(f_amount),
                            "競爭通路": f_comp_channel, "競爭品牌_list": f_comp_brand, "流失取回": f_lost_rec, "依賴事項": f_dependency
                        })
                        st.session_state.sync_step = 3; st.rerun()

        elif st.session_state.sync_step == 3:
            st.caption("3️⃣ 確認送出")
            with st.container(border=True):
                draft = st.session_state.crm_draft
                st.success("✅ 資料已備妥")
                st.write(f"**客戶:** {draft.get('客戶名稱')} | **金額:** {draft.get('總金額')} 萬")
                st.write(f"**產品:** {', '.join(draft.get('推廣產品_list', []))}")
            
            c_back, c_submit = st.columns(2)
            with c_back:
                if st.button("⬅️ 返回修改", use_container_width=True):
                    st.session_state.sync_step = 2; st.rerun()
            with c_submit:
                if st.button("🚀 確認上傳", type="primary", use_container_width=True):
                    with st.spinner("正在上傳..."):
                        success, msg = save_to_crm_sheet(client, draft)
                        if success:
                            st.success("✅ 上傳成功！")
                            st.session_state.dr_mode = "main" 
                            st.session_state.dr_sync_data = None
                            st.session_state.sync_step = 1 
                            time.sleep(1)
                            st.rerun()
                        else: st.error(msg)

            # --- [After: 修改後程式碼 (新增修改模式區塊)] ---
            # ==========================================
            #  狀態 D: 修改工作模式 (手機版獨立卡片修改)
            # ==========================================
            elif st.session_state.dr_mode == "edit":
                row_data = st.session_state.get("dr_edit_data")
                edit_idx = st.session_state.get("dr_edit_idx")
                
                if not row_data:
                    st.session_state.dr_mode = "main"
                    st.rerun()
        
                st.subheader("✏️ 修改工作內容")
                
                with st.form("edit_work_form", border=True):
                    c1, c2 = st.columns([1, 1])
                    with c1:
                        # 處理原本的日期帶入
                        try:
                            default_date = pd.to_datetime(row_data["日期"]).date()
                        except:
                            default_date = today
                        inp_date = st.date_input("日期", default_date)
                        
                    with c2:
                        cat_options = ["請選擇", "(A) 直賣A級", "(B) 直賣B級", "(C) 直賣C級", "(D-A) 經銷A級", "(D-B) 經銷B級", "(D-C) 經銷C級", "(O) 其它"]
                        current_cat = str(row_data.get("客戶分類", "請選擇"))
                        if current_cat not in cat_options: current_cat = "請選擇"
                        inp_type = st.selectbox("客戶分類", cat_options, index=cat_options.index(current_cat))
                    
                    # 【修正點】將字數限制直接改為明確的數字 5000，解決 NameError 導致表單破裂的問題
                    inp_client = st.text_input("客戶名稱", value=str(row_data.get("客戶名稱", "")), max_chars=5000)
                    inp_content = st.text_area("工作內容", value=str(row_data.get("工作內容", "")), height=100, max_chars=5000)
                    inp_result = st.text_area("實際行程", value=str(row_data.get("實際行程", "")), height=100, max_chars=5000)
        
                    c_sub, c_cancel = st.columns([1, 1])
                    with c_sub:
                        submitted = st.form_submit_button("💾 儲存修改", type="primary", use_container_width=True)
                    with c_cancel:
                        canceled = st.form_submit_button("❌ 取消返回", type="secondary", use_container_width=True)
        
                if canceled:
                    st.session_state.dr_mode = "main"
                    st.rerun()
        
                if submitted:
                    inp_client = sanitize_input(inp_client)
                    inp_content = sanitize_input(inp_content)
                    inp_result = sanitize_input(inp_result)
                    
                    if not inp_client and inp_type != "(O) 其它":
                        st.warning("⚠️ 請輸入客戶名稱")
                    else:
                        final_client_name = inp_client if inp_client else "-"
                        
                        # 從目前的 df 中抽取出基礎資料 (移除 UI 專用欄位)
                        if current_df is not None:
                            df_base = current_df.drop(columns=["選取", "同步"], errors='ignore')
                        else:
                            df_base = pd.DataFrame()
        
                        # 精準更新指定的該筆資料
                        if edit_idx in df_base.index:
                            df_base.loc[edit_idx, "日期"] = inp_date
                            df_base.loc[edit_idx, "客戶名稱"] = final_client_name
                            df_base.loc[edit_idx, "客戶分類"] = inp_type if inp_type != "請選擇" else ""
                            df_base.loc[edit_idx, "工作內容"] = inp_content
                            df_base.loc[edit_idx, "實際行程"] = inp_result
                            df_base.loc[edit_idx, "最後更新時間"] = get_tw_time()
                        
                        with st.spinner("正在儲存變更..."):
                            success, msg = save_to_google_sheet(ws, all_df, df_base, start_date, end_date)
                            if success:
                                st.success("✅ 修改已儲存！")
                                st.session_state.dr_mode = "main" # 切回首頁
                                time.sleep(0.5)
                                st.rerun()
                            else:
                                st.error(f"儲存失敗: {msg}")
