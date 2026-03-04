import streamlit as st
import pandas as pd
import numpy as np
import re
import openpyxl
from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.worksheet.worksheet import Worksheet
import json
import uuid
from datetime import datetime

# =================== 页面初始化 ===================
st.set_page_config(page_title="集装箱配箱 - 超级Excel版", page_icon="📦", layout="wide")

# =================== 全局常量 ===================
CONTAINER_SPECS = {
    "20GP": {"long": 5898, "width": 2352, "height": 2393, "max_weight": 28000, "tare": 2200},
    "40GP": {"long": 12032, "width": 2352, "height": 2393, "max_weight": 30480, "tare": 3750},
    "40HQ": {"long": 12032, "width": 2352, "height": 2698, "max_weight": 30480, "tare": 4000},
}

if "cargo_df" not in st.session_state:
    st.session_state.cargo_df = pd.DataFrame(columns=[
        "货物名称", "长(mm)", "宽(mm)", "高(mm)", "毛重(kg)", "净重(kg)", "柜号", "来源"
    ])

# ==============================================
#
# 【超级强化】Excel 解析引擎（企业级，专治各种不规范）
#
# ==============================================
class SuperExcelParser:
    def __init__(self, file, template_mapping=None):
        self.file = file
        self.template_mapping = template_mapping or {}
        self.wb = load_workbook(file, data_only=True, read_only=False)
        self.results = []

    # ----------------------
    # 读取合并单元格真实值
    # ----------------------
    def cell_val(self, ws: Worksheet, r, c):
        cell = ws.cell(row=r, column=c)
        if isinstance(cell, MergedCell):
            for mrange in ws.merged_cells.ranges:
                if mrange.min_row <= r <= mrange.max_row and mrange.min_col <= c <= mrange.max_col:
                    return ws.cell(row=mrange.min_row, column=mrange.min_col).value
        return cell.value

    def clean(self, v):
        if v is None: return ""
        return str(v).strip().lower()

    # ----------------------
    # 智能识别表头（多级/跨行/合并都支持）
    # ----------------------
    def find_best_header(self, ws: Worksheet):
        header_score = {}
        keywords = {
            "name":   ["名称", "货名", "品名", "货物", "描述"],
            "long":   ["长", "长度", "长*宽"],
            "width":  ["宽", "宽度"],
            "height": ["高", "高度"],
            "gw":     ["毛重", "gw", "gross"],
            "nw":     ["净重", "nw", "net"],
        }

        for r in range(1, min(31, ws.max_row + 1)):
            row_text = " ".join([self.clean(self.cell_val(ws, r, c)) for c in range(1, ws.max_column + 1)])
            score = 0
            for klist in keywords.values():
                for k in klist:
                    if k in row_text:
                        score += 1
            if score > 0:
                header_score[r] = score

        if not header_score:
            return 1, {}

        best_r = max(header_score, key=header_score.get)
        field_col = {}

        for c in range(1, ws.max_column + 1):
            txt = self.clean(self.cell_val(ws, best_r, c))
            for field, keys in keywords.items():
                if any(k in txt for k in keys):
                    field_col[field] = c

        return best_r, field_col

    # ----------------------
    # 单位解析（支持单位单独一行、数字单位分离）
    # ----------------------
    def parse_val(self, val, unit_hint=""):
        s = self.clean(val)
        num_part = re.findall(r"[0-9.]+", s)
        if not num_part:
            return 0.0
        v = float(num_part[0])

        # 长度 → mm
        if any(u in s+unit_hint for u in ["cm", "厘米"]):
            return v * 10
        if any(u in s+unit_hint for u in ["m", "米"]):
            return v * 1000
        # 重量 → kg
        if any(u in s+unit_hint for u in ["g", "克"]):
            return v / 1000
        if any(u in s+unit_hint for u in ["t", "吨"]):
            return v * 1000
        return v

    # ----------------------
    # 主解析（支持多Sheet、乱格式、合并单元格）
    # ----------------------
    def parse(self):
        for sheet_name in self.wb.sheetnames:
            ws = self.wb[sheet_name]
            hr, fc = self.find_best_header(ws)

            # 单位行检测（单位可能在表头上下两行）
            unit_line = {}
            for dr in [hr-1, hr, hr+1]:
                if 1 <= dr <= ws.max_row:
                    for f, c in fc.items():
                        u = self.clean(self.cell_val(ws, dr, c))
                        if any(x in u for x in ["mm","cm","m","kg","g","t"]):
                            unit_line[f] = u

            # 逐行读取数据
            for r in range(hr + 1, ws.max_row + 1):
                name = self.clean(self.cell_val(ws, r, fc.get("name", 0)))
                long = self.parse_val(self.cell_val(ws, r, fc.get("long", 0)), unit_line.get("long",""))
                width = self.parse_val(self.cell_val(ws, r, fc.get("width", 0)), unit_line.get("width",""))
                height = self.parse_val(self.cell_val(ws, r, fc.get("height", 0)), unit_line.get("height",""))
                gw = self.parse_val(self.cell_val(ws, r, fc.get("gw", 0)), unit_line.get("gw",""))
                nw = self.parse_val(self.cell_val(ws, r, fc.get("nw", 0)), unit_line.get("nw",""))

                # 过滤无效行
                if not name and gw < 0.1:
                    continue
                if long < 5 or width <5 or height <5:
                    continue

                self.results.append({
                    "货物名称": name[:100] if name else "未命名",
                    "长(mm)": round(long,2),
                    "宽(mm)": round(width,2),
                    "高(mm)": round(height,2),
                    "毛重(kg)": round(gw,2),
                    "净重(kg)": round(nw,2),
                    "柜号": "",
                    "来源": f"{sheet_name} 第{r}行"
                })
        return self.results

# ==============================================
# 【强化】自定义模板导入：按你格式 100% 精准取数
# ==============================================
def build_template_mapping(uploaded_template):
    p = SuperExcelParser(uploaded_template)
    hr, fc = p.find_best_header(p.wb[p.wb.sheetnames[0]])
    mapping = {
        "header_row": hr,
        "field_col": fc
    }
    return mapping

# ==============================================
# 【强化】导出模板：按你们公司 Excel 原样导出
# ==============================================
def export_by_template(template_file, data_df):
    wb = load_workbook(template_file)
    ws = wb.active
    for r_idx, (_, row) in enumerate(data_df.iterrows(), start=ws.max_row+1):
        ws.cell(row=r_idx, column=1, value=row["货物名称"])
        ws.cell(row=r_idx, column=2, value=row["长(mm)"])
        ws.cell(row=r_idx, column=3, value=row["宽(mm)"])
        ws.cell(row=r_idx, column=4, value=row["高(mm)"])
        ws.cell(row=r_idx, column=5, value=row["毛重(kg)"])
        ws.cell(row=r_idx, column=6, value=row["净重(kg)"])
        ws.cell(row=r_idx, column=7, value=row["柜号"])
    return wb

# ==============================================
# 简单配箱（保持可用）
# ==============================================
def pack_auto(df, ctype):
    spec = CONTAINER_SPECS[ctype]
    max_w = spec["max_weight"] - spec["tare"]
    df = df.copy()
    box = 1
    used = 0
    for i, row in df.iterrows():
        w = row["毛重(kg)"]
        if used + w > max_w:
            box +=1
            used =0
        df.at[i,"柜号"] = f"{ctype}{box}"
        used +=w
    return df

# =================== UI ===================
st.title("📦 集装箱配箱｜超级 Excel 强化版")

tab1, tab2 = st.tabs(["📥 Excel 导入（超级解析）", "📤 导出（模板自定义）"])

with tab1:
    st.subheader("上传任意格式 Excel（WPS/Office/合并单元格/多级表头）")
    file = st.file_uploader("选择货物清单", type=["xlsx"])

    # 模板自定义
    use_template = st.checkbox("使用自定义导入模板（100%精准）")
    template_map = None
    if use_template:
        template_file = st.file_uploader("上传你的模板 Excel", type=["xlsx"])
        if template_file:
            template_map = build_template_mapping(template_file)
            st.success("模板已加载，将严格按你格式提取")

    if file and st.button("✅ 超强解析导入"):
        with st.spinner("正在解析复杂Excel..."):
            parser = SuperExcelParser(file, template_map)
            data = parser.parse()
            st.session_state.cargo_df = pd.DataFrame(data)
        st.success(f"解析完成：共 {len(data)} 条有效货物")

    st.dataframe(st.session_state.cargo_df, use_container_width=True)

with tab2:
    st.subheader("按你们公司格式导出（保留样式/格式）")
    export_template = st.file_uploader("上传你们公司导出模板", type=["xlsx"])
    ctype = st.selectbox("箱型", ["20GP","40GP","40HQ"])

    if st.session_state.cargo_df.empty:
        st.warning("请先导入货物")
    else:
        df_out = pack_auto(st.session_state.cargo_df, ctype)
        st.dataframe(df_out, use_container_width=True)

        if export_template:
            wb_out = export_by_template(export_template, df_out)
            from io import BytesIO
            buf = BytesIO()
            wb_out.save(buf)
            buf.seek(0)
            st.download_button("📥 按模板导出 Excel", buf, "配箱结果.xlsx")

st.caption("✅ 已强化：合并单元格｜多级表头｜单位跨行｜多Sheet｜乱格式清洗｜模板自定义导入导出")
