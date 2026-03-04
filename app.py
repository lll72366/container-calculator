import streamlit as st
import pandas as pd
import numpy as np
import io
import re
import hashlib

# ========================== 基础配置 ==========================
ADMIN_USER = "admin"
ADMIN_PWD_HASH = hashlib.md5(b"admin123").hexdigest()

# 适配旧版Streamlit，移除新版参数
st.set_page_config(
    page_title="智能配箱系统",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ========================== 会话状态 ==========================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "all_cargo" not in st.session_state:
    st.session_state.all_cargo = pd.DataFrame()
if "sheet_names" not in st.session_state:
    st.session_state.sheet_names = []

# ========================== 登录模块 ==========================
def login():
    st.title("🔐 集装箱智能配箱系统")
    col1, col2, col3 = st.columns([0.2, 0.6, 0.2])
    with col2:
        username = st.text_input("账号", placeholder="默认：admin")
        password = st.text_input("密码", type="password", placeholder="默认：admin123")
        if st.button("登录", type="primary"):
            if username == ADMIN_USER and hashlib.md5(password.encode()).hexdigest() == ADMIN_PWD_HASH:
                st.session_state.logged_in = True
                st.rerun()
            else:
                st.error("❌ 账号或密码错误")

# ========================== 核心：多Sheet+多标题识别引擎 ==========================
def is_title_row(row_text):
    """判断是否为标题行（过滤无效标题/空行）"""
    title_keywords = ["货物名称", "长", "宽", "高", "毛重", "净重", "品名", "规格", "重量", "尺寸"]
    empty_keywords = ["nan", "", "无", "空"]
    
    # 标题行包含至少2个标题关键词，且不包含有效数字
    has_title = any(kw in row_text for kw in title_keywords)
    has_valid_num = bool(re.search(r'\d+\.?\d*', row_text))
    is_empty = all(kw in row_text.lower() for kw in empty_keywords)
    
    return has_title and not has_valid_num and not is_empty

def extract_cargo_info(text):
    """提取单单元格/单行的货物信息"""
    s = str(text).lower().strip()
    if s in ["", "nan", "无"]:
        return None
    
    # 提取数字
    nums = re.findall(r'\d+\.?\d*', s)
    if len(nums) < 3:  # 至少需要长宽高3个数字
        return None
    
    # 提取货物名称
    name_pattern = r'[0-9.×*x:：()（）cm mm kg g 吨 公斤 克 长 宽 高 厚 毛重 净重]'
    name = re.sub(name_pattern, '', s).strip()
    name = name if name else "未知货物"
    
    # 提取长宽高
    length = width = height = 0.0
    # 格式1：长50/长:50
    len_match = re.search(r'长[:：= ]*(\d+\.?\d*)', s)
    wid_match = re.search(r'宽[:：= ]*(\d+\.?\d*)', s)
    hei_match = re.search(r'高[:：= ]*(\d+\.?\d*)', s)
    # 格式2：50×40×30
    dim_match = re.search(r'(\d+\.?\d*)[×*x× ]+(\d+\.?\d*)[×*x× ]+(\d+\.?\d*)', s)
    
    if len_match: length = float(len_match.group(1))
    if wid_match: width = float(wid_match.group(1))
    if hei_match: height = float(hei_match.group(1))
    
    # 补充连续三个数字
    if length == 0 and width == 0 and height == 0 and dim_match:
        length = float(dim_match.group(1))
        width = float(dim_match.group(2))
        height = float(dim_match.group(3))
    
    # 单位转换（默认cm转mm）
    if "cm" in s or "公分" in s:
        length *= 10; width *= 10; height *= 10
    elif "m" in s:
        length *= 1000; width *= 1000; height *= 1000
    
    # 提取毛重
    gw = 0.0
    gw_match = re.search(r'毛重[:：= ]*(\d+\.?\d*)', s) or re.search(r'(\d+\.?\d*) *kg', s)
    if gw_match:
        gw = float(gw_match.group(1))
    
    # 过滤无效数据
    if length <= 0 or width <= 0 or height <= 0:
        return None
    
    return {
        "货物名称": name,
        "长(mm)": round(length, 2),
        "宽(mm)": round(width, 2),
        "高(mm)": round(height, 2),
        "毛重(kg)": round(gw, 2),
        "体积(m³)": round(length * width * height / 1e9, 4)
    }

def parse_multi_sheet_excel(uploaded_file):
    """解析多Sheet Excel，自动跳过标题行/空行"""
    all_cargo = []
    
    # 读取所有Sheet名称
    xl_file = pd.ExcelFile(uploaded_file)
    sheet_names = xl_file.sheet_names
    st.session_state.sheet_names = sheet_names
    
    # 遍历每个Sheet
    for sheet_name in sheet_names:
        # 读取Sheet所有数据（不跳过行）
        df = pd.read_excel(uploaded_file, sheet_name=sheet_name, engine="openpyxl", header=None)
        
        # 遍历每行，过滤标题行，提取有效数据
        for idx, row in df.iterrows():
            # 合并当前行所有单元格文本
            row_text = " ".join([str(cell) for cell in row if pd.notna(cell)])
            
            # 跳过标题行/空行
            if is_title_row(row_text) or row_text.strip() == "":
                continue
            
            # 提取货物信息
            cargo_info = extract_cargo_info(row_text)
            if cargo_info:
                cargo_info["来源Sheet"] = sheet_name  # 标记数据来源
                all_cargo.append(cargo_info)
    
    # 转换为DataFrame
    cargo_df = pd.DataFrame(all_cargo)
    return cargo_df, sheet_names

# ========================== 配箱计算 ==========================
def calculate_container(cargo_df, container_type="40HQ"):
    """配箱计算，自动分柜"""
    container_specs = {
        "20GP": {"max_weight": 21000, "max_volume": 33.2},
        "40GP": {"max_weight": 26500, "max_volume": 67.7},
        "40HQ": {"max_weight": 26000, "max_volume": 76.2},
        "45HQ": {"max_weight": 27500, "max_volume": 86.8}
    }
    spec = container_specs[container_type]
    
    df = cargo_df.copy().sort_values("体积(m³)", ascending=False)
    current_weight = 0.0
    current_volume = 0.0
    container_no = 1
    df["柜号"] = ""
    
    for idx, row in df.iterrows():
        if (current_weight + row["毛重(kg)"] > spec["max_weight"]) or (current_volume + row["体积(m³)"] > spec["max_volume"]):
            container_no += 1
            current_weight = 0.0
            current_volume = 0.0
        
        df.loc[idx, "柜号"] = f"{container_type}-{container_no:02d}"
        current_weight += row["毛重(kg)"]
        current_volume += row["体积(m³)"]
    
    return df

# ========================== 主界面（兼容所有Streamlit版本） ==========================
def main_interface():
    # 顶部标题
    st.markdown("## 📦 集装箱智能配箱系统（多Sheet兼容）")
    st.divider()
    
    # 1. 文件上传区（移除use_container_width，用列布局适配）
    col_upload = st.columns([1])[0]
    with col_upload:
        uploaded_file = st.file_uploader(
            "📁 上传Excel货物清单（支持多Sheet/多标题）",
            type=["xlsx", "xls"]
        )
    
    # 2. 数据识别区
    if uploaded_file:
        with st.spinner("🔍 正在解析Excel（多Sheet/多标题）..."):
            cargo_df, sheet_names = parse_multi_sheet_excel(uploaded_file)
            st.session_state.all_cargo = cargo_df
        
        if not cargo_df.empty:
            # 显示识别结果统计
            st.success(f"✅ 解析完成！共识别 {len(cargo_df)} 件货物（来源：{len(sheet_names)} 个Sheet）")
            
            # Sheet筛选（适配旧版，移除use_container_width）
            col_filter = st.columns([0.3, 0.7])[0]
            with col_filter:
                selected_sheet = st.selectbox(
                    "筛选Sheet",
                    options=["全部"] + sheet_names
                )
            
            # 筛选数据并展示
            if selected_sheet != "全部":
                show_df = cargo_df[cargo_df["来源Sheet"] == selected_sheet]
            else:
                show_df = cargo_df
            
            st.subheader("📋 识别结果")
            st.dataframe(show_df, use_container_width=True)  # 这个参数所有版本都支持
        else:
            st.warning("⚠️ 未识别到有效货物数据，请检查Excel内容")
    
    # 3. 配箱计算区（持续展示）
    if not st.session_state.all_cargo.empty:
        st.divider()
        st.subheader("🧮 配箱计算")
        
        # 柜型选择（适配旧版）
        col_container = st.columns([0.3, 0.7])[0]
        with col_container:
            container_type = st.selectbox(
                "选择集装箱类型",
                ["20GP", "40GP", "40HQ", "45HQ"],
                index=2
            )
            calculate_btn = st.button("🚀 开始配箱", type="primary")
        
        # 执行配箱并展示结果
        if calculate_btn:
            with st.spinner("📊 计算最优配箱方案..."):
                result_df = calculate_container(st.session_state.all_cargo, container_type)
                st.success("📦 配箱完成！")
                
                # 展示配箱结果
                st.subheader("📦 配箱结果")
                st.dataframe(result_df, use_container_width=True)
                
                # 导出结果（适配旧版）
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                    result_df.to_excel(writer, sheet_name=f"{container_type}配箱结果", index=False)
                st.download_button(
                    label="📥 下载配箱结果",
                    data=buffer,
                    file_name=f"集装箱配箱结果_{container_type}.xlsx"
                )

# ========================== 程序入口 ==========================
if not st.session_state.logged_in:
    login()
else:
    main_interface()

# ========================== 页脚 ==========================
st.divider()
st.caption("© 2025 智能配箱系统 - 支持多Sheet/多标题 | 货代专用")
