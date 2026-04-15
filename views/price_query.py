import streamlit as st
import pandas as pd
import gspread
import re
import logging
import os
import time
from datetime import datetime, timezone, timedelta
import html  

CACHE_FILE = "price_cache.parquet"
CACHE_TTL = 86400  

def get_tw_time():
    tw_tz = timezone(timedelta(hours=8))
    return datetime.now(tw_tz).strftime("%Y-%m-%d %H:%M:%S")

def write_search_log(client, db_name, user_email, query, result_count):
    try:
        if not client: return
        sh = client.open(db_name)
        try: ws = sh.worksheet("SearchLogs")
        except: 
            ws = sh.add_worksheet(title="SearchLogs", rows=1000, cols=4)
            ws.append_row(["時間", "使用者", "關鍵字", "結果數量"])
        ws.append_row([get_tw_time(), user_email, query, result_count])
    except Exception as e: pass

@st.cache_data(ttl=600, show_spinner=False)
def fetch_last_update_date(db_name, _client):
    try:
        if not _client: return "離線模式"
        sh = _client.open(db_name)
        try: ws = sh.worksheet("PriceData")
        except gspread.WorksheetNotFound: return "無法取得(分頁遺失)"
        val = ws.acell('G2').value
        return str(val) if val else "未知"
    except Exception as e: return "暫無法取得"

def clean_currency(val):
    if not val or pd.isna(val): return 0.0
    val_str = str(val)
    clean_str = re.sub(r'[^\d.]', '', val_str)
    try: return float(clean_str)
    except ValueError: return 0.0

@st.cache_data(ttl=300, show_spinner="正在讀取價格資料...")
def fetch_price_data(db_name, _client):
    cache_exists = os.path.exists(CACHE_FILE)
    cache_is_fresh = False
    if cache_exists:
        mtime = os.path.getmtime(CACHE_FILE)
        if (time.time() - mtime) < CACHE_TTL: cache_is_fresh = True

    if cache_exists and cache_is_fresh:
        try: return pd.read_parquet(CACHE_FILE), "" 
        except Exception as e: pass

    if _client:
        try:
            sh = _client.open(db_name)
            try: ws = sh.worksheet("經銷價(總)")
            except gspread.WorksheetNotFound: ws = sh.sheet1
            data = ws.get_all_records()
            if data:
                df = pd.DataFrame(data).dropna(how='all').astype(str) 
                try: df.to_parquet(CACHE_FILE, index=False)
                except Exception as save_err: pass
                return df, ""
        except Exception as e: pass

    if cache_exists:
        try:
            mtime = os.path.getmtime(CACHE_FILE)
            hours_old = (time.time() - mtime) / 3600
            return pd.read_parquet(CACHE_FILE), f"⚠️ 目前使用離線資料 (上次更新: {hours_old:.1f} 小時前)，請檢查網路連線。"
        except Exception as e: return pd.DataFrame(), f"❌ 無法讀取資料: {e}"
    return pd.DataFrame(), "❌ 無法連線至資料庫，且無本地存檔。"

MAX_SEARCH_LENGTH = 50

def sanitize_search_query(query):
    if not query: return ""
    query = str(query).strip()
    if len(query) > MAX_SEARCH_LENGTH: query = query[:MAX_SEARCH_LENGTH]
    return re.sub(r'[^\w\s\-\.\(\)\/]', '', query)

@st.dialog("🧮 業務報價試算")
def show_calculator_dialog(spec, desc, base_price):
    st.markdown(f"""
    <div style="background-color:#f8f9fa; padding:10px; border-radius:8px; margin-bottom:15px;">
        <div style="font-weight:bold; font-size:1.1em; color:#333;">{spec}</div>
        <div style="font-size:0.9em; color:#666;">{desc}</div>
        <hr style="margin:8px 0;">
        <div style="display:flex; justify-content:space-between; align-items:center;">
            <span>經銷價：</span><span style="color:#d9534f; font-weight:bold; font-size:1.1em;">${base_price:,.0f}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if 'calc_discount' not in st.session_state: st.session_state.calc_discount = 100.00
    if 'calc_price' not in st.session_state: st.session_state.calc_price = int(base_price)
    if 'current_base_price' not in st.session_state: st.session_state.current_base_price = base_price

    if st.session_state.current_base_price != base_price:
        st.session_state.current_base_price = base_price
        st.session_state.calc_discount = 100.00
        st.session_state.calc_price = int(base_price)

    def on_discount_change():
        if st.session_state.current_base_price > 0:
            st.session_state.calc_price = int(round(st.session_state.current_base_price * (st.session_state.calc_discount / 100)))

    def on_price_change():
        if st.session_state.current_base_price > 0:
            st.session_state.calc_discount = round((st.session_state.calc_price / st.session_state.current_base_price) * 100, 2)
    
    col1, col2 = st.columns(2)
    with col1: st.number_input("販售折數 (%)", min_value=0.0, max_value=300.0, step=0.5, format="%.2f", key="calc_discount", on_change=on_discount_change)
    with col2: st.number_input("販售價格 ($)", min_value=0, step=100, format="%d", key="calc_price", on_change=on_price_change)
    
    st.markdown(f"""
    <div style="margin-top: 15px; padding: 15px; background: linear-gradient(135deg, #0071e3 0%, #00c6ff 100%); color: white; border-radius: 12px; text-align: center;">
        <div style="font-size:0.9em; opacity:0.9;">最終報價金額</div>
        <div style="font-size:2em; font-weight:bold;">${st.session_state.calc_price:,.0f}</div>
    </div>
    """, unsafe_allow_html=True)

def show(client, db_name, user_email, real_name, is_manager, is_admin=False):
    if is_admin:
        if st.button("🔄 強制更新最新牌價", type="primary", use_container_width=True):
            with st.spinner("正在清除快取並重新下載資料..."):
                if os.path.exists(CACHE_FILE):
                    try: os.remove(CACHE_FILE)
                    except Exception: pass
                fetch_price_data.clear(); fetch_last_update_date.clear()
                time.sleep(1)
            st.success("✅ 快取已清除，正在重新載入最新資料...")
            time.sleep(1)
            st.rerun()
    
    df, warning_msg = fetch_price_data(db_name, client)
    st.caption(f"資料更新日期：{fetch_last_update_date(db_name, client)}")
    if warning_msg: st.warning(warning_msg)
    
    st.markdown("""
    <style>
    .search-card { background-color: white; border: 1px solid #e0e0e0; border-radius: 12px; padding: 16px; margin-bottom: 12px; }
    .card-title { font-weight: bold; font-size: 1.1rem; color: #333; margin-bottom: 4px; }
    .card-desc { font-size: 0.9rem; color: #666; margin-bottom: 8px; line-height: 1.4; }
    .card-price { font-weight: bold; font-size: 1.2rem; color: #0071e3; }
    @media (prefers-color-scheme: dark) {
        .search-card { background-color: #262730; border-color: #444; }
        .card-title { color: #fff; } .card-desc { color: #bbb; } .card-price { color: #4da6ff; }
    }
    </style>
    """, unsafe_allow_html=True)

    if "saved_price_query" not in st.session_state: st.session_state.saved_price_query = ""
    def update_search_memory(): st.session_state.saved_price_query = st.session_state.price_search_box

    with st.container(border=True):
        col1, col2 = st.columns([4, 1])
        with col1:
            query = st.text_input("🔍 關鍵字搜尋", value=st.session_state.saved_price_query, placeholder="例: SDE, 55KW...", max_chars=MAX_SEARCH_LENGTH, key="price_search_box", label_visibility="collapsed", on_change=update_search_memory)
        with col2:
            search_btn = st.button("搜尋", use_container_width=True, type="primary")

    if search_btn: st.session_state.saved_price_query = query
    if search_btn or query:
        query = sanitize_search_query(query)
        if not query: st.warning("⚠️ 請輸入關鍵字"); return
        if df.empty: st.error("無法讀取價格表，請聯繫管理員。"); return

        try:
            mask = df.apply(lambda row: row.astype(str).str.contains(query, case=False, regex=False).any(), axis=1)
            result_df = df[mask]
        except Exception as e: st.error("搜尋發生錯誤"); return

        st.markdown(f"**搜尋結果：** `{query}` (共 {len(result_df)} 筆)")
        
        if result_df.empty: st.info("找不到符合的資料，請嘗試其他關鍵字。")
        else:
            if len(result_df) > 50:
                st.caption(f"⚠️ 資料過多，僅顯示前 50 筆")
                result_df = result_df.head(50)
            
            for idx, row in result_df.iterrows():
                name_parts = [str(row.get(c, "")).strip() for c in ["產品名稱", "規格", "Item", "品名", "Name"] if str(row.get(c, "")).strip()]
                product_name = " | ".join(name_parts) if name_parts else str(row.values[0])
                desc_parts = [str(row.get(c, "")).strip() for c in ["型號", "備註", "說明", "Model", "Description"] if str(row.get(c, "")).strip()]
                product_desc = " | ".join(desc_parts)

                product_name_esc, product_desc_esc = html.escape(product_name), html.escape(product_desc)

                price_col = None
                dist_price_cols = [c for c in df.columns if '經銷' in c and '價' in c] or [c for c in df.columns if '經銷' in c]
                if dist_price_cols: price_col = dist_price_cols[0]

                base_price = 0
                price_display = "請洽詢"
                
                if price_col and price_col in row:
                    raw_price = row[price_col]
                    base_price = clean_currency(raw_price)
                    price_display = f"${base_price:,.0f}" if base_price > 0 else str(raw_price)
                elif not price_col: price_display = "⚠️ 無經銷價"

                with st.container():
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        st.markdown(f"""<div class="card-title">{product_name_esc}</div><div class="card-desc">{product_desc_esc}</div><div class="card-price">{price_display}</div>""", unsafe_allow_html=True)
                    with c2:
                        st.write("")
                        if base_price > 0:
                            if st.button("試算", key=f"btn_{idx}", use_container_width=True):
                                write_search_log(client, db_name, user_email, product_name, "試算選取")
                                show_calculator_dialog(product_name_esc, product_desc_esc, base_price)
                        else: st.caption("無法試算")
                    st.divider()
    else:
        st.info("👈 請輸入產品型號或規格開始查詢")
