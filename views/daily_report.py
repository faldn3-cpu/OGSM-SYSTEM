import streamlit as st
import streamlit.components.v1 as components
from datetime import date, datetime, timezone, timedelta
import pandas as pd
import gspread 
import time
import json

# === 設定 LIFF ID ===
# 請確保此 ID 在 LINE Developers Console 已開啟 "Share Target Picker" 權限
LIFF_ID = "2008945289-UvXWe3BK"

# === 設定您的 App 網址 (用於登入後跳轉回來) ===
APP_URL = "https://seec-sales-system.streamlit.app"

def get_tw_time():
    tw_tz = timezone(timedelta(hours=8))
    return datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")

def get_default_range(today):
    weekday_idx = today.weekday()
    start = today - timedelta(days=weekday_idx)
    end = today
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
        st.error(f"找不到 Google Sheet：{db_name}")
        return None

    HEADERS = ["項次", "日期", "星期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]

    try:
        ws = sh.worksheet(real_name)
        return ws
    except gspread.WorksheetNotFound:
        try:
            ws = sh.add_worksheet(title=real_name, rows=1000, cols=10)
            ws.append_row(HEADERS)
            return ws
        except Exception:
            return None

def load_data_by_range(ws, start_date, end_date):
    try:
        data = ws.get_all_records()
        ui_columns = ["日期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]
        
        if not data:
            return pd.DataFrame(columns=ui_columns), pd.DataFrame()
        
        df = pd.DataFrame(data)
        if "項次" in df.columns: df = df.drop(columns=["項次"])

        df = df.fillna("")
        text_cols = ["客戶名稱", "工作內容", "實際行程", "客戶分類", "最後更新時間"]
        for col in text_cols:
            if col in df.columns: df[col] = df[col].astype(str)

        df["日期"] = pd.to_datetime(df["日期"], errors='coerce').dt.date
        
        mask = (df["日期"] >= start_date) & (df["日期"] <= end_date)
        filtered_df = df.loc[mask].copy()
        filtered_df = filtered_df.sort_values(by=["日期"], ascending=True).reset_index(drop=True)
        
        display_df = pd.DataFrame(columns=ui_columns)
        for col in ui_columns:
            if col in filtered_df.columns:
                display_df[col] = filtered_df[col]
        
        return display_df, df 
    except Exception:
        return pd.DataFrame(columns=["日期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]), pd.DataFrame()

def save_data_by_range(ws, all_df, edited_df, view_start_date, view_end_date):
    try:
        edited_df["日期"] = pd.to_datetime(edited_df["日期"], errors='coerce').dt.date
        edited_df = edited_df.dropna(subset=["日期"])
        edited_df["星期"] = edited_df["日期"].apply(lambda x: get_weekday_str(x))
        
        now_str = get_tw_time()
        
        mask_new = (edited_df["最後更新時間"] == "") | (edited_df["最後更新時間"].isna()) | (edited_df["最後更新時間"] == "系統自動填入")
        edited_df.loc[mask_new, "最後更新時間"] = now_str
        
        if not all_df.empty and "日期" in all_df.columns:
            all_df["日期"] = pd.to_datetime(all_df["日期"], errors='coerce').dt.date
            mask_keep = (all_df["日期"] < view_start_date) | (all_df["日期"] > view_end_date)
            remaining_df = all_df.loc[mask_keep].copy()
        else:
            remaining_df = pd.DataFrame()

        if "項次" in remaining_df.columns: remaining_df = remaining_df.drop(columns=["項次"])
        
        final_df = pd.concat([remaining_df, edited_df], ignore_index=True)
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
        
        return True, edited_df
    except Exception as e:
        return False, str(e)

def show(client, db_name, user_email, real_name):
    st.title(f"📝 {real_name} 的業務日報")
    
    ws = get_or_create_user_sheet(client, db_name, real_name)
    if not ws: return

    today = date.today()
    def_start, def_end = get_default_range(today)
    
    col1, col2 = st.columns([2, 1])
    with col1:
        date_range = st.date_input("📅 資料區間", (def_start, def_end), key="date_range_picker")
    
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    elif isinstance(date_range, tuple) and len(date_range) == 1:
        start_date = end_date = date_range[0]
    else:
        start_date = end_date = today

    cache_key = f"report_data_{start_date}_{end_date}"
    all_data_key = "report_all_data_cache"
    
    if "current_cache_key" not in st.session_state:
        st.session_state.current_cache_key = ""
    
    if "review_mode" not in st.session_state:
        st.session_state.review_mode = False

    if st.session_state.current_cache_key != cache_key:
        st.session_state.review_mode = False
        current_df, all_df = load_data_by_range(ws, start_date, end_date)
        
        has_today = False
        if not current_df.empty:
            if today in current_df["日期"].values: has_today = True
        
        if not has_today and (start_date <= today <= end_date):
            new_row = pd.DataFrame([{
                "日期": today,
                "客戶名稱": "請填入4個字", 
                "客戶分類": "請選擇客戶ABC",
                "工作內容": "今日預計行程", 
                "實際行程": "今日實際行程", 
                "最後更新時間": "系統自動填入"
            }])
            current_df = pd.concat([current_df, new_row], ignore_index=True)
            current_df = current_df.reset_index(drop=True)
            
        st.session_state[cache_key] = current_df
        st.session_state[all_data_key] = all_df
        st.session_state.current_cache_key = cache_key
    
    df_to_edit = st.session_state[cache_key]
    all_df_cached = st.session_state[all_data_key]

    st.caption("""
    💡 **操作教學**：
    1. **新增/修改**：直接在下方表格編輯。
    2. **刪除**：勾選左側方塊後按鍵盤 `Delete`。
    3. **鎖定**：編輯完成後，請點擊 `🔒 鎖定並預覽` (這會強制儲存您輸入的內容)。
    4. **上傳**：確認無誤後，點擊出現的 `💾 確認上傳` 按鈕。
    """)
    
    edited_df = st.data_editor(
        df_to_edit,
        num_rows="dynamic",
        hide_index=True,
        column_config={
            "日期": st.column_config.DateColumn("日期", format="YYYY-MM-DD", required=True, default=today, width="small"),
            "客戶名稱": st.column_config.TextColumn("客戶名稱", required=True, width="medium", default="請填入4個字"),
            "客戶分類": st.column_config.SelectboxColumn("客戶分類", width="medium", required=True,
                options=["請選擇客戶ABC", "(A) 直賣A級", "(B) 直賣B級", "(C) 直賣C級", "(D-A) 經銷A級", "(D-B) 經銷B級", "(D-C) 經銷C級", "(O) 其它"],
                default="請選擇客戶ABC"),
            "工作內容": st.column_config.TextColumn("工作內容(今日)", width="large", default="今日預計行程"),
            "實際行程": st.column_config.TextColumn("實際行程", width="large", default="今日實際行程"),
            "最後更新時間": st.column_config.TextColumn("更新時間", disabled=True, width="small", default="系統自動填入")
        },
        key="editor",
        disabled=st.session_state.review_mode 
    )

    st.write("") 

    if not st.session_state.review_mode:
        if st.button("🔒 鎖定並預覽 (編輯完請按我)", type="secondary", use_container_width=True):
            st.session_state[cache_key] = edited_df
            st.session_state.review_mode = True
            st.rerun()
    else:
        st.info("👀 請確認上方資料是否正確？(如需修改，請點擊「取消鎖定」)")
        
        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("🔙 取消鎖定 (繼續編輯)", use_container_width=True):
                st.session_state.review_mode = False
                st.rerun()
        with c2:
            if st.button("💾 確認上傳 Google Sheet", type="primary", use_container_width=True):
                with st.spinner("正在上傳資料..."):
                    success, msg = save_data_by_range(ws, all_df_cached, edited_df, start_date, end_date)
                    if success:
                        st.success("✅ 上傳成功！")
                        st.session_state.review_mode = False
                        st.session_state[cache_key] = edited_df
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(f"上傳失敗：{msg}")

    st.markdown("---")
    st.subheader("📤 發送日報到 LINE (LIFF 增強版)")
    
    today_date = date.today()
    today_data = edited_df[edited_df["日期"] == today_date]
    
    valid_rows = []
    for idx, row in today_data.iterrows():
        c_name = str(row.get("客戶名稱", "")).strip()
        job = str(row.get("工作內容", "")).strip()
        result = str(row.get("實際行程", "")).strip()
        
        invalid_names = ["", "請填入4個字"]
        invalid_jobs = ["", "今日預計行程"]
        invalid_results = ["", "今日實際行程"]
        
        has_real_name = c_name not in invalid_names
        has_real_job = job not in invalid_jobs
        has_real_result = result not in invalid_results
        
        if has_real_name or has_real_job or has_real_result:
            valid_rows.append(row)
    
    if not valid_rows:
        st.warning("⚠️ 今天還沒有填寫任何有效資料，無法發送日報。")
    else:
        # === 準備訊息內容 ===
        msg_lines = [f"【{real_name} 日報】📅 {today_date}"]
        msg_lines.append("--------------")
        for row in valid_rows:
            client_name = str(row.get("客戶名稱", ""))
            if client_name in ["", "請填入4個字"]: client_name = "（內部/其他事項）"
            
            cat = row.get("客戶分類", "")
            if cat == "請選擇客戶ABC": cat = "" 
            
            job = row.get("工作內容", "")
            if job == "今日預計行程": job = "" 
            
            result = row.get("實際行程", "")
            if result == "今日實際行程": result = ""

            msg_lines.append(f"🏢 {client_name} {cat}")
            if job: msg_lines.append(f"📝 {job}")
            if result: msg_lines.append(f"✅ {result}")
            msg_lines.append("---")
        
        msg_text = "\n".join(msg_lines)
        
        # === JS Escaping (防止文字中斷 JS 程式碼) ===
        safe_msg_json = json.dumps(msg_text) 

        # === 嵌入 LIFF JavaScript (增強版: 含 Redirect Logic) ===
        liff_script = f"""
        <html>
        <head>
            <script charset="utf-8" src="https://static.line-scdn.net/liff/edge/2/sdk.js"></script>
            <style>
                .liff-btn {{
                    background-color: #06c755;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 12px 24px;
                    font-size: 16px;
                    font-weight: bold;
                    width: 100%;
                    cursor: pointer;
                    transition: background-color 0.3s;
                    font-family: "Helvetica Neue", Arial, sans-serif;
                }}
                .liff-btn:hover {{
                    background-color: #05b34c;
                }}
                .status {{
                    margin-top: 8px;
                    font-size: 12px;
                    color: #666;
                    text-align: center;
                }}
            </style>
        </head>
        <body>
            <button id="sendBtn" class="liff-btn" onclick="sendLiffMessage()">🚀 開啟 LINE 選擇好友傳送</button>
            <div id="status" class="status">系統準備中...</div>

            <script>
                // 填入後端定義好的變數
                const LIFF_ID = "{LIFF_ID}";
                const APP_URL = "{APP_URL}"; 

                async function initializeLiff() {{
                    try {{
                        await liff.init({{ liffId: LIFF_ID }});
                        
                        // 檢查是否已登入
                        if (!liff.isLoggedIn()) {{
                            document.getElementById("status").innerText = "尚未登入，點擊按鈕將進行登入...";
                        }} else {{
                            document.getElementById("status").innerText = "✅ LINE 已連線，可發送";
                        }}
                    }} catch (err) {{
                        document.getElementById("status").innerText = "初始化錯誤 (請檢查 ID/網址): " + err;
                    }}
                }}

                async function sendLiffMessage() {{
                    try {{
                        // === 關鍵修正：若未登入，強制跳轉回 App 網址 ===
                        if (!liff.isInClient() && !liff.isLoggedIn()) {{
                            liff.login({{ redirectUri: APP_URL }});
                            return;
                        }}

                        const message = {safe_msg_json}; 

                        if (liff.isApiAvailable('shareTargetPicker')) {{
                            const res = await liff.shareTargetPicker([
                                {{
                                    type: "text",
                                    text: message
                                }}
                            ]);
                            if (res) {{
                                document.getElementById("status").innerText = "✅ 發送成功！";
                            }} else {{
                                document.getElementById("status").innerText = "❌ 取消發送";
                            }}
                        }} else {{
                            document.getElementById("status").innerText = "⚠️ 此裝置不支援直接選人，請登入手機版 LINE 使用。";
                            alert("請使用手機版 LINE 操作，或手動複製下方文字。");
                        }}
                    }} catch (error) {{
                        document.getElementById("status").innerText = "❌ 執行錯誤: " + error;
                    }}
                }}

                initializeLiff();
            </script>
        </body>
        </html>
        """
        
        col_btn, col_copy = st.columns([1, 1])
        
        with col_btn:
            st.info("👇 使用 LIFF 強力傳送 (支援電腦/手機)")
            components.html(liff_script, height=120)
            
        with col_copy:
            st.warning("👇 備用：若 LIFF 無法開啟，請手動複製")
            st.code(msg_text, language="text")