import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import date
from utils import db

# ==========================================
#  資料處理
# ==========================================
def clean_currency(val):
    if not val: return 0.0
    if isinstance(val, (int, float)): return float(val)
    val_str = str(val).replace(",", "").strip()
    try: return float(val_str)
    except: return 0.0

@st.cache_data(ttl=600, show_spinner="下載 CRM 資料中...")
def load_crm_data():
    """讀取 CRM_DB -> 表單回應 1"""
    sh, msg = db.get_db_connection("crm")
    if not sh: return pd.DataFrame()
    
    try:
        ws = sh.worksheet("表單回應 1")
    except:
        ws = sh.sheet1
        
    data = ws.get_all_records()
    if not data: return pd.DataFrame()
    
    df = pd.DataFrame(data)
    
    # 轉換數值與日期
    if "拜訪日期" in df.columns:
        df["拜訪日期_dt"] = pd.to_datetime(df["拜訪日期"], errors='coerce').dt.date
    else:
        df["拜訪日期_dt"] = None
        
    if "總金額" in df.columns:
        df["總金額_數值"] = df["總金額"].apply(clean_currency)
    
    df = df.fillna("")
    return df

# ==========================================
#  主顯示函式
# ==========================================
def show(user_info):
    st.title("📈 CRM 商機總覽")
    
    user_role = user_info.get("Role", "sales")
    user_name = user_info.get("Name", "")
    is_manager = user_role in ["admin", "manager"]

    # 1. 讀取資料
    df_origin = load_crm_data()
    if df_origin.empty:
        st.info("尚無 CRM 資料")
        if st.button("🔄 重試"):
            st.cache_data.clear()
            st.rerun()
        return

    # 2. 篩選器
    with st.container(border=True):
        c1, c2 = st.columns([1, 2])
        with c1:
            today = date.today()
            start = today.replace(day=1) # 本月1號
            dr = st.date_input("📅 拜訪日期區間", (start, today))
        
        with c2:
            # 人員篩選
            all_users = sorted(list(set(df_origin["填寫人"].astype(str))))
            if is_manager:
                sel_users = st.multiselect("👥 選擇業務員 (填寫人)", options=all_users, default=all_users)
            else:
                st.text_input("👤 查看對象", value=user_name, disabled=True)
                sel_users = [user_name]

    # 3. 進階篩選 (含：【恢復】客戶名稱多選)
    with st.expander("🔍 進階條件 (客戶、產業、關鍵字)", expanded=True):
        # 依據目前的資料來源取得所有客戶名稱
        all_clients = sorted(list(set([x for x in df_origin["客戶名稱"].unique() if x])))
        
        # 第一列：客戶名稱篩選 (加回)
        sel_client_name = st.multiselect("🏢 客戶名稱 (支援多選)", options=all_clients, placeholder="選擇特定客戶...")

        rc1, rc2, rc3 = st.columns(3)
        with rc1:
            # 產業別
            if "產業別" in df_origin.columns:
                opts_ind = sorted(list(set([x for x in df_origin["產業別"] if x])))
                sel_ind = st.multiselect("產業別", opts_ind)
            else: sel_ind = []
        with rc2:
            # 產品
            kw_prod = st.text_input("產品關鍵字", placeholder="例: 變頻器")
        with rc3:
            # 模糊搜尋
            kw_fuzzy = st.text_input("模糊搜尋", placeholder="內容/備註/行程...")

    # 4. 資料過濾邏輯
    # 日期
    if isinstance(dr, tuple) and len(dr) == 2:
        mask = (df_origin["拜訪日期_dt"] >= dr[0]) & (df_origin["拜訪日期_dt"] <= dr[1])
        df = df_origin[mask].copy()
    else:
        df = df_origin.copy()

    # 人員
    if "填寫人" in df.columns:
        df = df[df["填寫人"].isin(sel_users)]

    # 【新增】客戶名稱過濾
    if sel_client_name:
        df = df[df["客戶名稱"].isin(sel_client_name)]

    # 產業
    if sel_ind:
        df = df[df["產業別"].isin(sel_ind)]
    
    # 產品關鍵字
    if kw_prod and "推廣產品" in df.columns:
        df = df[df["推廣產品"].astype(str).str.contains(kw_prod, case=False)]
    
    # 模糊搜尋 (針對所有欄位)
    if kw_fuzzy:
        mask_fuzzy = df.astype(str).apply(lambda x: x.str.contains(kw_fuzzy, case=False)).any(axis=1)
        df = df[mask_fuzzy]

    # 5. 統計看板
    if df.empty:
        st.warning("此條件下無資料")
        return

    st.markdown("---")
    k1, k2, k3, k4 = st.columns(4)
    total_amt = df["總金額_數值"].sum() if "總金額_數值" in df.columns else 0
    k1.metric("💰 預估商機 (萬)", f"{total_amt:,.1f}")
    k2.metric("📝 案件數", len(df))
    k3.metric("🏢 客戶數", df["客戶名稱"].nunique() if "客戶名稱" in df.columns else 0)
    
    avg = total_amt / len(df) if len(df) > 0 else 0
    k4.metric("📈 平均案單價", f"{avg:,.1f}")

    # 6. 圖表
    g1, g2 = st.columns(2)
    with g1:
        if "產業別" in df.columns:
            cnt = df["產業別"].value_counts().reset_index()
            cnt.columns = ["產業別", "數量"]
            fig = px.pie(cnt, values="數量", names="產業別", title="產業分佈", hole=0.4)
            st.plotly_chart(fig, use_container_width=True)
            
    with g2:
        if "推廣產品" in df.columns:
            # 拆解複選產品 (逗號分隔)
            s_prod = df["推廣產品"].astype(str).str.split(r'[、,]\s*').explode()
            s_prod = s_prod[s_prod != ""]
            cnt_p = s_prod.value_counts().head(10).reset_index()
            cnt_p.columns = ["產品", "次數"]
            fig2 = px.bar(cnt_p, x="次數", y="產品", orientation='h', title="熱門推廣產品 (Top 10)")
            fig2.update_layout(yaxis={'categoryorder':'total ascending'})
            st.plotly_chart(fig2, use_container_width=True)

    # 7. 列表與下載
    st.subheader("📝 詳細清單")
    st.dataframe(
        df, 
        use_container_width=True, 
        hide_index=True,
        column_config={
            "總金額_數值": st.column_config.NumberColumn("金額", format="%.1f")
        }
    )
    
    csv = df.to_csv(index=False).encode('utf-8-sig')
    st.download_button("📥 下載 CRM 報表", csv, f"CRM報表_{dr[0]}_{dr[1]}.csv", "text/csv")