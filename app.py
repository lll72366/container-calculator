import streamlit as st
import pandas as pd
import numpy as np
import re
import openpyxl
from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
import warnings
warnings.filterwarnings("ignore")

st.set_page_config(page_title="集装箱配箱｜微软+WPS超强兼容", page_icon="📦", layout="wide")

# --------------------------
# 箱型标准
# --------------------------
CONTAINER_SPECS = {
    "20GP": {"long": 5898, "width": 2352, "height": 2393, "max_weight": 28000, "tare": 2200},
    "40GP": {"long": 12032, "width": 2352, "height": 2393, "max_weight": 30480, "tare": 3750},
    "40HQ": {"long": 12032, "width": 2352, "height": 2698, "max_weight": 30480, "tare": 4000},
}

# --------------------------
# 全局状态
# --------------------------
if "cargo_df" not in st.session_state:
    st.session_state.cargo_df = pd.DataFrame(columns=[
        "货物名称", "长(mm)", "宽(mm)", "高(mm)", "毛重(kg)", "净重(kg)", "柜号", "来源"
    ])

# ==========================================================
#
# 【双引擎超级解析器】
# 引擎A：openpyxl → 微软Excel / WPS 结构级读取（合并单元格、公式）
# 引擎B：pandas → 兼容兜底（乱码、加密、格式异常）
#
# ==========================================================
class UniversalExcelParser:
    def __init__(self, file):
        self.file = file
        self.items = []

    # --------------------------
    # 读取合并单元格（微软 + WPS）
    # --------------------------
    def get_cell_value(self, ws, r, c):
        cell = ws.cell(row=r, column=c)
        if isinstance(cell, MergedCell):
            for mrange in ws.merged_cells.ranges:
                if mrange.min_row <= r <= mrange.max_row and mrange.min_col <= c <= mrange.max_col:
                    return ws.cell(row=mrange.min_row, column=mrange.min_col).value
        return cell.value

    def clean(self, v):
        if v is None:
            return ""
        return str(v).strip().replace("\n", "").replace("\r", "")

    # --------------------------
    # 智能识别表头（兼容两边软件）
    # --------------------------
    def detect_header(self, ws):
        keywords = {
            "name":   ["名称", "货名", "品名", "货物", "描述", "产品"],
            "long":   ["长", "长度", "长*宽", "长x宽"],
            "width":  ["宽", "宽度"],
            "height": ["高", "高度"],
            "gw":     ["毛重", "GW", "Gross"],
            "nw":     ["净重", "NW", "Net"]
        }
        best_row = 1
        field_col = {}
        max_score = 0

        for r in range(1, min(31, ws.max_row + 1)):
            s = ""
            for c in range(1, min(51, ws.max_column + 1)):
                s += self.clean(self.get_cell_value(ws, r, c)) + " "

            score = 0
            for klist in keywords.values():
                for k in klist:
                    if k.lower() in s.lower():
                        score += 1
            if score > max_score:
                max_score = score
                best_row = r

        for c in range(1, ws.max_column + 1):
            val = self.clean(self.get_cell_value(ws, best_row, c)).lower()
            for field, keys in keywords.items():
                if any(k.lower() in val for k in keys):
                    field_col[field] = c

        return best_row, field_col

    # --------------------------
    # 单位解析（数字+文字混合）
    # --------------------------
    def parse_num(self, val, unit_ctx=""):
        s = self.clean(val).lower() + self.clean(unit_ctx).lower()
        nums = re.findall(r"[0-9.]+", s)
        if not nums:
            return 0.0
        v = float(nums[0])

        if "cm" in s: return v * 10
        if "m" in s: return v * 1000
        if "g" in s: return v / 1000
        if "t" in s or "吨" in s: return v * 1000
        return v

    # --------------------------
    # 引擎A：结构解析（微软 + WPS 专业）
    # --------------------------
    def engine_structured(self):
        try:
            wb = load_workbook(self.file, data_only=True)
            for sheet in wb.sheetnames:
                ws = wb[sheet]
                hr, fc = self.detect_header(ws)
                unit_ctx = {}

                for dr in [hr-1, hr, hr+1]:
                    if 1 <= dr <= ws.max_row:
                        for f, c in fc.items():
                            u = self.clean(self.get_cell_value(ws, dr, c)).lower()
                            if any(x in u for x in ["mm","cm","m","kg","g","t"]):
                                unit_ctx[f] = u

                for r in range(hr+1, ws.max_row+1):
                    name = self.clean(self.get_cell_value(ws, r, fc.get("name", 0)))
                    long = self.parse_num(self.get_cell_value(ws, r, fc.get("long", 0)), unit_ctx.get("long",""))
                    width = self.parse_num(self.get_cell_value(ws, r, fc.get("width", 0)), unit_ctx.get("width",""))
                    height = self.parse_num(self.get_cell_value(ws, r, fc.get("height", 0)), unit_ctx.get("height",""))
                    gw = self.parse_num(self.get_cell_value(ws, r, fc.get("gw", 0)), unit_ctx.get("gw",""))
                    nw = self.parse_num(self.get_cell_value(ws, r, fc.get("nw", 0)), unit_ctx.get("nw",""))

                    if not name and gw < 0.1:
                        continue
                    if long < 5 or width <5 or height <5:
                        continue

                    self.items.append({
                        "货物名称": name[:100],
                        "长(mm)": round(long,2),
                        "宽(mm)": round(width,2),
                        "高(mm)": round(height,2),
                        "毛重(kg)": round(gw,2),
                        "净重(kg)": round(nw,2),
                        "柜号": "",
                        "来源": f"{sheet} 行{r}"
                    })
            wb.close()
            return True
        except Exception as e:
            return False

    # --------------------------
    # 引擎B：兼容兜底（专治格式怪异）
    # --------------------------
    def engine_fallback(self):
        try:
            xl = pd.ExcelFile(self.file)
            for sheet in xl.sheet_names:
                df = pd.read_excel(self.file, sheet_name=sheet, header=None)
                df = df.dropna(how="all").dropna(how="all", axis=1)
                for _, row in df.iterrows():
                    row_str = " ".join([str(x) for x in row.fillna("").values]).lower()
                    if any(k in row_str for k in ["名称","长","宽","高","毛重"]):
                        continue
                    if len(row) >=5:
                        self.items.append({
                            "货物名称": str(row.iloc[0])[:100],
                            "长(mm)": self.parse_num(row.iloc[1] if len(row)>=2 else 0),
                            "宽(mm)": self.parse_num(row.iloc[2] if len(row)>=3 else 0),
                            "高(mm)": self.parse_num(row.iloc[3] if len(row)>=4 else 0),
                            "毛重(kg)": self.parse_num(row.iloc[4] if len(row)>=5 else 0),
                            "净重(kg)": self.parse_num(row.iloc[5] if len(row)>=6 else 0),
                            "柜号": "",
                            "来源": f"{sheet}(兼容模式)"
                        })
            return True
        except:
            return False

    # --------------------------
    # 最终解析：双引擎自动切换
    # --------------------------
    def parse(self):
        success = self.engine_structured()
        if not success or len(self.items) == 0:
            self.engine_fallback()
        return self.items

# ==========================================================
# 导出：同时兼容微软Excel / WPS 打开不乱码
# ==========================================================
def export_excel_cross(df):
    from io import BytesIO
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name="配箱清单", index=False)
    output.seek(0)
    return output

# ==========================================================
# 配箱
# ==========================================================
def allocate(df, ctype):
    df = df.copy()
    spec = CONTAINER_SPECS[ctype]
    max_load = spec["max_weight"] - spec["tare"]
    box = 1
    used = 0
    for i, row in df.iterrows():
        w = row["毛重(kg)"]
        if used + w > max_load:
            box +=1
            used =0
        df.at[i,"柜号"] = f"{ctype}{box}"
        used +=w
    return df

# ==========================================================
# UI
# ==========================================================
st.title("📦 集装箱配箱｜**微软 Excel + WPS 双兼容超强版**")

tab1, tab2 = st.tabs(["📥 导入Excel（双引擎）", "📤 导出Excel（双兼容）"])

with tab1:
    st.subheader("上传任意 Excel：微软 / WPS 都能稳定读")
    file = st.file_uploader("选择货物清单 .xlsx", type=["xlsx"])

    if file and st.button("✅ 超强解析导入"):
        with st.spinner("双引擎解析中..."):
            parser = UniversalExcelParser(file)
            data = parser.parse()
            st.session_state.cargo_df = pd.DataFrame(data)
        st.success(f"解析完成：{len(data)} 条货物｜引擎：结构+兼容双保险")

    st.dataframe(st.session_state.cargo_df, use_container_width=True)

with tab2:
    st.subheader("导出可在 微软Excel / WPS 完美打开")
    ctype = st.selectbox("选择箱型", ["20GP","40GP","40HQ"])

    if not st.session_state.cargo_df.empty:
        df_out = allocate(st.session_state.cargo_df, ctype)
        st.dataframe(df_out, use_container_width=True)

        file_export = export_excel_cross(df_out)
        st.download_button(
            "📥 导出 Excel（微软/WPS通用）",
            file_export,
            "集装箱配箱清单.xlsx"
        )

st.caption("✅ 双引擎解析｜微软Excel / WPS Excel 深度兼容｜合并单元格｜多级表头｜单位跨行｜多Sheet")
