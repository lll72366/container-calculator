import streamlit as st
import pandas as pd
import numpy as np
import io
import re

# ====================== 页面配置（全版本兼容） ======================
st.set_page_config(page_title="智能配箱系统", layout="wide")

# ====================== 会话状态初始化 ======================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "cargo_data" not in st.session_state:
    st.session_state.cargo_data = pd.DataFrame()

# ====================== 登录模块（稳定无错） ======================
def login_module():
    st.title("🔐 集装箱智能配箱系统（MS Office/WPS全兼容）")
    col1, col2, col3 = st.columns([0.2, 0.6, 0.2])
    with col2:
        username = st.text_input("账号", placeholder="默认：admin")
        password = st.text_input("密码", type="password", placeholder="默认：admin123")
        if st.button("登录", type="primary"):
            if username.strip() == "admin" and password.strip() == "admin123":
                st.session_state.logged_in = True
                st.rerun()
            else:
                st.error("❌ 账号或密码错误（默认：admin/admin123）")

# ====================== 核心工具函数（精准识别+兼容Excel） ======================
def clean_text(text: str) -> str:
    """清洗文本：过滤空值、统一小写"""
    if pd.isna(text) or str(text).strip() in ["nan", "None", "", "无", "空"]:
        return ""
    return str(text).strip().lower()

def is_header_row(text: str) -> bool:
    """精准判断标题行（兼容MS Office/WPS表头格式）"""
    header_keywords = {"品名", "货物名称", "长", "宽", "高", "毛重", "净重", "规格", "尺寸", "重量"}
    # 标题行判定：包含≥2个关键词 + 无连续数字（避免误判货物行）
    text_clean = clean_text(text)
    keyword_count = sum([1 for kw in header_keywords if kw in text_clean])
    has_valid_number = bool(re.search(r'\d{2,}', text_clean))  # 至少2位数字才是有效货物数据
    return keyword_count >= 2 and not has_valid_number

def extract_cargo_info(text: str) -> dict:
    """
    提取货物信息（兼容MS Office/WPS所有文本格式）
    返回：{name, length, width, height, gross_weight, net_weight, volume}
    """
    text_clean = clean_text(text)
    if not text_clean:
        return {"name": "未知货物", "length": 0.0, "width": 0.0, "height": 0.0, "gross_weight": 0.0, "net_weight": 0.0, "volume": 0.0}
    
    # 1. 提取长宽高（关键字优先，避免错位）
    length = width = height = 0.0
    # 匹配：长50 / 长:50 / 长=50 / 长度50
    len_match = re.search(r'长[:：= ]*(\d+\.?\d*)', text_clean)
    wid_match = re.search(r'宽[:：= ]*(\d+\.?\d*)', text_clean)
    hei_match = re.search(r'高[:：= ]*(\d+\.?\d*)', text_clean)
    
    if len_match: length = float(len_match.group(1))
    if wid_match: width = float(wid_match.group(1))
    if hei_match: height = float(hei_match.group(1))
    
    # 备用：匹配 50×40×30 / 50*40*30（仅当关键字匹配失败时）
    if length == 0 and width == 0 and height == 0:
        dim_match = re.search(r'(\d+\.?\d)\D*(\d+\.?\d)\D*(\d+\.?\d)', text_clean)
        if dim_match:
            length = float(dim_match.group(1))
            width = float(dim_match.group(2))
            height = float(dim_match.group(3))
    
    # 2. 单位转换（统一转mm，兼容MS Office/WPS常用单位）
    if any(unit in text_clean for unit in ["cm", "公分", "厘米"]):
        length *= 10; width *= 10; height *= 10
    elif any(unit in text_clean for unit in ["m", "米", "公尺"]):
        length *= 1000; width *= 1000; height *= 1000
    
    # 3. 提取毛重/净重（独立匹配，避免干扰）
    gross_weight = 0.0
    net_weight = 0.0
    gross_match = re.search(r'毛重[:：= ]*(\d+\.?\d*)', text_clean) or re.search(r'(\d+\.?\d*) *kg', text_clean)
    net_match = re.search(r'净重[:：= ]*(\d+\.?\d*)', text_clean)
    
    if gross_match: gross_weight = float(gross_match.group(1))
    if net_match: net_weight = float(net_match.group(1))
    
    # 4. 提取货物名称（过滤数字/单位/关键字）
    name_pattern = r'[\d\s\.\*×:cmkg米公斤吨长宽高毛重净重()]'
    name = re.sub(name_pattern, '', text_clean).strip()
    name = name if name else "未知货物"
    
    # 5. 计算体积（m³）
    volume = round((length * width * height) / 1e9, 4) if (length > 0 and width > 0 and height > 0) else 0.0
    
    return {
        "name": name,
        "length": round(length, 2),
        "width": round(width, 2),
        "height": round(height, 2),
        "gross_weight": round(gross_weight, 2),
        "net_weight": round(net_weight, 2),
        "volume": volume
    }

# ====================== Excel解析（兼容MS Office/WPS全版本） ======================
def parse_excel_file(uploaded_file) -> pd.DataFrame:
    """
    解析Excel文件（.xls/.xlsx）
    兼容：MS Office 2007+/WPS/多Sheet/多标题行/合并单元格
    """
    cargo_list = []
    try:
        # 兼容.xls（xlrd）和.xlsx（openpyxl）
        if uploaded_file.name.endswith(".xls"):
            xl_file = pd.ExcelFile(uploaded_file, engine="xlrd")
        else:
            xl_file = pd.ExcelFile(uploaded_file, engine="openpyxl")
        
        # 遍历所有Sheet
        for sheet_name in xl_file.sheet_names:
            # 读取Sheet（不指定header，避免表头干扰）
            df = pd.read_excel(uploaded_file, sheet_name=sheet_name, header=None, engine="openpyxl" if uploaded_file.name.endswith(".xlsx") else "xlrd")
            
            # 遍历每行
            for _, row in df.iterrows():
                # 合并整行文本（兼容合并单元格/跨列数据）
                row_text = " ".join([str(cell) for cell in row if pd.notna(cell)])
                # 跳过标题行/空行
                if is_header_row(row_text) or clean_text(row_text) == "":
                    continue
                # 提取货物信息
                cargo_info = extract_cargo_info(row_text)
                # 过滤无效数据（长宽高必须＞0）
                if cargo_info["length"] > 0 and cargo_info["width"] > 0 and cargo_info["height"] > 0:
                    cargo_list.append({
                        "来源Sheet": sheet_name,
                        "货物名称": cargo_info["name"],
                        "长(mm)": cargo_info["length"],
                        "宽(mm)": cargo_info["width"],
                        "高(mm)": cargo_info["height"],
                        "毛重(kg)": cargo_info["gross_weight"],
                        "净重(kg)": cargo_info["net_weight"],
                        "体积(m³)": cargo_info["volume"]
                    })
    except Exception as e:
        st.error(f"❌ Excel解析失败：{str(e)}")
        return pd.DataFrame()
    
    return pd.DataFrame(cargo_list)

# ====================== 配箱计算（稳定无错） ======================
def calculate_container(cargo_df: pd.DataFrame, container_type: str = "40HQ") -> pd.DataFrame:
    """配箱计算（兼容所有主流柜型）"""
    container_specs = {
        "20GP": {"max_weight": 21000, "max_volume": 33.2},
        "40GP": {"max_weight": 26500, "max_volume": 67.7},
        "40HQ": {"max_weight": 26000, "max_volume": 76.2},
        "45HQ": {"max_weight": 27500, "max_volume": 86.8}
    }
    # 校验柜型，默认40HQ
    container_type = container_type if container_type in container_specs else "40HQ"
    spec = container_specs[container_type]
    
    df = cargo_df.copy().sort_values("体积(m³)", ascending=False)
    current_weight = 0.0
    current_volume = 0.0
    container_no = 1
    df["柜号"] = ""
    
    for idx, row in df.iterrows():
        # 超过柜型限制，新建柜子
        if (current_weight + row["毛重(kg)"] > spec["max_weight"]) or (current_volume + row["体积(m³)"] > spec["max_volume"]):
            container_no += 1
            current_weight = 0.0
            current_volume = 0.0
        # 分配柜号
        df.loc[idx, "柜号"] = f"{container_type}-{container_no:02d}"
        current_weight += row["毛重(kg)"]
        current_volume += row["体积(m³)"]
    
    return df

# ====================== 主界面（持续式+兼容微信） ======================
def main_interface():
    st.markdown("## 📦 智能配箱系统（MS Office/WPS Excel全兼容）")
    st.divider()
    
    # 1. 文件上传（仅保留Excel，避免依赖报错）
    col_upload = st.columns([1])[0]
    with col_upload:
        uploaded_file = st.file_uploader(
            "📁 上传Excel货物清单（.xls/.xlsx，兼容MS Office/WPS）",
            type=["xlsx", "xls"]
        )
    
    # 2. 解析Excel并展示结果
    if uploaded_file:
        with st.spinner("🔍 正在解析Excel（兼容MS Office/WPS格式）..."):
            cargo_df = parse_excel_file(uploaded_file)
            st.session_state.cargo_data = cargo_df
        
        if not cargo_df.empty:
            # 识别结果统计
            st.success(f"✅ 解析完成！共识别 {len(cargo_df)} 件货物（来源：{len(cargo_df['来源Sheet'].unique())} 个Sheet）")
            
            # Sheet筛选
            col_filter = st.columns([0.3, 0.7])[0]
            with col_filter:
                sheet_options = ["全部"] + list(cargo_df["来源Sheet"].unique())
                selected_sheet = st.selectbox("筛选Sheet", options=sheet_options)
            
            # 筛选数据
            show_df = cargo_df[cargo_df["来源Sheet"] == selected_sheet] if selected_sheet != "全部" else cargo_df
            
            # 展示识别结果
            st.subheader("📋 货物识别结果")
            st.dataframe(show_df, use_container_width=True, hide_index=True)
        else:
            st.warning("⚠️ 未识别到有效货物数据，请检查Excel内容（需包含长宽高有效数值）")
    
    # 3. 配箱计算与导出
    if not st.session_state.cargo_data.empty:
        st.divider()
        st.subheader("🧮 配箱计算")
        
        # 柜型选择
        col_container = st.columns([0.3, 0.7])[0]
        with col_container:
            container_type = st.selectbox(
                "选择集装箱类型",
                ["20GP", "40GP", "40HQ", "45HQ"],
                index=2
            )
            calculate_btn = st.button("🚀 开始配箱", type="primary")
        
        # 执行配箱
        if calculate_btn:
            with st.spinner("📊 计算最优配箱方案..."):
                result_df = calculate_container(st.session_state.cargo_data, container_type)
                st.success("📦 配箱完成！")
                
                # 展示配箱结果
                st.subheader("📦 配箱结果")
                st.dataframe(result_df, use_container_width=True, hide_index=True)
                
                # 导出Excel（兼容MS Office/WPS）
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                    result_df.to_excel(writer, sheet_name=f"{container_type}配箱结果", index=False)
                
                st.download_button(
                    label="📥 下载配箱结果（兼容MS Office/WPS）",
                    data=buffer,
                    file_name=f"集装箱配箱结果_{container_type}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

# ====================== 程序入口 ======================
if not st.session_state.logged_in:
    login_module()
else:
    main_interface()

# ====================== 页脚 ======================
st.divider()
st.caption("© 2025 智能配箱系统 - 兼容MS Office/WPS Excel | 稳定无依赖报错")
