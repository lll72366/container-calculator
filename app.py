import streamlit as st
import pandas as pd
import numpy as np
import io
import re
import hashlib

# ==========================登录==========================
ADMIN_USER = "admin"
ADMIN_PWD_HASH = hashlib.md5(b"admin123").hexdigest()

st.set_page_config(page_title="集装箱配箱计算器", layout="wide")

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

def login():
    st.title("🔐 登录")
    u = st.text_input("账号")
    p = st.text_input("密码", type="password")
    if st.button("登录"):
        if u == ADMIN_USER and hashlib.md5(p.encode()).hexdigest() == ADMIN_PWD_HASH:
            st.session_state.logged_in = True
            st.rerun()

if not st.session_state.logged_in:
    login()
    st.stop()

# ==========================智能识别==========================
def extract_from_text(s):
    s = str(s).lower()
    nums = re.findall(r'\d+\.?\d*', s)
    name = re.sub(r'[0-9.×长宽厚高毛重净重kgcm:mm]', '', s).strip()
    if not name: name = "未知货物"
    l = w = h = gw = 0.0
    m = re.search(r'(\d+\.?\d*)[^0-9]+(\d+\.?\d*)[^0-9]+(\d+\.?\d*)', s)
    if m:
        l,w,h = float(m[1]), float(m[2]), float(m[3])
    return name, l*10, w*10, h*10, gw

def smart_read(df):
    rows = []
    for _, r in df.iterrows():
        for v in r:
            if v is None: continue
            n,l,w,h,g = extract_from_text(v)
            if l>0 and w>0 and h>0:
                rows.append({"货物名称":n,"长(mm)":l,"宽(mm)":w,"高(mm)":h,"毛重(kg)":g})
                break
    return pd.DataFrame(rows)

# ==========================主界面==========================
st.title("📦 集装箱智能配箱系统（微信可用）")
file = st.file_uploader("上传Excel", type=["xlsx"])

if file:
    df = pd.read_excel(file, engine="openpyxl")
    cargo = smart_read(df)
    st.subheader("✅ 自动识别结果")
    st.dataframe(cargo, use_container_width=True)

    if st.button("开始配箱"):
        cargo["体积"] = cargo["长(mm)"]*cargo["宽(mm)"]*cargo["高(mm)"]/1e9
        st.subheader("📦 配箱完成")
        st.dataframe(cargo, use_container_width=True)

        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as f:
            cargo.to_excel(f, index=False)
        st.download_button("下载结果", buffer, "配箱结果.xlsx")
