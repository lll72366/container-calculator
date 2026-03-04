import streamlit as st
import pandas as pd
import numpy as np
import re
import openpyxl
import xlrd
import sqlite3
import hashlib
import uuid
from datetime import datetime
from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from io import BytesIO

# ====================== 基础配置 ======================
st.set_page_config(page_title="集装箱配箱系统｜极简版", page_icon="📦", layout="wide")
st.title("📦 集装箱智能配箱系统｜极简可部署版")

# ====================== 数据库初始化 ======================
def init_db():
    conn = sqlite3.connect("container_system.db")
    c = conn.cursor()
    # 用户表
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id TEXT PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT)''')
    # 批次表
    c.execute('''CREATE TABLE IF NOT EXISTS batches 
                 (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, data TEXT, create_time TEXT)''')
    # 日志表
    c.execute('''CREATE TABLE IF NOT EXISTS logs 
                 (id TEXT PRIMARY KEY, user_id TEXT, action TEXT, create_time TEXT)''')
    # 默认管理员
    c.execute("SELECT * FROM users WHERE username='admin'")
    if not c.fetchone():
        c.execute("INSERT INTO users VALUES (?,?,?,?)", 
                  (str(uuid.uuid4()), "admin", hashlib.md5("admin123".encode()).hexdigest(), "admin"))
    conn.commit()
    conn.close()

init_db()

# ====================== 权限控制 ======================
if "user" not in st.session_state:
    st.session_state.user = None

def login(username, password):
    conn = sqlite3.connect("container_system.db")
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username=? AND password=?", 
              (username, hashlib.md5(password.encode()).hexdigest()))
    user = c.fetchone()
    conn.close()
    return user

def log_action(user_id, action):
    conn = sqlite3.connect("container_system.db")
    c = conn.cursor()
    c.execute("INSERT INTO logs VALUES (?,?,?,?)", 
              (str(uuid.uuid4()), user_id, action, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

# ====================== 箱型定义 ======================
CONTAINER_SPECS = {
    "20GP": {"long": 5898, "width": 2352, "height": 2393, "max_weight": 28000, "tare": 2200},
    "40GP": {"long": 12032, "width": 2352, "height": 2393, "max_weight": 30480, "tare": 3750},
    "40HQ": {"long": 12032, "width": 2352, "height": 2698, "max_weight": 30480, "tare": 4000},
}

# ====================== 全局状态 ======================
if "cargo_df" not in st.session_state:
    st.session_state.cargo_df = pd.DataFrame(columns=["货物名称","长(mm)","宽(mm)","高(mm)","毛重(kg)","净重(kg)","柜号","来源"])
if "alloc_result" not in st.session_state:
    st.session_state.alloc_result = {}

# ====================== Excel解析引擎（仅保留核心） ======================
class SimpleExcelParser:
    def __init__(self, file, filename):
        self.file = file
        self.filename = filename
        self.items = []

    def clean(self, v):
        return str(v).strip().replace("\n","") if v is not None else ""

    def parse_val(self, val, ctx=""):
        s = self.clean(val).lower() + self.clean(ctx).lower()
        nums = re.findall(r"[0-9.]+", s)
        v = float(nums[0]) if nums else 0.0
        if "cm" in s: return v*10
        if "m" in s: return v*1000
        if "g" in s: return v/1000
        if "t" in s or "吨" in s: return v*1000
        return v

    def parse_xlsx(self):
        try:
            wb = load_workbook(self.file, data_only=True)
            for sn in wb.sheetnames:
                ws = wb[sn]
                for r in range(1, min(ws.max_row+1, 2000)):
                    name = self.clean(ws.cell(r,1).value)
                    if not name: continue
                    self.items.append({
                        "货物名称": name,
                        "长(mm)": round(self.parse_val(ws.cell(r,2).value),2),
                        "宽(mm)": round(self.parse_val(ws.cell(r,3).value),2),
                        "高(mm)": round(self.parse_val(ws.cell(r,4).value),2),
                        "毛重(kg)": round(self.parse_val(ws.cell(r,5).value),2),
                        "净重(kg)": round(self.parse_val(ws.cell(r,6).value),2),
                        "柜号": "", "来源": f"{sn}行{r}"
                    })
            return True
        except:
            return False

    def parse_xls(self):
        try:
            wb = xlrd.open_workbook(file_contents=self.file.read())
            for s in wb.sheets():
                for r in range(min(s.nrows, 2000)):
                    row = s.row_values(r)
                    if len(row)<3: continue
                    name = self.clean(row[0])
                    if not name: continue
                    self.items.append({
                        "货物名称": name,
                        "长(mm)": round(self.parse_val(row[1] if len(row)>1 else 0),2),
                        "宽(mm)": round(self.parse_val(row[2] if len(row)>2 else 0),2),
                        "高(mm)": round(self.parse_val(row[3] if len(row)>3 else 0),2),
                        "毛重(kg)": round(self.parse_val(row[4] if len(row)>4 else 0),2),
                        "净重(kg)": round(self.parse_val(row[5] if len(row)>5 else 0),2),
                        "柜号": "", "来源": s.name
                    })
            return True
        except:
            return False

    def parse(self):
        if self.filename.endswith(".xlsx"): self.parse_xlsx()
        elif self.filename.endswith(".xls"): self.parse_xls()
        return self.items

# ====================== 配箱计算（仅保留核心） ======================
def simple_pack(df, types):
    res = {}
    df = df.copy()
    df["柜号"] = ""
    
    for container_type in types:
        spec = CONTAINER_SPECS[container_type]
        max_w = spec["max_weight"] - spec["tare"]
        box_no = 1
        used = 0
        for i, row in df.iterrows():
            if df.at[i, "柜号"]: continue
            w = row["毛重(kg)"]
            if used + w > max_w:
                box_no +=1
                used =0
            code = f"{container_type}{box_no}"
            df.at[i, "柜号"] = code
            used +=w
            if code not in res:
                res[code] = {"箱型":container_type, "总重":0, "总件":0}
            res[code]["总重"] +=w
            res[code]["总件"] +=1
    
    return df, res

# ====================== Excel导出（仅保留核心） ======================
def export_excel(df):
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="配箱结果", index=False)
    buffer.seek(0)
    return buffer

# ====================== 登录界面 ======================
if not st.session_state.user:
    with st.form("login_form"):
        st.subheader("🔐 系统登录")
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        if st.form_submit_button("登录"):
            user = login(username, password)
            if user:
                st.session_state.user = {
                    "id": user[0],
                    "name": user[1],
                    "role": user[3]
                }
                log_action(user[0], "用户登录")
                st.success("登录成功！")
                st.rerun()
            else:
                st.error("用户名或密码错误！")
    st.stop()

# ====================== 主界面 ======================
st.sidebar.title(f"欢迎 {st.session_state.user['name']}")
menu = st.sidebar.radio("功能菜单", ["货物导入", "配箱计算", "批次管理", "操作日志"])

# 退出登录
if st.sidebar.button("🚪 退出登录"):
    log_action(st.session_state.user["id"], "用户退出")
    st.session_state.user = None
    st.rerun()

# ---------------------- 货物导入 ----------------------
if menu == "货物导入":
    st.subheader("📥 Excel导入（支持.xls/.xlsx，微软/WPS兼容）")
    uploaded_file = st.file_uploader("上传货物清单", type=["xls", "xlsx"])
    
    if uploaded_file and st.button("✅ 解析导入"):
        with st.spinner("解析中..."):
            parser = SimpleExcelParser(uploaded_file, uploaded_file.name)
            data = parser.parse()
            st.session_state.cargo_df = pd.DataFrame(data)
            log_action(st.session_state.user["id"], f"导入{len(data)}条货物数据")
        st.success(f"导入成功！共解析 {len(data)} 条有效货物")
    
    st.dataframe(st.session_state.cargo_df, use_container_width=True)

# ---------------------- 配箱计算 ----------------------
elif menu == "配箱计算":
    st.subheader("🚀 多箱型配箱计算")
    if st.session_state.cargo_df.empty:
        st.warning("请先导入货物数据！")
    else:
        selected_types = st.multiselect(
            "选择可用箱型", 
            list(CONTAINER_SPECS.keys()), 
            default=["20GP", "40HQ"]
        )
        if st.button("开始配箱计算"):
            with st.spinner("配箱计算中..."):
                df_out, alloc_result = simple_pack(st.session_state.cargo_df, selected_types)
                st.session_state.cargo_df = df_out
                st.session_state.alloc_result = alloc_result
                log_action(st.session_state.user["id"], "执行配箱计算")
            st.success("配箱计算完成！")
        
        # 显示配箱结果
        st.dataframe(st.session_state.cargo_df, use_container_width=True)
        
        # 导出Excel
        if not st.session_state.cargo_df.empty:
            excel_buffer = export_excel(st.session_state.cargo_df)
            st.download_button(
                "📥 导出配箱结果",
                excel_buffer,
                f"配箱结果_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        
        # 配箱汇总
        if st.session_state.alloc_result:
            st.subheader("配箱汇总")
            summary_df = pd.DataFrame([
                {"柜号":k, **v} for k, v in st.session_state.alloc_result.items()
            ])
            st.dataframe(summary_df, use_container_width=True)

# ---------------------- 批次管理 ----------------------
elif menu == "批次管理":
    st.subheader("📊 配箱批次管理")
    batch_name = st.text_input("输入批次名称")
    if batch_name and st.button("💾 保存当前批次"):
        conn = sqlite3.connect("container_system.db")
        c = conn.cursor()
        c.execute("INSERT INTO batches VALUES (?,?,?,?,?)", 
                  (str(uuid.uuid4()), 
                   st.session_state.user["id"],
                   batch_name,
                   st.session_state.cargo_df.to_json(),
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        log_action(st.session_state.user["id"], f"保存批次：{batch_name}")
        st.success(f"批次「{batch_name}」保存成功！")
    
    # 显示历史批次
    conn = sqlite3.connect("container_system.db")
    batch_df = pd.read_sql(
        "SELECT * FROM batches WHERE user_id=? ORDER BY create_time DESC",
        conn,
        params=(st.session_state.user["id"],)
    )
    conn.close()
    st.dataframe(batch_df[["id", "name", "create_time"]], use_container_width=True)

# ---------------------- 操作日志 ----------------------
elif menu == "操作日志":
    st.subheader("📜 操作日志")
    if st.session_state.user["role"] == "admin":
        conn = sqlite3.connect("container_system.db")
        log_df = pd.read_sql("SELECT * FROM logs ORDER BY create_time DESC", conn)
        conn.close()
        st.dataframe(log_df, use_container_width=True)
    else:
        st.warning("⚠️ 仅管理员可查看操作日志！")

st.caption("✅ 极简版：Excel全兼容｜配箱计算｜数据库存储｜权限控制")
