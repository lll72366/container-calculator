import streamlit as st
import csv
import json
import re
import hashlib
import uuid
import sqlite3
from datetime import datetime
from io import StringIO, BytesIO

# ====================== 基础配置 ======================
st.set_page_config(page_title="集装箱配箱系统｜零依赖版", page_icon="📦", layout="wide")
st.title("📦 集装箱智能配箱系统｜零依赖可部署版")

# ====================== 数据库初始化（Python内置sqlite3） ======================
def init_db():
    conn = sqlite3.connect("container_system.db")
    c = conn.cursor()
    # 用户表
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id TEXT PRIMARY KEY, username TEXT UNIQUE, password TEXT, role TEXT)''')
    # 货物数据表
    c.execute('''CREATE TABLE IF NOT EXISTS cargo 
                 (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, length REAL, width REAL, 
                  height REAL, gross_weight REAL, net_weight REAL, container_no TEXT, source TEXT)''')
    # 批次表
    c.execute('''CREATE TABLE IF NOT EXISTS batches 
                 (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, create_time TEXT)''')
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

# ====================== 权限控制（纯Python） ======================
if "user" not in st.session_state:
    st.session_state.user = None
if "cargo_data" not in st.session_state:
    st.session_state.cargo_data = []
if "alloc_result" not in st.session_state:
    st.session_state.alloc_result = {}

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

# ====================== 箱型定义（纯Python） ======================
CONTAINER_SPECS = {
    "20GP": {"long": 5898, "width": 2352, "height": 2393, "max_weight": 28000, "tare": 2200},
    "40GP": {"long": 12032, "width": 2352, "height": 2393, "max_weight": 30480, "tare": 3750},
    "40HQ": {"long": 12032, "width": 2352, "height": 2698, "max_weight": 30480, "tare": 4000},
}

# ====================== 纯Python解析Excel（用csv兼容） ======================
def clean_value(v):
    return str(v).strip().replace("\n", "") if v is not None else ""

def parse_numeric(val, ctx=""):
    s = clean_value(val).lower() + clean_value(ctx).lower()
    nums = re.findall(r"[0-9.]+", s)
    v = float(nums[0]) if nums else 0.0
    # 单位转换
    if "cm" in s: return v * 10
    if "m" in s: return v * 1000
    if "g" in s: return v / 1000
    if "t" in s or "吨" in s: return v * 1000
    return v

def parse_uploaded_file(uploaded_file):
    data = []
    # 处理CSV（Streamlit内置支持）
    if uploaded_file.name.endswith('.csv'):
        stringio = StringIO(uploaded_file.getvalue().decode("utf-8"))
        reader = csv.DictReader(stringio)
        for row in reader:
            name = clean_value(row.get("货物名称", ""))
            if not name: continue
            data.append({
                "货物名称": name,
                "长(mm)": round(parse_numeric(row.get("长(mm)", 0)), 2),
                "宽(mm)": round(parse_numeric(row.get("宽(mm)", 0)), 2),
                "高(mm)": round(parse_numeric(row.get("高(mm)", 0)), 2),
                "毛重(kg)": round(parse_numeric(row.get("毛重(kg)", 0)), 2),
                "净重(kg)": round(parse_numeric(row.get("净重(kg)", 0)), 2),
                "柜号": "",
                "来源": "CSV文件"
            })
    # 处理文本格式（兼容简易Excel导出的文本）
    else:
        try:
            # 读取为文本，按行解析
            content = uploaded_file.getvalue().decode("utf-8", errors="ignore")
            lines = content.split("\n")
            for line in lines[1:]:  # 跳过表头
                if not line.strip(): continue
                parts = line.split(",")
                if len(parts) < 3: continue
                name = clean_value(parts[0])
                if not name: continue
                data.append({
                    "货物名称": name,
                    "长(mm)": round(parse_numeric(parts[1] if len(parts)>1 else 0), 2),
                    "宽(mm)": round(parse_numeric(parts[2] if len(parts)>2 else 0), 2),
                    "高(mm)": round(parse_numeric(parts[3] if len(parts)>3 else 0), 2),
                    "毛重(kg)": round(parse_numeric(parts[4] if len(parts)>4 else 0), 2),
                    "净重(kg)": round(parse_numeric(parts[5] if len(parts)>5 else 0), 2),
                    "柜号": "",
                    "来源": uploaded_file.name
                })
        except:
            st.error("文件格式不支持，请上传CSV文件！")
    return data

# ====================== 纯Python配箱算法 ======================
def container_pack(cargo_list, container_types):
    alloc_result = {}
    cargo_data = cargo_list.copy()
    
    for ctype in container_types:
        spec = CONTAINER_SPECS[ctype]
        max_load = spec["max_weight"] - spec["tare"]
        box_number = 1
        current_load = 0
        
        for idx, cargo in enumerate(cargo_data):
            if cargo["柜号"]: continue  # 已分配的跳过
            
            weight = cargo["毛重(kg)"]
            # 超重则新建箱子
            if current_load + weight > max_load:
                box_number += 1
                current_load = 0
            
            # 分配柜号
            container_code = f"{ctype}{box_number}"
            cargo_data[idx]["柜号"] = container_code
            current_load += weight
            
            # 更新汇总
            if container_code not in alloc_result:
                alloc_result[container_code] = {
                    "箱型": ctype,
                    "总重": 0.0,
                    "总件数": 0
                }
            alloc_result[container_code]["总重"] += weight
            alloc_result[container_code]["总件数"] += 1
    
    return cargo_data, alloc_result

# ====================== 纯Python导出功能 ======================
def export_to_csv(data):
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=["货物名称","长(mm)","宽(mm)","高(mm)","毛重(kg)","净重(kg)","柜号"])
    writer.writeheader()
    writer.writerows(data)
    output.seek(0)
    return output

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
menu = st.sidebar.radio("功能菜单", ["货物导入", "配箱计算", "数据导出", "操作日志"])

# 退出登录
if st.sidebar.button("🚪 退出登录"):
    log_action(st.session_state.user["id"], "用户退出")
    st.session_state.user = None
    st.rerun()

# ---------------------- 1. 货物导入 ----------------------
if menu == "货物导入":
    st.subheader("📥 数据导入（支持CSV格式）")
    st.info("请将Excel另存为CSV格式后上传（避免第三方包依赖）")
    uploaded_file = st.file_uploader("上传CSV文件", type=["csv"])
    
    if uploaded_file and st.button("✅ 解析导入"):
        with st.spinner("解析中..."):
            data = parse_uploaded_file(uploaded_file)
            st.session_state.cargo_data = data
            log_action(st.session_state.user["id"], f"导入{len(data)}条货物数据")
        st.success(f"导入成功！共解析 {len(data)} 条有效货物")
    
    # 显示数据
    if st.session_state.cargo_data:
        st.dataframe(st.session_state.cargo_data, use_container_width=True)
    else:
        st.info("暂无数据，请上传CSV文件")

# ---------------------- 2. 配箱计算 ----------------------
elif menu == "配箱计算":
    st.subheader("🚀 集装箱配箱计算")
    if not st.session_state.cargo_data:
        st.warning("请先导入货物数据！")
    else:
        # 选择箱型
        selected_types = st.multiselect(
            "选择可用箱型",
            list(CONTAINER_SPECS.keys()),
            default=["20GP", "40HQ"]
        )
        
        if st.button("开始配箱计算") and selected_types:
            with st.spinner("配箱计算中..."):
                cargo_data, alloc_result = container_pack(st.session_state.cargo_data, selected_types)
                st.session_state.cargo_data = cargo_data
                st.session_state.alloc_result = alloc_result
                log_action(st.session_state.user["id"], "执行配箱计算")
            st.success("配箱计算完成！")
        
        # 显示配箱结果
        st.subheader("配箱结果")
        st.dataframe(st.session_state.cargo_data, use_container_width=True)
        
        # 显示汇总
        if st.session_state.alloc_result:
            st.subheader("配箱汇总")
            summary_data = [{"柜号":k, **v} for k, v in st.session_state.alloc_result.items()]
            st.dataframe(summary_data, use_container_width=True)

# ---------------------- 3. 数据导出 ----------------------
elif menu == "数据导出":
    st.subheader("📤 数据导出")
    if not st.session_state.cargo_data:
        st.warning("暂无数据可导出！")
    else:
        # 导出配箱结果
        csv_data = export_to_csv(st.session_state.cargo_data)
        st.download_button(
            label="📥 导出配箱结果（CSV）",
            data=csv_data,
            file_name=f"集装箱配箱结果_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv"
        )
        
        # 导出汇总
        if st.session_state.alloc_result:
            summary_csv = export_to_csv([{"柜号":k, **v} for k, v in st.session_state.alloc_result.items()])
            st.download_button(
                label="📥 导出配箱汇总（CSV）",
                data=summary_csv,
                file_name=f"配箱汇总_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )

# ---------------------- 4. 操作日志 ----------------------
elif menu == "操作日志":
    st.subheader("📜 操作日志")
    if st.session_state.user["role"] == "admin":
        conn = sqlite3.connect("container_system.db")
        # 读取日志
        c = conn.cursor()
        c.execute("SELECT * FROM logs ORDER BY create_time DESC")
        logs = []
        for row in c.fetchall():
            logs.append({
                "日志ID": row[0],
                "用户ID": row[1],
                "操作": row[2],
                "时间": row[3]
            })
        conn.close()
        st.dataframe(logs, use_container_width=True)
    else:
        st.warning("⚠️ 仅管理员可查看操作日志！")

# 底部说明
st.caption("✅ 零依赖版：纯Python+Streamlit｜无第三方包｜100%可部署")
