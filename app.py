import streamlit as st
import pandas as pd
import numpy as np
import io
import re
from pdfminer.high_level import extract_text
from PIL import Image
import pytesseract

# ====================== 页面配置（全版本兼容） ======================
st.set_page_config(page_title="智能配箱系统", layout="wide")

# ====================== 登录 ======================
if "login" not in st.session_state:
    st.session_state.login = False

def login():
    st.title("🔐 智能配箱系统")
    account = st.text_input("账号")
    pwd = st.text_input("密码", type="password")
    if st.button("登录"):
        if account == "admin" and pwd == "admin123":
            st.session_state.login = True
            st.rerun()
        else:
            st.error("账号或密码错误")

if not st.session_state.login:
    login()
    st.stop()

# ====================== 工具函数 ======================
def clean_text(s):
    return str(s).strip().lower() if str(s).strip() not in ["nan", "None", ""] else ""

def is_header(text):
    ht = {"品名","货物名称","长","宽","高","毛重","净重","规格","尺寸","重量"}
    words = clean_text(text)
    count = sum([1 for k in ht if k in words])
    return count >= 2 and not re.search(r'\d{3,}', words)

def extract_size(text):
    l,w,h = 0.0, 0.0, 0.0
    t = clean_text(text)
    # 关键字优先
    r = re.search(r'长[^0-9]*([0-9.]+)', t)
    if r: l = float(r.group(1))
    r = re.search(r'宽[^0-9]*([0-9.]+)', t)
    if r: w = float(r.group(1))
    r = re.search(r'高[^0-9]*([0-9.]+)', t)
    if r: h = float(r.group(1))

    # 三联数字
    if l == 0 and w == 0 and h == 0:
        r = re.search(r'(\d+\.?\d)\D*(\d+\.?\d)\D*(\d+\.?\d)', t)
        if r:
            l,w,h = float(r.group(1)), float(r.group(2)), float(r.group(3))

    # 单位转 mm
    if any(x in t for x in ["cm","公分","厘米"]):
        l*=10;w*=10;h*=10
    elif any(x in t for x in ["m","米"]):
        l*=1000;w*=1000;h*=1000
    return round(l,2), round(w,2), round(h,2)

def extract_weight(text):
    t = clean_text(text)
    gw,nw = 0.0,0.0
    r = re.search(r'毛重[^0-9]*([0-9.]+)',t)
    if r: gw=float(r.group(1))
    r = re.search(r'净重[^0-9]*([0-9.]+)',t)
    if r: nw=float(r.group(1))
    if gw == 0:
        r = re.search(r'([0-9.]+)\s*kg',t)
        if r: gw=float(r.group(1))
    return round(gw,2), round(nw,2)

def extract_name(text):
    t = re.sub(r'[\d\s\.\*×:cmkg米公斤吨长宽高毛重净重()]','',clean_text(text))
    return t if t else "未知货物"

# ====================== 文件解析 ======================
def parse_excel(file):
    items = []
    xl = pd.ExcelFile(file)
    for sheet in xl.sheet_names:
        df = pd.read_excel(file, sheet_name=sheet, header=None)
        for _, row in df.iterrows():
            line = " ".join([str(x) for x in row if pd.notna(x)])
            if is_header(line) or not line:
                continue
            name = extract_name(line)
            l,w,h = extract_size(line)
            gw,nw = extract_weight(line)
            if l>0 and w>0 and h>0:
                items.append({
                    "Sheet": sheet,
                    "货物名称": name,
                    "长(mm)": l,
                    "宽(mm)": w,
                    "高(mm)": h,
                    "毛重(kg)": gw,
                    "净重(kg)": nw,
                    "体积": round(l*w*h/1e9,4)
                })
    return pd.DataFrame(items)

def parse_pdf(file):
    txt = extract_text(io.BytesIO(file.read()))
    items = []
    for line in txt.split("\n"):
        line = line.strip()
        if not line or is_header(line):
            continue
        name = extract_name(line)
        l,w,h = extract_size(line)
        gw,nw = extract_weight(line)
        if l>0 and w>0 and h>0:
            items.append({
                "货物名称": name,"长(mm)":l,"宽(mm)":w,"高(mm)":h,
                "毛重(kg)":gw,"净重(kg)":nw,"体积":round(l*w*h/1e9,4)
            })
    return pd.DataFrame(items)

def parse_image(file):
    img = Image.open(file)
    txt = pytesseract.image_to_string(img, lang="chi_sim+eng")
    items = []
    for line in txt.split("\n"):
        line = line.strip()
        if not line or is_header(line):
            continue
        name = extract_name(line)
        l,w,h = extract_size(line)
        gw,nw = extract_weight(line)
        if l>0 and w>0 and h>0:
            items.append({
                "货物名称": name,"长(mm)":l,"宽(mm)":w,"高(mm)":h,
                "毛重(kg)":gw,"净重(kg)":nw,"体积":round(l*w*h/1e9,4)
            })
    return pd.DataFrame(items)

# ====================== 配箱 ======================
def pack(df, typ="40HQ"):
    spec = {"20GP":(21000,33.2),"40GP":(26500,67.7),"40HQ":(26000,76.2),"45HQ":(27500,86.8)}[typ]
    wl, vl = spec
    df = df.copy().sort_values("体积", ascending=False)
    cw, cv, no = 0,0,1
    df["柜号"] = ""
    for i, r in df.iterrows():
        if cw + r["毛重(kg)"] > wl or cv + r["体积"] > vl:
            no +=1
            cw,cv =0,0
        df.loc[i,"柜号"] = f"{typ}-{no:02d}"
        cw += r["毛重(kg)"]
        cv += r["体积"]
    return df

# ====================== 主界面 ======================
st.title("📦 智能配箱｜Excel+PDF+图片 全兼容")

f = st.file_uploader("上传文件", type=["xlsx","xls","pdf","jpg","jpeg","png"])
df_result = pd.DataFrame()

if f:
    try:
        if f.name.endswith((".xlsx",".xls")):
            df_result = parse_excel(f)
        elif f.name.endswith(".pdf"):
            df_result = parse_pdf(f)
        elif f.name.endswith((".jpg",".jpeg",".png")):
            df_result = parse_image(f)
    except:
        st.error("文件解析失败，请检查格式")

if not df_result.empty:
    st.success(f"识别成功：{len(df_result)} 件")
    st.dataframe(df_result, use_container_width=True)

    ct = st.selectbox("柜型", ["20GP","40GP","40HQ","45HQ"])
    if st.button("开始配箱"):
        res = pack(df_result, ct)
        st.dataframe(res, use_container_width=True)
        bio = io.BytesIO()
        with pd.ExcelWriter(bio, engine="openpyxl") as writer:
            res.to_excel(writer, index=False)
        st.download_button("下载结果", bio, "配箱结果.xlsx")
