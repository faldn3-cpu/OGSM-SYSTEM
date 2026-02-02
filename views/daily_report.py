import streamlit as st
from datetime import date, datetime, timedelta
import pandas as pd
import time
import logging
import streamlit.components.v1 as components
from utils import db, config_loader  # 引入 Phase 1 的工具

# ==========================================
#  常數與選項 (改由 System_Config 讀取)
# ==========================================
# 假日需從 config 讀取
TW_HOLIDAYS = config_loader.get_holidays()

# ==========================================
#  工具函式
# ==========================================
def get_next_work_day(start_date):
    """計算下一個工作日"""
    next_d = start_date + timedelta(days=1)
    # 若 Config 尚未載入假日，避免報錯
    holidays = config_loader.get_holidays() or []
    while next_d.weekday() >= 5 or next_d.strftime("%Y-%m-%d") in holidays:
         next_d += timedelta(days=1)
    return next_d

def get_or_create_user_sheet(sh, real_name):
    """
    在 Report_DB 中取得或建立使用者專屬工作表
    """
    HEADERS = ["項次", "日期", "星期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]
    try:
        ws = sh.worksheet(real_name)
        return ws
    except:
        try:
            ws = sh.add_worksheet(title=real_name, rows=1000, cols=10)
            ws.append_row(HEADERS)
            return ws
        except Exception as e:
            logging.error(f"Failed to create worksheet {real_name}: {e}")
            return None

def save_to_report_db(ws, current_df, start_date, end_date):
    """
    儲存至 Report_DB
    邏輯: 讀取全表 -> 移除當前區間舊資料 -> 合併新資料 -> 寫回
    """
    try:
        # 1. 讀取現有資料 (不含 Header)
        all_records = ws.get_all_values()
        header = all_records[0] if all_records else ["項次", "日期", "星期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]
        old_data = all_records[1:] if len(all_records) > 1 else []
        
        all_df = pd.DataFrame(old_data, columns=header)
        
        # 2. 資料清洗與合併
        if not all_df.empty:
            all_df["日期"] = pd.to_datetime(all_df["日期"], errors='coerce').dt.date
            # 保留區間外的資料
            mask_keep = (all_df["日期"] < start_date) | (all_df["日期"] > end_date)
            remaining_df = all_df.loc[mask_keep].copy()
        else:
            remaining_df = pd.DataFrame(columns=header)

        # 整理 current_df
        current_df["日期"] = pd.to_datetime(current_df["日期"], errors='coerce').dt.date
        current_df = current_df.dropna(subset=["日期"])
        current_df["星期"] = current_df["日期"].apply(lambda x: {0:"(一)", 1:"(二)", 2:"(三)", 3:"(四)", 4:"(五)", 5:"(六)", 6:"(日)"}.get(x.weekday(), ""))
        current_df["最後更新時間"] = db.get_tw_time().strftime("%Y-%m-%d %H:%M:%S")

        # 合併
        final_df = pd.concat([remaining_df, current_df], ignore_index=True)
        final_df = final_df.sort_values(by=["日期"], ascending=True)
        
        # 重編項次
        if "項次" in final_df.columns: final_df = final_df.drop(columns=["項次"])
        final_df.insert(0, "項次", range(1, len(final_df) + 1))
        
        # 填補空值
        final_df = final_df.fillna("")
        final_df["日期"] = final_df["日期"].astype(str)

        # 寫回 Google Sheet
        val_list = [final_df.columns.values.tolist()] + final_df.values.tolist()
        ws.clear()
        ws.update(values=val_list, range_name='A1')
        return True, "儲存成功"

    except Exception as e:
        return False, str(e)

def save_to_crm_db(data_dict):
    """儲存至 CRM_DB -> 表單回應 1"""
    sh, msg = db.get_db_connection("crm")
    if not sh: return False, msg
    
    try:
        ws = sh.worksheet("表單回應 1")
    except:
        ws = sh.sheet1

    try:
        # V6 Spec: 格式化時間
        now_dt = db.get_tw_time()
        # 格式: 2026/1/26 下午 4:15:05
        ampm = "上午" if now_dt.hour < 12 else "下午"
        h = now_dt.hour if now_dt.hour <= 12 else now_dt.hour - 12
        if h == 0: h = 12
        ts_str = f"{now_dt.year}/{now_dt.month}/{now_dt.day} {ampm} {h}:{now_dt.minute:02d}:{now_dt.second:02d}"
        
        date_val = pd.to_datetime(data_dict.get("拜訪日期")).date()
        date_str = f"{date_val.year}/{date_val.month}/{date_val.day}"

        row = [
            ts_str,
            data_dict.get("填寫人", ""),
            data_dict.get("客戶名稱", ""),
            data_dict.get("通路商", ""),
            data_dict.get("競爭通路", ""),
            data_dict.get("行動方案", ""),
            data_dict.get("客戶性質", ""),
            data_dict.get("流失取回", ""),
            data_dict.get("產業別", ""),
            date_str,
            data_dict.get("推廣產品", ""),
            data_dict.get("工作內容", ""),
            data_dict.get("產出日期", ""),
            data_dict.get("總金額", ""),
            data_dict.get("依賴事項", ""),
            data_dict.get("實際行程", ""),
            data_dict.get("競爭品牌", ""),
            data_dict.get("客戶所屬", "")
        ]
        ws.append_row(row)
        return True, "上傳成功"
    except Exception as e:
        return False, str(e)

# ==========================================
#  JS 複製按鈕 (保留原功能)
# ==========================================
def render_copy_button(text):
    safe_text = text.replace("`", "\`").replace("\\", "\\\\").replace("$", "\\$").replace("\n", "\\n")
    html = f"""
    <script>
    function copyToClipboard() {{
        const text = `{safe_text}`;
        navigator.clipboard.writeText(text).then(
            () => {{ document.getElementById("status").innerText = "✅ 複製成功！"; }},
            () => {{ 
                const ta = document.createElement("textarea");
                ta.value = text;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand("copy");
                document.body.removeChild(ta);
                document.getElementById("status").innerText = "✅ 複製成功！"; 
            }}
        );
        setTimeout(() => {{ document.getElementById("status").innerText = ""; }}, 3000);
    }}
    </script>
    <div style="margin: 5px 0;">
        <button onclick="copyToClipboard()" style="
            background-color:#00C851; color:white; border:none; padding:10px 20px; 
            border-radius:8px; cursor:pointer; width:100%; box-shadow:0 2px 5px rgba(0,0,0,0.2);">
            📋 點擊複製 LINE 日報文字
        </button>
        <div id="status" style="color:green; font-size:14px; margin-top:5px; height:20px;"></div>
    </div>
    """
    components.html(html, height=100)

# ==========================================
#  主顯示函式
# ==========================================
def show(user_info):
    real_name = user_info.get("Name", "User")
    role = user_info.get("Role", "sales")
    
    # 唯讀模式檢查 (Admin 模擬檢視時不可修改)
    is_readonly = (role == "admin")

    st.title(f"📝 {real_name} 的業務日報")
    if is_readonly:
        st.warning("🔒 目前為唯讀模式 (Admin View)，無法儲存變更。")

    # DB 連線
    sh, msg = db.get_db_connection("report")
    if not sh:
        st.error(f"資料庫連線失敗: {msg}")
        return

    # 狀態管理
    if "dr_mode" not in st.session_state: st.session_state.dr_mode = "main"
    if "dr_sync_data" not in st.session_state: st.session_state.dr_sync_data = None

    # 初始化工作表
    ws = get_or_create_user_sheet(sh, real_name)
    if not ws: return

    # 日期區間
    today = date.today()
    if st.session_state.dr_mode == "main":
        with st.expander("📅 切換日期區間", expanded=False):
            dr = st.date_input("選擇區間", (today - timedelta(days=today.weekday()), today + timedelta(days=6)))
            if isinstance(dr, tuple) and len(dr) == 2: s_date, e_date = dr
            else: s_date, e_date = today, today
    else:
        s_date, e_date = today, today # 副模式下不重要

    # 讀取資料
    try:
        data = ws.get_all_records()
        df = pd.DataFrame(data)
    except:
        df = pd.DataFrame()

    # 欄位處理
    ui_cols = ["日期", "客戶名稱", "客戶分類", "工作內容", "實際行程", "最後更新時間"]
    if df.empty:
        df = pd.DataFrame(columns=ui_cols)
    else:
        if "項次" in df.columns: df = df.drop(columns=["項次"])
    
    # 過濾日期
    df["日期"] = pd.to_datetime(df["日期"], errors='coerce').dt.date
    current_df = df[(df["日期"] >= s_date) & (df["日期"] <= e_date)].copy()
    current_df = current_df.sort_values("日期").reset_index(drop=True)

    # 插入 UI 控制欄位
    current_df.insert(0, "選取", False)
    current_df["同步"] = False
    
    # 自動勾選今天與下一個工作日
    next_day = get_next_work_day(today)
    mask_auto = (current_df["日期"] == today) | (current_df["日期"] == next_day)
    current_df.loc[mask_auto, "選取"] = True

    # ==========================
    # Mode: Main
    # ==========================
    if st.session_state.dr_mode == "main":
        col_t, col_btn = st.columns([3, 1])
        with col_t: st.subheader("📋 工作清單")
        with col_btn:
            if not is_readonly and st.button("➕ 新增工作", type="primary", use_container_width=True):
                st.session_state.dr_mode = "add"
                st.rerun()

        edited_df = st.data_editor(
            current_df,
            num_rows="dynamic",
            hide_index=True,
            use_container_width=True,
            column_config={
                "選取": st.column_config.CheckboxColumn("LINE", width="small"),
                "同步": st.column_config.CheckboxColumn("CRM", width="small"),
                "日期": st.column_config.DateColumn("日期", format="YYYY-MM-DD"),
                "客戶分類": st.column_config.SelectboxColumn("客戶分類", options=["(A) 直賣A級", "(B) 直賣B級", "(C) 直賣C級", "(D-A) 經銷A級", "(D-B) 經銷B級", "(D-C) 經銷C級", "(O) 其它"]),
                "最後更新時間": st.column_config.TextColumn("更新時間", disabled=True)
            },
            disabled=is_readonly
        )

        # 儲存與同步偵測
        if not is_readonly:
            if st.button("💾 儲存修改", use_container_width=True):
                to_save = edited_df.drop(columns=["選取", "同步"])
                success, msg = save_to_report_db(ws, to_save, s_date, e_date)
                if success: st.success("已儲存"); time.sleep(0.5); st.rerun()
                else: st.error(msg)
            
            # 偵測同步
            sync_rows = edited_df[edited_df["同步"] == True]
            if not sync_rows.empty:
                st.session_state.dr_sync_data = sync_rows.iloc[0].to_dict()
                st.session_state.dr_mode = "sync"
                st.rerun()

        # LINE 文字產生
        st.markdown("---")
        c1, c2 = st.columns([2, 1])
        with c1: st.subheader("📤 LINE 日報文字")
        
        sel_rows = edited_df[edited_df["選取"] == True]
        if not sel_rows.empty:
            msg_lines = [f"【{real_name} 業務匯報】"]
            for d in sorted(sel_rows["日期"].unique()):
                suffix = " (今日)" if d == today else " (明日預計)" if d > today else ""
                msg_lines.append(f"\n📅 {d}{suffix}\n--------------")
                day_data = sel_rows[sel_rows["日期"] == d]
                for _, r in day_data.iterrows():
                    msg_lines.append(f"🏢 {r['客戶名稱']} {r['客戶分類']}")
                    if r['工作內容']: msg_lines.append(f"📋 {r['工作內容']}")
                    if r['實際行程']: msg_lines.append(f"✅ {r['實際行程']}")
                    msg_lines.append("---")
            final_msg = "\n".join(msg_lines)
            st.text_area("預覽", final_msg, height=300)
            with c2: render_copy_button(final_msg)

    # ==========================
    # Mode: Add
    # ==========================
    elif st.session_state.dr_mode == "add":
        st.subheader("➕ 新增工作")
        with st.form("add_form"):
            d_in = st.date_input("日期", today)
            t_in = st.selectbox("分類", ["(A) 直賣A級", "(B) 直賣B級", "(O) 其它", "(D-A) 經銷A級"]) # 簡化顯示
            c_in = st.text_input("客戶名稱")
            j_in = st.text_area("工作內容")
            r_in = st.text_area("實際行程")
            
            if st.form_submit_button("確認新增", type="primary"):
                new_row = pd.DataFrame([{
                    "日期": d_in, "客戶名稱": c_in, "客戶分類": t_in, 
                    "工作內容": j_in, "實際行程": r_in, "最後更新時間": ""
                }])
                # 重新讀取 current 並合併
                save_to_report_db(ws, pd.concat([current_df.drop(columns=["選取", "同步"]), new_row]), s_date, e_date)
                st.session_state.dr_mode = "main"
                st.rerun()
            
            if st.form_submit_button("取消"):
                st.session_state.dr_mode = "main"
                st.rerun()

    # ==========================
    # Mode: Sync
    # ==========================
    elif st.session_state.dr_mode == "sync":
        row = st.session_state.dr_sync_data
        st.subheader(f"🔗 同步至 CRM: {row['客戶名稱']}")
        
        with st.form("crm_sync"):
            # 唯讀欄位
            c1, c2 = st.columns(2)
            c1.text_input("客戶名稱", row['客戶名稱'], disabled=True)
            c2.text_input("日期", str(row['日期']), disabled=True)
            st.text_area("工作內容", row['工作內容'])
            st.text_area("實際行程", row['實際行程'])
            
            st.markdown("---")
            # 動態選單 (Config Loader)
            col_a, col_b = st.columns(2)
            with col_a:
                f_owner = st.selectbox("客戶所屬", config_loader.get_crm_options("CRM_Owner") or ["本人"]) # Fallback
                f_channel = st.selectbox("通路商", config_loader.get_crm_options("CRM_Channel"))
                f_action = st.selectbox("行動方案", config_loader.get_crm_options("CRM_Action"))
                f_amount = st.number_input("預估金額 (萬)", step=0.1)

            with col_b:
                f_industry = st.selectbox("產業別", config_loader.get_crm_options("CRM_Industry"))
                f_products = st.multiselect("推廣產品", config_loader.get_crm_options("CRM_Product"))
                f_est = st.selectbox("產出日期", config_loader.get_crm_options("CRM_Est_Date"))
            
            # 選填項
            with st.expander("更多選項 (競爭、流失取回)"):
                 f_comp_ch = st.selectbox("競爭通路", config_loader.get_crm_options("CRM_Competitor_Channel"))
                 f_comp_br = st.selectbox("競爭品牌", config_loader.get_crm_options("CRM_Competitor_Brand"))
                 f_lost = st.selectbox("流失取回", config_loader.get_crm_options("CRM_Lost_Recovery"))
                 f_dep = st.text_input("依賴事項")

            if st.form_submit_button("🚀 確認上傳"):
                crm_data = {
                    "填寫人": real_name, "客戶名稱": row['客戶名稱'], "拜訪日期": row['日期'],
                    "工作內容": row['工作內容'], "實際行程": row['實際行程'],
                    "通路商": f_channel, "行動方案": f_action, "總金額": f_amount,
                    "產業別": f_industry, "推廣產品": ",".join(f_products), "產出日期": f_est,
                    "競爭通路": f_comp_ch, "競爭品牌": f_comp_br, "流失取回": f_lost,
                    "依賴事項": f_dep, "客戶性質": row['客戶分類'], "客戶所屬": f_owner
                }
                ok, res = save_to_crm_db(crm_data)
                if ok:
                    st.success("同步成功")
                    st.session_state.dr_mode = "main"
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(res)

            if st.form_submit_button("取消"):
                st.session_state.dr_mode = "main"
                st.rerun()