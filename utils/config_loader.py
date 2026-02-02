import streamlit as st
from .db import get_db_connection
import logging

@st.cache_data(ttl=600)
def get_system_config():
    """
    讀取 System_Config 表 (V6 Spec 3.B)
    回傳格式: dict { 'Category': [Value1, Value2...] }
    """
    sh, msg = get_db_connection("price")
    if not sh:
        return {}

    try:
        ws = sh.worksheet("System_Config")
        records = ws.get_all_records()
        
        config = {}
        for row in records:
            cat = row.get("Category")
            val = row.get("Value")
            if cat and val:
                if cat not in config:
                    config[cat] = []
                config[cat].append(val)
        return config
    except Exception as e:
        logging.error(f"Config load error: {e}")
        return {}

def get_crm_options(category_key):
    """
    取得特定 CRM 下拉選單選項
    """
    config = get_system_config()
    # 部分選項可能在 DB 中是以逗號分隔的字串存在，需解析
    raw_list = config.get(category_key, [])
    final_list = []
    for item in raw_list:
        if "," in item:
            final_list.extend([x.strip() for x in item.split(",")])
        else:
            final_list.append(item)
    
    # 去重並排序
    return sorted(list(set(final_list))) if final_list else []

def get_holidays():
    """
    取得假日列表
    """
    config = get_system_config()
    return config.get("Holiday", [])