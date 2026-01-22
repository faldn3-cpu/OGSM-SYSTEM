import streamlit as st
from datetime import date, datetime, timezone, timedelta
import pandas as pd
import gspread 
import time

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
        if not data: return pd.DataFrame(columns=ui_columns), pd.DataFrame()
        
        df = pd.DataFrame(data)
        if "項次" in df.columns: df = df.drop(columns=["項次"])
        df = df.fillna("")
        for col in ["客戶名稱", "工作內容", "實際行程", "客戶分類", "最後更新時間"]:
            if col in df.columns: df[col] = df[col].astype(str)

        df["日期"] = pd.to_datetime(df["日期"], errors='coerce').dt.date
        mask = (df["日期"] >= start_date) & (df["日期"] <= end_date)
        filtered_df = df.loc[mask].copy().sort_values(by=["日期"], ascending=True).reset_index(drop=True)
        
        display_df = filtered_df[ui_columns].copy() if not filtered_df.empty else pd.DataFrame(columns=ui_columns)
        return display_df, df 
    except:
        return pd.DataFrame(columns=["日期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]), pd.DataFrame()

def save_to_google_sheet(ws, all_df, current_df, start_date, end_date):
    """將目前的 DataFrame 完整存回 Google Sheet"""
    try:
        # 1. 整理 current_df
        current_df["日期"] = pd.to_datetime(current_df["日期"], errors='coerce').dt.date
        current_df = current_df.dropna(subset=["日期"])
        current_df["星期"] = current_df["日期"].apply(lambda x: get_weekday_str(x))
        current_df["最後更新時間"] = get_tw_time() # 強制更新時間
        
        # 2. 整理 all_df (保留區間外的資料)
        if not all_df.empty and "日期" in all_df.columns:
            all_df["日期"] = pd.to_datetime(all_df["日期"], errors='coerce').dt.date
            mask_keep = (all_df["日期"] < start_date) | (all_df["日期"] > end_date)
            remaining_df = all_df.loc[mask_keep].copy()
        else:
            remaining_df = pd.DataFrame()

        # 3. 合併 (注意：這裡會自動忽略 current_df 中的額外欄位如 '選取')
        final_df = pd.concat([remaining_df, current_df], ignore_index=True)
        final_df = final_df.sort_values(by=["日期"], ascending=True)

        # 4. 重新編號項次
        if "項次" in final_df.columns: final_df = final_df.drop(columns=["項次"])
        final_df.insert(0, "項次", range(1, len(final_df) + 1))

        # 5. 確保欄位順序 (這裡會排除 '選取' 欄位，確保資料庫乾淨)
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
        return True, "儲存成功"
    except Exception as e:
        return False, str(e)

def show(client, db_name, user_email, real_name):
    st.title(f"📝 {real_name} 的業務日報")
    ws = get_or_create_user_sheet(client, db_name, real_name)
    if not ws: return

    today = date.today()
    def_start, def_end = get_default_range(today)
    
    # 手機版面優化：將日期選擇收合
    with st.expander("📅 切換資料日期區間", expanded=False):
        date_range = st.date_input("選擇區間", (def_start, def_end))
    
    if isinstance(date_range, tuple) and len(date_range) == 2: start_date, end_date = date_range
    elif isinstance(date_range, tuple) and len(date_range) == 1: start_date = end_date = date_range[0]
    else: start_date = end_date = today

    # 載入資料
    current_df, all_df = load_data_by_range(ws, start_date, end_date)

    # === [功能升級] 加入「選取」欄位用於勾選發送 ===
    if not current_df.empty:
        # 1. 插入「選取」欄位到第一欄
        current_df.insert(0, "選取", False)
        # 2. 智慧預設：自動勾選「今天」的項目
        # 如果日期欄位是字串，先轉成 date 物件比較
        try:
            date_col = pd.to_datetime(current_df["日期"]).dt.date
            current_df.loc[date_col == today, "選取"] = True
        except:
            pass # 如果轉換失敗就不預設

    # ==========================================
    #  Part 1: 賈伯斯模式 - 新增工作 (Mobile First)
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
        
        inp_client = st.text_input("客戶名稱", placeholder="輸入客戶名稱...")
        inp_content = st.text_area("工作內容", placeholder="輸入預計行程或今日重點...", height=100)
        inp_result = st.text_area("實際行程", placeholder="輸入實際執行結果...", height=100)

        if st.button("➕ 加入清單", type="primary", use_container_width=True):
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
                # 這裡不需加入 "選取" 欄位，因為 concat 後，pandas 會自動處理缺失欄位 (fillna)
                # 重新載入時會自動補上預設值
                
                # 合併到當前顯示的 DataFrame (先移除選取欄位以免干擾儲存)
                if "選取" in current_df.columns:
                    df_to_save = current_df.drop(columns=["選取"])
                else:
                    df_to_save = current_df

                df_to_save = pd.concat([df_to_save, new_row], ignore_index=True)
                
                with st.spinner("正在儲存..."):
                    success, msg = save_to_google_sheet(ws, all_df, df_to_save, start_date, end_date)
                    if success:
                        st.success("✅ 已新增並儲存！")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(f"儲存失敗: {msg}")

    # ==========================================
    #  Part 2: 檢視與編輯清單 (含勾選功能)
    # ==========================================
    st.write("")
    st.subheader(f"📋 工作清單 ({start_date} ~ {end_date})")
    
    # 使用者可以在這裡勾選要傳送的項目
    edited_df = st.data_editor(
        current_df,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        column_config={
            "選取": st.column_config.CheckboxColumn("LINE", width="small", help="勾選以產生 LINE 報表"),
            "日期": st.column_config.DateColumn("日期", format="YYYY-MM-DD", width="small"),
            "客戶名稱": st.column_config.TextColumn("客戶", width="medium"),
            "客戶分類": st.column_config.SelectboxColumn("分類", width="small", 
                options=["(A) 直賣A級", "(B) 直賣B級", "(C) 直賣C級", "(D-A) 經銷A級", "(D-B) 經銷B級", "(D-C) 經銷C級", "(O) 其它"]),
            "工作內容": st.column_config.TextColumn("計畫", width="large"),
            "實際行程": st.column_config.TextColumn("實績", width="large"),
            "最後更新時間": st.column_config.TextColumn("更新時間", disabled=True, width="small")
        },
        key="data_editor_grid"
    )

    if st.button("💾 儲存修改 (表格編輯後請按我)", type="secondary", use_container_width=True):
         with st.spinner("儲存變更中..."):
            # 儲存前先移除「選取」欄位，因為資料庫不需要存這個
            df_to_save = edited_df.drop(columns=["選取"]) if "選取" in edited_df.columns else edited_df
            
            success, msg = save_to_google_sheet(ws, all_df, df_to_save, start_date, end_date)
            if success:
                st.success("✅ 修改已儲存！")
                time.sleep(1)
                st.rerun()
            else:
                st.error(f"儲存失敗: {msg}")

    st.markdown("---")
    
    # ==========================================
    #  Part 3: 產生 LINE 文字 (勾選版)
    # ==========================================
    st.subheader("📤 產生 LINE 日報文字")

    # [關鍵邏輯] 只抓取「被勾選 (True)」的資料
    if "選取" in edited_df.columns:
        selected_rows = edited_df[edited_df["選取"] == True].copy()
    else:
        selected_rows = pd.DataFrame()
    
    if selected_rows.empty:
        st.info("💡 請在上方表格勾選要傳送的項目 (預設已勾選今天)。")
    else:
        # 按日期排序，讓報表整齊
        selected_rows = selected_rows.sort_values(by="日期")
        
        # 產生報表頭
        msg_lines = [f"【{real_name} 業務匯報】"]
        
        # 依照日期分組產生內容
        unique_dates = selected_rows["日期"].unique()
        
        for d in unique_dates:
            d_str = str(d) # 轉字串 YYYY-MM-DD
            # 取得該日期的所有工作
            day_rows = selected_rows[selected_rows["日期"] == d]
            
            msg_lines.append(f"\n📅 {d_str}")
            msg_lines.append("--------------")
            
            for idx, row in day_rows.iterrows():
                c_name = str(row.get("客戶名稱", "")).strip()
                job = str(row.get("工作內容", "")).strip()
                result = str(row.get("實際行程", "")).strip()
                cat = str(row.get("客戶分類", "")).strip()
                
                if not c_name and not job and not result: continue

                msg_lines.append(f"🏢 {c_name} {cat}")
                if job: msg_lines.append(f"📝 {job}")
                if result: msg_lines.append(f"✅ {result}")
                msg_lines.append("---")
            
        final_msg = "\n".join(msg_lines)
        
        # 使用 st.code 顯示，右上角會有一個「複製」按鈕
        st.code(final_msg, language="text")
        st.caption("👆 點擊右上角的「複製圖示」，即可貼到 LINE 群組。")