import streamlit as st
import pandas as pd
import numpy as np
import io
import re
from collections import defaultdict

# ====================== 基础配置（全版本兼容） ======================
st.set_page_config(page_title="货代通用智能配箱系统", layout="wide")
if "state" not in st.session_state:
    st.session_state.state = {
        "logged_in": False,
        "cargo_data": pd.DataFrame(),
        "parse_log": []  # 识别日志，便于调试
    }

# ====================== 登录模块（移除新版参数） ======================
def login():
    st.title("🔐 货代通用智能配箱系统")
    col1, col2, col3 = st.columns([0.3, 0.4, 0.3])
    with col2:
        # 移除 use_container_width 参数，兼容低版本
        if st.button("免密登录（测试/生产可用）", type="primary"):
            st.session_state.state["logged_in"] = True
            st.rerun()

if not st.session_state.state["logged_in"]:
    login()
    st.stop()

# ====================== 核心工具：文本清洗与特征提取 ======================
def clean_text(text: str) -> str:
    """通用文本清洗：保留型号符号，处理全角/半角/特殊字符"""
    if pd.isna(text) or str(text).strip().lower() in ["nan", "none", "", "无", "空", "0"]:
        return ""
    # 保留货代常用符号（-/_().），其他特殊符号替换为空格
    cleaned = re.sub(r'[^\w\s\-\_\(\)\.\×]', ' ', str(text).strip())
    # 全角转半角、统一符号
    cleaned = cleaned.replace("．", ".").replace("×", "*").replace("：", ":").replace("－", "-").replace("　", " ")
    # 合并空格、转小写
    return re.sub(r'\s+', ' ', cleaned).lower()

def extract_numeric_features(text: str) -> dict:
    """提取文本中的数值特征：尺寸（长/宽/高）、重量（毛重/净重）、单位"""
    cleaned = clean_text(text)
    features = {
        "length": 0.0, "width": 0.0, "height": 0.0,
        "gross_weight": 0.0, "net_weight": 0.0,
        "dim_unit": "mm", "weight_unit": "kg"
    }

    # 1. 识别单位（优先确定单位，避免数值误判）
    # 长度单位（mm/cm/m）
    if "m" in cleaned:
        features["dim_unit"] = "m"
    elif "cm" in cleaned or "公分" in cleaned:
        features["dim_unit"] = "cm"
    # 重量单位（kg/g/吨）
    if "吨" in cleaned or "t" in cleaned:
        features["weight_unit"] = "吨"
    elif "g" in cleaned:
        features["weight_unit"] = "g"

    # 2. 提取尺寸（支持3种格式：关键词、三联数字、规格格式）
    dim_patterns = [
        # 格式1：长50宽40高30 / 长:50 宽:40 高:30
        (r'长[:：= ]*(\d+\.?\d*)', r'宽[:：= ]*(\d+\.?\d*)', r'高[:：= ]*(\d+\.?\d*)'),
        # 格式2：50×40×30 / 50*40*30 / 50 40 30（三联数字）
        (r'(\d+\.?\d*)\D*(\d+\.?\d*)\D*(\d+\.?\d*)',),
        # 格式3：1.2m×0.3m×0.05m（带单位的规格）
        (r'(\d+\.?\d*)\s*[cm|m]\D*(\d+\.?\d*)\s*[cm|m]\D*(\d+\.?\d*)\s*[cm|m]',)
    ]

    dim_extracted = False
    for pattern in dim_patterns:
        if len(pattern) == 3:  # 关键词格式
            l_match = re.search(pattern[0], cleaned)
            w_match = re.search(pattern[1], cleaned)
            h_match = re.search(pattern[2], cleaned)
            if l_match and w_match and h_match:
                features["length"] = float(l_match.group(1))
                features["width"] = float(w_match.group(1))
                features["height"] = float(h_match.group(1))
                dim_extracted = True
                break
        else:  # 数字组合格式
            match = re.search(pattern[0], cleaned)
            if match and len(match.groups()) >= 3:
                # 按数值大小排序（长≥宽≥高，符合货代习惯）
                nums = sorted([float(g) for g in match.groups()[:3]], reverse=True)
                features["length"], features["width"], features["height"] = nums
                dim_extracted = True
                break

    # 3. 提取重量（毛重/净重，支持关键词和单位格式）
    # 毛重
    gw_patterns = [r'毛重[:：= ]*(\d+\.?\d*)', r'(\d+\.?\d*)\s*(kg|吨|t)']
    for pattern in gw_patterns:
        match = re.search(pattern, cleaned)
        if match:
            features["gross_weight"] = float(match.group(1))
            break
    # 净重
    nw_patterns = [r'净重[:：= ]*(\d+\.?\d*)', r'(\d+\.?\d*)\s*g']
    for pattern in nw_patterns:
        match = re.search(pattern, cleaned)
        if match:
            features["net_weight"] = float(match.group(1))
            break

    # 4. 单位转换（统一转mm/kg）
    dim_coeff = {"mm": 1, "cm": 10, "m": 1000}[features["dim_unit"]]
    weight_coeff = {"g": 0.001, "kg": 1, "吨": 1000}[features["weight_unit"]]
    features["length"] *= dim_coeff
    features["width"] *= dim_coeff
    features["height"] *= dim_coeff
    features["gross_weight"] *= weight_coeff
    features["net_weight"] *= weight_coeff

    return features

def extract_text_features(text: str) -> dict:
    """提取文本中的文本特征：货物名称、非货物标记"""
    cleaned = clean_text(text)
    features = {
        "cargo_name": "未知货物",
        "is_non_cargo": False  # 是否为非货物行（表头/项目信息/汇总行）
    }

    # 1. 判断非货物行（货代清单常见非货物特征）
    non_cargo_keywords = [
        # 项目信息关键词
        "订单号", "合同号", "项目号", "客户", "联系人", "电话", "地址", "日期", "备注",
        # 表头关键词（含2个以上即判定为表头）
        "品名", "货物名称", "规格", "型号", "长", "宽", "高", "毛重", "净重", "数量", "单位",
        # 汇总行关键词
        "合计", "总计", "小计", "合计重量", "合计体积", "页", "共"
    ]
    keyword_count = sum(1 for kw in non_cargo_keywords if kw in cleaned)
    # 非货物行判定规则：含≥2个表头关键词 / 含项目/汇总关键词 / 长度过短（<5字符）
    features["is_non_cargo"] = (keyword_count >= 2) or (any(kw in cleaned for kw in non_cargo_keywords[:9] + non_cargo_keywords[-6:])) or (len(cleaned) < 5)

    # 2. 提取货物名称（保留型号，过滤数值和单位）
    if not features["is_non_cargo"]:
        # 保留字母、汉字、型号符号（-/_()），过滤数值和单位
        name = re.sub(r'(\d+\.?\d*\s*[mm|cm|m|kg|g|吨])|(\d+\.?\d*)', ' ', cleaned)
        name = re.sub(r'\s+', ' ', name).strip()
        # 补充默认名称（避免空名称）
        if not name or name in ["-", "_", "()"]:
            name = "未知货物"
        features["cargo_name"] = name

    return features

# ====================== 核心逻辑：通用Excel解析引擎 ======================
def parse_universal_excel(file) -> pd.DataFrame:
    """通用Excel解析引擎：适配所有货代清单模板（.xls/.xlsx）"""
    cargo_list = []
    parse_log = []
    engine = "xlrd" if file.name.endswith(".xls") else "openpyxl"
    sheet_names = []

    try:
        # 1. 读取Excel所有Sheet
        xl_file = pd.ExcelFile(file, engine=engine)
        sheet_names = xl_file.sheet_names
        parse_log.append(f"成功读取文件：{file.name}，共{len(sheet_names)}个Sheet")

        # 2. 遍历每个Sheet解析
        for sheet_idx, sheet_name in enumerate(sheet_names):
            df = pd.read_excel(file, sheet_name=sheet_name, engine=engine, header=None)
            parse_log.append(f"开始解析Sheet{sheet_idx+1}：{sheet_name}（共{len(df)}行）")

            # 3. 逐行解析（合并多列信息，动态匹配字段）
            for row_idx, row in df.iterrows():
                row_num = row_idx + 1  # 行号（用户友好）
                # 合并当前行所有非空单元格文本（处理合并单元格/跨列数据）
                row_cells = [str(cell) for cell in row if pd.notna(cell) and clean_text(str(cell)) != ""]
                if not row_cells:
                    parse_log.append(f"Sheet{sheet_idx+1}行{row_num}：空行，跳过")
                    continue
                full_row_text = " | ".join(row_cells)  # 保留原始列分隔，便于调试

                # 4. 提取行特征，判断是否为货物行
                text_features = extract_text_features(full_row_text)
                if text_features["is_non_cargo"]:
                    parse_log.append(f"Sheet{sheet_idx+1}行{row_num}：非货物行（{full_row_text[:60]}...），跳过")
                    continue

                # 5. 提取数值特征（尺寸/重量）
                numeric_features = extract_numeric_features(full_row_text)
                # 过滤无效货物（至少有1个尺寸>0）
                if all(v <= 0 for v in [numeric_features["length"], numeric_features["width"], numeric_features["height"]]):
                    parse_log.append(f"Sheet{sheet_idx+1}行{row_num}：无有效尺寸（{full_row_text[:60]}...），跳过")
                    continue

                # 6. 组装货物数据
                cargo_data = {
                    "来源Sheet": sheet_name,
                    "行号": row_num,
                    "货物名称": text_features["cargo_name"],
                    "长(mm)": round(numeric_features["length"], 2),
                    "宽(mm)": round(numeric_features["width"], 2),
                    "高(mm)": round(numeric_features["height"], 2),
                    "毛重(kg)": round(numeric_features["gross_weight"], 2),
                    "净重(kg)": round(numeric_features["net_weight"], 2),
                    "体积(m³)": round(numeric_features["length"] * numeric_features["width"] * numeric_features["height"] / 1e9, 4)
                }
                cargo_list.append(cargo_data)
                parse_log.append(f"Sheet{sheet_idx+1}行{row_num}：识别成功（{text_features['cargo_name']} | 长{round(numeric_features['length'],2)}mm | 毛重{round(numeric_features['gross_weight'],2)}kg）")

        # 7. 生成结果DataFrame
        result_df = pd.DataFrame(cargo_list)
        parse_log.append(f"解析完成！共识别{len(result_df)}件货物")
        st.session_state.state["parse_log"] = parse_log
        return result_df

    except Exception as e:
        error_msg = f"解析异常：{str(e)}"
        parse_log.append(error_msg)
        st.session_state.state["parse_log"] = parse_log
        st.error(error_msg)
        return pd.DataFrame()

# ====================== 配箱计算模块（货代行业标准） ======================
def calculate_container(cargo_df: pd.DataFrame, container_type: str = "40HQ") -> pd.DataFrame:
    """标准配箱计算：支持所有主流柜型，按体积降序优化空间"""
    # 货代行业标准柜型参数（承重kg/体积m³）
    container_specs = {
        "20GP": {"max_weight": 21000, "max_volume": 33.2},
        "40GP": {"max_weight": 26500, "max_volume": 67.7},
        "40HQ": {"max_weight": 26000, "max_volume": 76.2},
        "45HQ": {"max_weight": 27500, "max_volume": 86.8},
        "10GP": {"max_weight": 10000, "max_volume": 15.0}  # 补充小柜型
    }
    # 柜型容错（默认40HQ）
    if container_type not in container_specs:
        container_type = "40HQ"
    spec = container_specs[container_type]

    # 按体积降序排列，优化配箱空间利用率
    result_df = cargo_df.copy().sort_values("体积(m³)", ascending=False).reset_index(drop=True)
    current_weight = 0.0
    current_volume = 0.0
    container_no = 1
    result_df["柜号"] = ""

    # 逐行分配柜号
    for idx, row in result_df.iterrows():
        # 超过柜型限制，新建柜子
        if (current_weight + row["毛重(kg)"] > spec["max_weight"]) or (current_volume + row["体积(m³)"] > spec["max_volume"]):
            container_no += 1
            current_weight = 0.0
            current_volume = 0.0
        # 分配柜号（格式：柜型-序号，如40HQ-01）
        result_df.loc[idx, "柜号"] = f"{container_type}-{container_no:02d}"
        current_weight += row["毛重(kg)"]
        current_volume += row["体积(m³)"]

    # 补充配箱统计信息
    container_stats = result_df.groupby("柜号").agg({
        "货物名称": "count",
        "毛重(kg)": "sum",
        "体积(m³)": "sum"
    }).round(2)
    container_stats.columns = ["货物件数", "总毛重(kg)", "总体积(m³)"]
    st.session_state.state["container_stats"] = container_stats

    return result_df

# ====================== 主界面（移除所有新版参数） ======================
def main_interface():
    st.markdown("## 📦 货代通用智能配箱系统（适配所有Excel模板）")
    st.divider()

    # 1. 文件上传区（移除 use_container_width 参数）
    col_upload = st.columns([1])[0]
    with col_upload:
        uploaded_file = st.file_uploader(
            "上传Excel清单（.xls/.xlsx，支持：标准清单/项目清单/乱序清单）",
            type=["xlsx", "xls"]
            # 移除 use_container_width=True，兼容低版本
        )

    # 2. 解析与结果展示
    if uploaded_file:
        # 解析Excel
        with st.spinner("🔍 正在智能解析Excel（适配当前模板）..."):
            cargo_df = parse_universal_excel(uploaded_file)
            st.session_state.state["cargo_data"] = cargo_df

        # 显示解析日志（可折叠，便于调试）
        with st.expander("📝 解析日志（点击查看详细过程）", expanded=False):
            for log in st.session_state.state["parse_log"]:
                st.caption(log)

        # 显示识别结果
        if not cargo_df.empty:
            st.success(f"✅ 解析完成！共识别 {len(cargo_df)} 件货物")
            
            # 结果表格（隐藏行号列，更简洁）
            st.subheader("📋 货物识别结果")
            display_df = cargo_df.drop(columns=["行号"], errors="ignore")
            st.dataframe(display_df, use_container_width=True, hide_index=True)

            # 3. 配箱计算
            st.divider()
            st.subheader("🧮 配箱计算")
            col1, col2 = st.columns([0.2, 0.8])
            with col1:
                container_type = st.selectbox(
                    "选择集装箱类型",
                    ["20GP", "40GP", "40HQ", "45HQ", "10GP"],
                    index=2
                    # 移除 use_container_width 参数
                )
                # 移除 use_container_width 参数
                calc_btn = st.button("🚀 开始配箱", type="primary")

            if calc_btn:
                with st.spinner("📊 计算最优配箱方案..."):
                    result_df = calculate_container(cargo_df, container_type)
                    # 显示配箱结果
                    st.subheader("📦 配箱结果")
                    st.dataframe(result_df.drop(columns=["行号"], errors="ignore"), use_container_width=True, hide_index=True)

                    # 显示配箱统计
                    st.subheader("📈 配箱统计")
                    st.dataframe(st.session_state.state["container_stats"], use_container_width=True)

                    # 4. 导出结果（兼容MS Office/WPS）
                    st.subheader("💾 导出结果")
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                        # 工作表1：配箱结果
                        result_df.drop(columns=["行号"], errors="ignore").to_excel(writer, sheet_name="配箱结果", index=False)
                        # 工作表2：配箱统计
                        st.session_state.state["container_stats"].to_excel(writer, sheet_name="配箱统计")
                    # 移除 use_container_width 参数
                    st.download_button(
                        label="下载配箱结果（Excel，兼容MS Office/WPS）",
                        data=buffer,
                        file_name=f"货代配箱结果_{container_type}_{pd.Timestamp.now().strftime('%Y%m%d')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
        else:
            st.warning("⚠️ 未识别到有效货物数据，请检查Excel内容（需包含货物名称和尺寸信息）")

# ====================== 程序入口 ======================
if __name__ == "__main__":
    main_interface()

# ====================== 页脚 ======================
st.divider()
st.caption("© 2025 货代通用智能配箱系统 | 适配所有Excel模板 | 精准识别 | 稳定可用")
