import streamlit as st
import pandas as pd
import numpy as np
import io
import re
import hashlib

# ========================== 配置 ==========================
ADMIN_USER = "admin"
ADMIN_PWD_HASH = hashlib.md5(b"admin123").hexdigest()

st.set_page_config(
    page_title="智能配箱",
    layout="wide",
    initial_sidebar_state="collapsed"
)

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "cargo" not in st.session_state:
    st.session_state.cargo = None

# ========================== 登录 ==========================
def login():
    st.title("🔐 智能配箱系统")
    u = st.text_input("账号")
    p = st.text_input("密码", type="password")
    if st.button("登录", use_container_width=True):
        if u == ADMIN_USER and hashlib.md5(p.encode()).hexdigest() == ADMIN_PWD_HASH:
            st.session_state.logged_in = True
            st.rerun()

# ========================== 超级识别引擎 ==========================
def extract_all(text):
    s = str(text).lower()
    nums = re.findall(r'\d+\.?\d*', s)
    nums = [float(n) for n in nums]

    name = re.sub(r'[0-9.×*x:：长宽高厚毛重净重kgcm mmt吨公分毫米]', '', s).strip()
    name = name if name else "未知货物"

    l = w = h = gw = 0.0

    p_len = re.search(r'长[:： ]*(\d+\.?\d*)', s)
    p_wid = re.search(r'宽[:： ]*(\d+\.?\d*)', s)
    p_hei = re.search(r'高[:： ]*(\d+\.?\d*)', s)
    p_mul = re.search(r'(\d+\.?\d*)[×*x ]+(\d+\.?\d*)[×*x ]+(\d+\.?\d*)', s)
    p_gw  = re.search(r'毛重[:： ]*(\d+\.?\d*)', s)

    if p_len: l = float(p_len.group(1))
    if p_wid: w = float(p_wid.group(1))
    if p_hei: h = float(p_hei.group(1))
    if p_gw:  gw = float(p_gw.group(1))

    if l == 0 and w == 0 and h == 0 and p_mul:
        l = float(p_mul.group(1))
        w = float(p_mul.group(2))
        h = float(p_mul.group(3))

    if any(x in s for x in ['cm','公分','厘米']):
        l *=10; w *=10; h *=10
    elif any(x in s for x in ['m','米']):
        l *=1000; w *=1000; h *=1000

    return name, round(l,2), round(w,2), round(h,2), round(gw,2)

def smart_parse(df):
    rows = []
    for _, r in df.iterrows():
        txt = " ".join([str(c) for c in r if pd.notna(c)])
        name, l, w, h, gw = extract_all(txt)
        if l > 0 and w > 0 and h > 0:
            rows.append({
                "货物名称": name,
                "长(mm)": l,
                "宽(mm)": w,
                "高(mm)": h,
                "毛重(kg)": gw,
                "体积(m³)": round(l*w*h/1e9,4)
            })
    return pd.DataFrame(rows)

# ========================== 配箱 ==========================
def pack_cargo(df, typ="40HQ"):
    spec = {
        "20GP": (21000, 33.2),
        "40GP": (26500, 67.7),
        "40HQ": (26000, 76.2),
        "45HQ": (27500, 86.8)
    }[typ]
    mw, mv = spec
    df = df.copy().sort_values("体积(m³)", ascending=False)
    cw, cv, no = 0,0,1
    df["柜号"] = ""
    for i, r in df.iterrows():
        if cw + r["毛重(kg)"] > mw or cv + r["体积(m³)"] > mv:
            no +=1
            cw, cv =0,0
        df.at[i,"柜号"] = f"{typ}-{no:02d}"
        cw += r["毛重(kg)"]
        cv += r["体积(m³)"]
    return df

# ========================== 主界面（持续式，不跳转） ==========================
def main():
    st.markdown("## 📦 智能配箱｜一键识别·一键计算")

    # 上传区（一直保持在顶部）
    file = st.file_uploader("📁 上传Excel货物清单", type=["xlsx","xls"])

    if file:
        with st.spinner("正在识别..."):
            df = pd.read_excel(file, engine="openpyxl")
            cargo = smart_parse(df)
            st.session_state.cargo = cargo

        if not cargo.empty:
            st.success(f"✅ 识别完成：共 {len(cargo)} 件货物")
            st.dataframe(cargo, use_container_width=True, hide_index=True)
        else:
            st.warning("⚠️ 未识别到有效尺寸")

    # 配箱区（紧跟下方，持续展示）
    if st.session_state.cargo is not None and not st.session_state.cargo.empty:
        st.divider()
        st.markdown("### 🧮 配箱计算")
        ctype = st.selectbox("柜型", ["20GP","40GP","40HQ","45HQ"], index=2)
        if st.button("🚀 开始配箱", type="primary", use_container_width=True):
            res = pack_cargo(st.session_state.cargo, ctype)
            st.success("📦 配箱完成")
            st.dataframe(res, use_container_width=True, hide_index=True)

            bio = io.BytesIO()
            with pd.ExcelWriter(bio, engine="openpyxl") as f:
                res.to_excel(f, index=False)
            st.download_button("📥 下载结果", bio, "配箱结果.xlsx", use_container_width=True)

if not st.session_state.logged_in:
    login()
else:
    main()
