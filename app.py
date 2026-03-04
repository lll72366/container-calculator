import streamlit as st
import pandas as pd
import numpy as np
import io
import re

# ====================== 基础配置（全版本兼容） ======================
st.set_page_config(page_title="货代装箱清单配箱系统（精准版）", layout="wide")
if "state" not in st.session_state:
    st.session_state.state = {
        "logged_in": False,
        "cargo_data": pd.DataFrame(),
        "parse_log": [],
        "source_file_type": ""  # 记录源文件类型（Excel/CSV）
    }

# ====================== 登录模块 ======================
def login():
    st.title("🔐 货代装箱清单配箱系统（Excel+CSV双兼容）")
    col1, col2, col3 = st.columns([0.3, 0.4, 0.3])
    with col2:
        if st.button("免密登录（测试/生产可用）", type="primary"):
            st.session_state.state["logged_in"] = True
            st.rerun()

if not st.session_state.state["logged_in"]:
    login()
    st.stop()

# ====================== 核心优化1：专属围栏清单识别规则 ======================
def clean_text_ultimate(text: str) -> str:
    """
    优化点：
    1. 保留围栏型号（如SHRL50125900），不被过滤
    2. 全角→半角+单位统一+杂字符过滤
    """
    if pd.isna(text) or str(text).strip().lower() in ["nan", "none", "", "无", "空", "0"]:
        return ""
    
    # 全角转半角
    def full2half(s):
        result = []
        for char in s:
            code = ord(char)
            if code == 0x3000:
                result.append(' ')
            elif 0xFF01 <= code <= 0xFF5E:
                result.append(chr(code - 0xFEE0))
            else:
                result.append(char)
        return ''.join(result)
    
    text = full2half(str(text).strip())
    
    # 单位统一（重点保留围栏型号中的数字/字母）
    unit_map = {
        "米":"m", "公尺":"m", "厘米":"cm", "公分":"cm", "毫米":"mm",
        "吨":"t", "千克":"kg", "公斤":"kg", "克":"g"
    }
    for old, new in unit_map.items():
        text = text.replace(old, new)
    
    # 过滤杂字符（保留：字母/数字/中文/型号符号(-/_) / 单位(m/cm/mm/t/kg/g)）
    cleaned = re.sub(r'[^\w\s\-\_\u4e00-\u9fa5mcmmmtkgɡ]', ' ', text)
    cleaned = re.sub(r'\s+', ' ', cleaned).lower()
    
    return cleaned

def extract_fence_spec(spec_text: str) -> tuple:
    """
    优化点：专属围栏清单规格提取
    支持：2000×300×50mm（网片）、1500×100×100mm（立柱）等格式
    返回：(长, 宽, 高, 单位)
    """
    cleaned = clean_text_ultimate(spec_text)
    
    # 提取尺寸数值（优先匹配×分隔的三联数）
    dim_pattern = r'(\d+\.?\d*)\D*(\d+\.?\d*)\D*(\d+\.?\d*)'
    match = re.search(dim_pattern, cleaned)
    if match and len(match.groups()) >= 3:
        nums = [float(g) for g in match.groups()[:3] if float(g) > 0]
        if len(nums) == 3:
            # 识别单位（默认mm）
            unit = "mm"
            if "m" in cleaned:
                unit = "m"
            elif "cm" in cleaned:
                unit = "cm"
            # 转换为mm
            coeff = {"m":1000, "cm":10, "mm":1}[unit]
            length = round(nums[0] * coeff, 2)
            width = round(nums[1] * coeff, 2)
            height = round(nums[2] * coeff, 2)
            return length, width, height, unit
    
    return 0.0, 0.0, 0.0, "mm"

def extract_fence_name(name_text: str, spec_text: str) -> str:
    """
    优化点：围栏名称+型号合并（如SHRL50125900 + 围栏）
    """
    name_clean = clean_text_ultimate(name_text)
    spec_clean = clean_text_ultimate(spec_text)
    
    # 提取型号（字母+数字组合，长度≥8的视为围栏型号）
    model_pattern = r'([a-zA-Z0-9]{8,})'
    model_match = re.search(model_pattern, name_clean + spec_clean)
    model = model_match.group(1) if model_match else ""
    
    # 提取货物名称（过滤数值/单位）
    name = re.sub(r'(\d+\.?\d*)|(m|cm|mm|t|kg|g)', ' ', name_clean)
    name = re.sub(r'\s+', ' ', name).strip()
    if not name:
        # 从规格中提取名称（如网片/立柱）
        name = "围栏" if "围栏" in spec_clean else "网片" if "网片" in spec_clean else "立柱" if "立柱" in spec_clean else "未知货物"
    
    # 合并型号+名称
    full_name = f"{model} {name}" if model else name
    return full_name.strip()

# ====================== 核心优化2：双格式解析（Excel围栏清单+CSV装箱清单） ======================
def parse_excel_fence(file) -> pd.DataFrame:
    """优化点：专属Excel围栏清单解析"""
    cargo_list = []
    parse_log = []
    engine = "xlrd" if file.name.endswith(".xls") else "openpyxl"
    st.session_state.state["source_file_type"] = "Excel（围栏清单）"

    try:
        xl_file = pd.ExcelFile(file, engine=engine)
        sheet_names = xl_file.sheet_names
        parse_log.append(f"成功读取Excel围栏清单：{file.name}，共{len(sheet_names)}个Sheet")

        for sheet_idx, sheet_name in enumerate(sheet_names):
            df = pd.read_excel(file, sheet_name=sheet_name, engine=engine, header=None)
            df = df.reset_index(drop=True)
            parse_log.append(f"解析Sheet{sheet_idx+1}：{sheet_name}（共{len(df)}行）")

            # 围栏清单专属过滤：非货物行关键词（更精准）
            fence_non_cargo = ["订单号", "合同号", "项目号", "客户", "联系人", "电话", "合计", "总计", "小计", "页", "共"]
            unit_cache = []  # 单位缓存

            for row_idx, row in df.iterrows():
                row_num = row_idx + 1
                row_cells = [str(cell) for cell in row if pd.notna(cell) and clean_text_ultimate(str(cell)) != ""]
                if not row_cells:
                    parse_log.append(f"Sheet{sheet_idx+1}行{row_num}：空行，跳过")
                    continue
                
                current_text = " | ".join(row_cells)
                current_clean = clean_text_ultimate(current_text)

                # 过滤非货物行（围栏清单专属规则）
                if any(kw in current_clean for kw in fence_non_cargo) and len(re.findall(r'\d+\.?\d*', current_clean)) < 3:
                    parse_log.append(f"Sheet{sheet_idx+1}行{row_num}：非货物行，跳过")
                    continue

                # 提取单位（围栏清单多在规格列）
                dim_unit = "mm"
                if "m" in current_clean:
                    dim_unit = "m"
                elif "cm" in current_clean:
                    dim_unit = "cm"
                weight_unit = "kg"
                if "t" in current_clean:
                    weight_unit = "t"

                # 提取规格（核心优化：专属围栏规格解析）
                length, width, height, _ = extract_fence_spec(current_text)
                if length == 0:
                    parse_log.append(f"Sheet{sheet_idx+1}行{row_num}：无有效尺寸，跳过")
                    continue

                # 提取重量（围栏清单毛重多在单独列）
                gross_weight = 0.0
                weight_match = re.search(r'(\d+\.?\d*)\s*(t|kg)', current_clean)
                if weight_match:
                    gross_weight = float(weight_match.group(1))
                    if weight_match.group(2) == "t":
                        gross_weight *= 1000
                gross_weight = round(gross_weight, 2)

                # 提取名称（型号+名称合并）
                name = extract_fence_name(current_text, current_text)

                # 组装数据（匹配CSV装箱清单字段）
                cargo_data = {
                    "来源Sheet": sheet_name,
                    "行号": row_num,
                    "货物名称": name,
                    "长(mm)": length,
                    "宽(mm)": width,
                    "高(mm)": height,
                    "毛重(kg)": gross_weight,
                    "净重(kg)": 0.0,  # 围栏清单多无净重，默认0
                    "体积(m³)": round(length * width * height / 1e9, 4),
                    "识别说明": f"围栏清单专属解析 | 尺寸单位：{dim_unit}→mm"
                }
                cargo_list.append(cargo_data)
                parse_log.append(
                    f"Sheet{sheet_idx+1}行{row_num}：识别成功 | {name} "
                    f"| 长{length}mm 宽{width}mm 高{height}mm | 毛重{gross_weight}kg"
                )

        result_df = pd.DataFrame(cargo_list)
        parse_log.append(f"Excel围栏清单解析完成！共识别{len(result_df)}件货物")
        st.session_state.state["parse_log"] = parse_log
        return result_df

    except Exception as e:
        error_msg = f"Excel解析异常：{str(e)}"
        parse_log.append(error_msg)
        st.session_state.state["parse_log"] = parse_log
        st.error(error_msg)
        return pd.DataFrame()

def parse_csv_packing(file) -> pd.DataFrame:
    """优化点：CSV装箱清单专属解析（结构化数据直接匹配）"""
    cargo_list = []
    parse_log = []
    st.session_state.state["source_file_type"] = "CSV（装箱清单）"

    try:
        # 读取CSV（自动识别表头）
        df = pd.read_csv(file, encoding="utf-8-sig")  # 兼容中文编码
        parse_log.append(f"成功读取CSV装箱清单：{file.name}，共{len(df)}行")

        # CSV字段映射（匹配常见装箱清单字段）
        field_map = {
            "货物名称": ["货物名称", "品名", "名称"],
            "长(mm)": ["长", "长度", "长(mm)"],
            "宽(mm)": ["宽", "宽度", "宽(mm)"],
            "高(mm)": ["高", "高度", "高(mm)"],
            "毛重(kg)": ["毛重", "重量", "毛重(kg)"],
            "体积(m³)": ["体积", "体积(m³)"]
        }

        # 动态匹配字段
        df_columns = [col.lower() for col in df.columns]
        matched_fields = {}
        for target, sources in field_map.items():
            for source in sources:
                if source.lower() in df_columns:
                    matched_fields[target] = source
                    break

        # 解析每行数据
        for row_idx, row in df.iterrows():
            row_num = row_idx + 1

            # 提取核心字段（结构化数据直接取值）
            name = row[matched_fields.get("货物名称")] if "货物名称" in matched_fields else "未知货物"
            length = float(row[matched_fields.get("长(mm)")]) if "长(mm)" in matched_fields else 0.0
            width = float(row[matched_fields.get("宽(mm)")]) if "宽(mm)" in matched_fields else 0.0
            height = float(row[matched_fields.get("高(mm)")]) if "高(mm)" in matched_fields else 0.0
            gross_weight = float(row[matched_fields.get("毛重(kg)")]) if "毛重(kg)" in matched_fields else 0.0
            volume = float(row[matched_fields.get("体积(m³)")]) if "体积(m³)" in matched_fields else round(length * width * height / 1e9, 4)

            # 过滤无效数据
            if length <= 0 and width <= 0 and height <= 0:
                parse_log.append(f"行{row_num}：无有效尺寸，跳过")
                continue

            # 组装数据（与Excel解析结果格式统一）
            cargo_data = {
                "来源Sheet": "CSV清单",
                "行号": row_num,
                "货物名称": clean_text_ultimate(name),
                "长(mm)": round(length, 2),
                "宽(mm)": round(width, 2),
                "高(mm)": round(height, 2),
                "毛重(kg)": round(gross_weight, 2),
                "净重(kg)": 0.0,
                "体积(m³)": round(volume, 4),
                "识别说明": "CSV装箱清单结构化解析"
            }
            cargo_list.append(cargo_data)
            parse_log.append(
                f"行{row_num}：识别成功 | {name} "
                f"| 长{length}mm 宽{width}mm 高{height}mm | 毛重{gross_weight}kg"
            )

        result_df = pd.DataFrame(cargo_list)
        parse_log.append(f"CSV装箱清单解析完成！共识别{len(result_df)}件货物")
        st.session_state.state["parse_log"] = parse_log
        return result_df

    except Exception as e:
        error_msg = f"CSV解析异常：{str(e)}"
        parse_log.append(error_msg)
        st.session_state.state["parse_log"] = parse_log
        st.error(error_msg)
        return pd.DataFrame()

def parse_universal_file(file) -> pd.DataFrame:
    """优化点：自动识别文件类型，调用对应解析函数"""
    if file.name.endswith((".xls", ".xlsx")):
        return parse_excel_fence(file)
    elif file.name.endswith(".csv"):
        return parse_csv_packing(file)
    else:
        st.error("不支持的文件格式！仅支持Excel(.xls/.xlsx)和CSV(.csv)")
        return pd.DataFrame()

# ====================== 核心优化3：配箱计算精准化 ======================
def calculate_container_optimized(cargo_df: pd.DataFrame, container_type: str = "40HQ") -> pd.DataFrame:
    """
    优化点：
    1. 围栏货物按体积+重量双重校验（围栏多为轻泡货，重点控体积）
    2. 配箱结果与CSV装箱清单字段对齐
    """
    # 标准柜型参数（精准匹配货代实际使用值）
    container_specs = {
        "20GP": {"max_weight": 21000, "max_volume": 33.2},
        "40GP": {"max_weight": 26500, "max_volume": 67.7},
        "40HQ": {"max_weight": 26000, "max_volume": 76.2},
        "45HQ": {"max_weight": 27500, "max_volume": 86.8},
        "10GP": {"max_weight": 10000, "max_volume": 15.0}
    }
    container_type = container_type if container_type in container_specs else "40HQ"
    spec = container_specs[container_type]

    # 围栏清单专属排序：轻泡货按体积降序，优先装大体积货物
    result_df = cargo_df.copy().sort_values(["体积(m³)", "毛重(kg)"], ascending=False).reset_index(drop=True)
    current_weight, current_volume, container_no = 0.0, 0.0, 1
    result_df["柜号"] = ""

    for idx, row in result_df.iterrows():
        # 双重校验：重量+体积均不超限
        weight_exceed = (current_weight + row["毛重(kg)"]) > spec["max_weight"]
        volume_exceed = (current_volume + row["体积(m³)"]) > spec["max_volume"]
        if weight_exceed or volume_exceed:
            container_no += 1
            current_weight, current_volume = 0.0, 0.0
        
        # 柜号格式与CSV装箱清单对齐（如40HQ-01）
        result_df.loc[idx, "柜号"] = f"{container_type}-{container_no:02d}"
        current_weight += row["毛重(kg)"]
        current_volume += row["体积(m³)"]

    # 配箱统计（精准到小数点后2位）
    container_stats = result_df.groupby("柜号").agg({
        "货物名称": "count",
        "毛重(kg)": "sum",
        "体积(m³)": "sum"
    }).round(2)
    container_stats.columns = ["货物件数", "总毛重(kg)", "总体积(m³)"]
    st.session_state.state["container_stats"] = container_stats

    return result_df

# ====================== 主界面优化（用户体验+双格式适配） ======================
def main_interface():
    st.markdown("## 📦 货代装箱清单配箱系统（优化版）")
    st.markdown("### 支持：Excel围栏清单 | CSV装箱清单 | 精准识别 | 数据一致")
    st.divider()

    # 文件上传（支持Excel+CSV）
    col_upload = st.columns([1])[0]
    with col_upload:
        uploaded_file = st.file_uploader(
            "上传清单文件（Excel/.xls/.xlsx 或 CSV/.csv）",
            type=["xlsx", "xls", "csv"]
        )

    if uploaded_file:
        with st.spinner(f"🔍 正在解析{st.session_state.state.get('source_file_type', '文件')}..."):
            cargo_df = parse_universal_file(uploaded_file)
            st.session_state.state["cargo_data"] = cargo_df

        # 解析日志（折叠展示）
        with st.expander("📝 解析日志（点击查看详细过程）", expanded=False):
            for log in st.session_state.state["parse_log"]:
                st.caption(log)

        if not cargo_df.empty:
            st.success(f"✅ 解析完成！共识别 {len(cargo_df)} 件货物（{st.session_state.state['source_file_type']}）")
            
            # 识别结果（字段与CSV装箱清单对齐）
            st.subheader("📋 货物识别结果（与装箱清单字段一致）")
            display_cols = ["货物名称", "长(mm)", "宽(mm)", "高(mm)", "毛重(kg)", "体积(m³)", "识别说明"]
            display_df = cargo_df[display_cols].copy()
            st.dataframe(display_df, use_container_width=True, hide_index=True)

            # 配箱计算（优化版）
            st.divider()
            st.subheader("🧮 精准配箱计算（围栏清单专属规则）")
            col1, col2 = st.columns([0.2, 0.8])
            with col1:
                container_type = st.selectbox(
                    "选择集装箱类型",
                    ["20GP", "40GP", "40HQ", "45HQ", "10GP"],
                    index=2
                )
                calc_btn = st.button("🚀 开始配箱", type="primary")

            if calc_btn:
                with st.spinner("📊 计算最优配箱方案（体积+重量双重校验）..."):
                    result_df = calculate_container_optimized(cargo_df, container_type)
                    
                    # 配箱结果（字段对齐）
                    st.subheader("📦 配箱结果（与CSV装箱清单格式一致）")
                    result_display_cols = ["货物名称", "长(mm)", "宽(mm)", "高(mm)", "毛重(kg)", "体积(m³)", "柜号"]
                    st.dataframe(result_df[result_display_cols], use_container_width=True, hide_index=True)

                    # 配箱统计（精准）
                    st.subheader("📈 配箱统计（精准到小数点后2位）")
                    st.dataframe(st.session_state.state["container_stats"], use_container_width=True)

                    # 导出结果（兼容Excel/CSV）
                    st.subheader("💾 导出结果（匹配装箱清单格式）")
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                        # 配箱结果（与原始装箱清单字段一致）
                        result_df[result_display_cols].to_excel(writer, sheet_name="配箱结果", index=False)
                        # 配箱统计
                        st.session_state.state["container_stats"].to_excel(writer, sheet_name="配箱统计")
                    st.download_button(
                        label="下载配箱结果（Excel格式）",
                        data=buffer,
                        file_name=f"配箱结果_{container_type}_{pd.Timestamp.now().strftime('%Y%m%d')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
        else:
            st.warning("⚠️ 未识别到有效货物数据，请检查文件内容")

# ====================== 程序入口 ======================
if __name__ == "__main__":
    main_interface()

# ====================== 页脚 ======================
st.divider()
st.caption("© 2025 货代装箱清单配箱系统 | Excel+CSV双兼容 | 围栏清单专属优化 | 数据100%一致")
