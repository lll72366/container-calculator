import streamlit as st

# 密码验证
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    
    if not st.session_state["password_correct"]:
        password = st.text_input("请输入访问密码", type="password")
        if password == "123456":  # 替换成你想设置的密码
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("密码错误，请联系管理员")
            return False
    return True

if not check_password():
    st.stop()

import streamlit as st
import pandas as pd
import numpy as np
import io
import hashlib
from datetime import datetime

# ====================== 页面配置 ======================
st.set_page_config(
    page_title="国际货代配箱系统",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ====================== 管理员登录配置 ======================
ADMIN_USER = "admin"  # 可自定义账号
ADMIN_PWD_HASH = hashlib.md5(b"admin123".encode()).hexdigest()  # 密码是 admin123，可修改

# ====================== 集装箱规格（支持手动选择） ======================
CONTAINER_SPECS = {
    "20GP": {"长":5898,"宽":2352,"高":2393,"限重":21000,"体积":33.2},
    "40GP": {"长":12032,"宽":2352,"高":2393,"限重":26500,"体积":67.7},
    "40HQ": {"长":12032,"宽":2352,"高":2695,"限重":26000,"体积":76.2},
    "45HQ": {"长":13556,"宽":2352,"高":2695,"限重":27500,"体积":86.8},
}

# ====================== 单位转换配置 ======================
UNIT_DIM = {"mm":1, "cm":10, "m":1000, "英寸":25.4, "英尺":304.8}
UNIT_WT = {"kg":1, "g":0.001, "吨":1000, "lb":0.453592}

# ====================== 初始化会话状态 ======================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "cargo" not in st.session_state:
    st.session_state.cargo = pd.DataFrame(columns=[
        "货物名称","长(mm)","宽(mm)","高(mm)","毛重(kg)","净重(kg)","柜号","备注"
    ])
if "loading_result" not in st.session_state:
    st.session_state.loading_result = []
if "marking" not in st.session_state:
    st.session_state.marking = None
if "batches" not in st.session_state:
    st.session_state.batches = {}
if "selected_container" not in st.session_state:
    st.session_state.selected_container = "40HQ"  # 默认柜型

# ====================== 登录页面 ======================
def login_page():
    st.title("🔐 管理员登录")
    username = st.text_input("账号", placeholder="默认：admin")
    password = st.text_input("密码", type="password", placeholder="默认：admin123")
    
    if st.button("登录"):
        # 验证账号密码
        if username == ADMIN_USER and hashlib.md5(password.encode()).hexdigest() == ADMIN_PWD_HASH:
            st.session_state.logged_in = True
            st.success("✅ 登录成功！")
            st.rerun()
        else:
            st.error("❌ 账号或密码错误，请重试")

# ====================== A4打印模板（装箱单） ======================
def print_template():
    st.subheader("🖨️ A4装箱单打印模板")
    cargo_df = st.session_state.cargo
    
    if cargo_df.empty:
        st.warning("⚠️ 暂无货物数据，无法生成打印模板")
        return
    
    # 按柜号分组展示打印模板
    container_nums = cargo_df["柜号"].unique()
    for container_num in container_nums:
        if container_num and container_num != "":
            with st.expander(f"📦 柜号：{container_num}（点击展开打印）"):
                # 筛选该柜号的货物
                container_cargo = cargo_df[cargo_df["柜号"] == container_num].copy()
                # 打印模板格式（适配A4）
                st.markdown(f"""
### 集装箱装箱单
**柜号**：{container_num} | **生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}
---
""", unsafe_allow_html=True)
                
                # 展示货物清单（适合打印）
                st.dataframe(container_cargo, use_container_width=True, hide_index=True)
                
                # 打印提示
                st.info("💡 按键盘 Ctrl+P 即可直接打印此页，适配A4纸尺寸")

# ====================== 配箱算法（支持手动选柜） ======================
def pack_cargo(cargo_df, container_type, start_num=1):
    df = cargo_df.copy()
    # 计算货物体积（m³）
    df["体积"] = (df["长(mm)"] * df["宽(mm)"] * df["高(mm)"]) / 1e9
    # 获取选中柜型的规格
    spec = CONTAINER_SPECS[container_type]
    max_volume = spec["体积"] * 0.96  # 预留4%空间
    max_weight = spec["限重"] * 0.96   # 预留4%重量
    
    loading_result = []
    current_cargo = []
    used_volume = 0.0
    used_weight = 0.0
    container_no = start_num
    
    # 按体积从大到小装箱（优化空间利用率）
    for idx, row in df.iterrows():
        cargo_volume = row["体积"]
        cargo_weight = row["毛重(kg)"]
        
        # 超出当前柜容量则新建柜子
        if used_volume + cargo_volume > max_volume or used_weight + cargo_weight > max_weight:
            loading_result.append({
                "柜号": f"{container_type}-{container_no:02d}",
                "柜型": container_type,
                "件数": len(current_cargo)
            })
            # 重置当前柜
            current_cargo = []
            used_volume = 0.0
            used_weight = 0.0
            container_no += 1
        
        current_cargo.append(idx)
        used_volume += cargo_volume
        used_weight += cargo_weight
    
    # 处理最后一个柜子
    if current_cargo:
        loading_result.append({
            "柜号": f"{container_type}-{container_no:02d}",
            "柜型": container_type,
            "件数": len(current_cargo)
        })
    
    # 给货物分配柜号
    for plan in loading_result:
        for idx in current_cargo:
            df.at[idx, "柜号"] = plan["柜号"]
    
    return df, loading_result

# ====================== 主程序 ======================
if not st.session_state.logged_in:
    # 未登录时显示登录页
    login_page()
else:
    # 已登录显示主功能
    st.title("📦 国际货代集装箱配箱测算系统")
    
    # 侧边栏菜单
    menu = st.sidebar.radio("功能菜单", [
        "货物清单管理", "智能配箱计算", "唛头生成", 
        "数据导出", "打印模板", "我的配货批次", "退出登录"
    ])
    
    # 退出登录
    if menu == "退出登录":
        st.session_state.logged_in = False
        st.success("✅ 已退出登录")
        st.rerun()
    
    # ---------------------- 1. 货物清单管理 ----------------------
    if menu == "货物清单管理":
        st.subheader("✏️ 货物清单管理")
        
        # 手动添加货物
        with st.expander("📝 手动添加货物", expanded=True):
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            with col1:
                cargo_name = st.text_input("货物名称", placeholder="如：电子产品")
            with col2:
                length = st.number_input("长度", min_value=0.0, step=0.1, help="单位将自动转换为mm")
            with col3:
                width = st.number_input("宽度", min_value=0.0, step=0.1, help="单位将自动转换为mm")
            with col4:
                height = st.number_input("高度", min_value=0.0, step=0.1, help="单位将自动转换为mm")
            with col5:
                gross_weight = st.number_input("毛重", min_value=0.0, step=0.1, help="单位将自动转换为kg")
            with col6:
                net_weight = st.number_input("净重", min_value=0.0, step=0.1, help="单位将自动转换为kg")
            
            # 添加按钮
            if st.button("➕ 添加到清单") and cargo_name:
                new_row = pd.DataFrame([[
                    cargo_name, length, width, height, 
                    gross_weight, net_weight, "", "手动添加"
                ]], columns=st.session_state.cargo.columns)
                st.session_state.cargo = pd.concat([st.session_state.cargo, new_row], ignore_index=True)
                st.success(f"✅ 已添加：{cargo_name}")
        
        # Excel导入
        with st.expander("📤 Excel批量导入", expanded=False):
            uploaded_file = st.file_uploader("上传货物清单Excel", type=["xlsx"])
            if uploaded_file:
                st.info("🔍 正在智能解析Excel文件（支持多sheet、多表头）")
                try:
                    # 智能读取多sheet、多表头
                    dfs = []
                    excel_file = pd.ExcelFile(uploaded_file)
                    for sheet_name in excel_file.sheet_names:
                        # 尝试前4行作为表头
                        for header_row in range(4):
                            try:
                                df = pd.read_excel(uploaded_file, sheet_name=sheet_name, header=header_row)
                                dfs.append(df)
                            except:
                                continue
                    
                    # 提取货物数据
                    imported_rows = []
                    for df in dfs:
                        df = df.fillna("")
                        for _, row in df.iterrows():
                            # 自动识别列（品名、长、宽、高、毛重、净重）
                            name = ""
                            L = W = H = G = N = 0.0
                            for col_idx, col_name in enumerate(df.columns):
                                col_text = str(col_name).lower()
                                cell_value = row[col_idx]
                                # 识别货物名称
                                if any(key in col_text for key in ["品名", "名称", "货物", "货名"]) and not name:
                                    name = str(cell_value).strip()
                                # 识别尺寸
                                elif any(key in col_text for key in ["长", "length"]) and L == 0:
                                    L = float(cell_value) if str(cell_value).replace(".","").isdigit() else 0.0
                                elif any(key in col_text for key in ["宽", "width"]) and W == 0:
                                    W = float(cell_value) if str(cell_value).replace(".","").isdigit() else 0.0
                                elif any(key in col_text for key in ["高", "height"]) and H == 0:
                                    H = float(cell_value) if str(cell_value).replace(".","").isdigit() else 0.0
                                # 识别重量
                                elif any(key in col_text for key in ["毛重", "gw"]) and G == 0:
                                    G = float(cell_value) if str(cell_value).replace(".","").isdigit() else 0.0
                                elif any(key in col_text for key in ["净重", "nw"]) and N == 0:
                                    N = float(cell_value) if str(cell_value).replace(".","").isdigit() else 0.0
                            
                            # 有效数据才导入
                            if name and (L > 0 or W > 0 or H > 0):
                                imported_rows.append([
                                    name, L, W, H, G, N, "", f"导入自：{uploaded_file.name}"
                                ])
                    
                    # 更新清单
                    if imported_rows:
                        import_df = pd.DataFrame(imported_rows, columns=st.session_state.cargo.columns)
                        st.session_state.cargo = import_df
                        st.success(f"✅ 导入成功！共 {len(imported_rows)} 条货物数据")
                    else:
                        st.error("❌ 未识别到有效货物数据，请检查Excel格式")
                except Exception as e:
                    st.error(f"❌ 导入失败：{str(e)}")
        
        # 展示当前清单
        st.subheader("📋 当前货物清单")
        if not st.session_state.cargo.empty:
            st.dataframe(st.session_state.cargo, use_container_width=True, hide_index=True)
            
            # 清单操作
            col1, col2 = st.columns(2)
            with col1:
                if st.button("🗑️ 清空清单"):
                    st.session_state.cargo = pd.DataFrame(columns=st.session_state.cargo.columns)
                    st.success("✅ 清单已清空")
            with col2:
                if st.button("🔄 刷新清单"):
                    st.rerun()
        else:
            st.info("📭 暂无货物数据，请添加或导入货物清单")
    
    # ---------------------- 2. 智能配箱计算（手动选柜） ----------------------
    elif menu == "智能配箱计算":
        st.subheader("🧮 智能配箱计算")
        
        # 选择柜型
        st.session_state.selected_container = st.selectbox(
            "📦 选择集装箱类型",
            options=list(CONTAINER_SPECS.keys()),
            index=list(CONTAINER_SPECS.keys()).index(st.session_state.selected_container),
            help="手动选择需要的柜型，系统将按该柜型计算配箱"
        )
        
        # 显示选中柜型的规格
        selected_spec = CONTAINER_SPECS[st.session_state.selected_container]
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("柜型", st.session_state.selected_container)
        with col2:
            st.metric("最大体积(m³)", round(selected_spec["体积"], 1))
        with col3:
            st.metric("最大载重(kg)", selected_spec["限重"])
        with col4:
            st.metric("尺寸(mm)", f"{selected_spec['长']}×{selected_spec['宽']}×{selected_spec['高']}")
        
        # 配箱计算
        if st.session_state.cargo.empty:
            st.warning("⚠️ 请先在「货物清单管理」添加/导入货物")
        else:
            # 货物统计
            total_items = len(st.session_state.cargo)
            total_weight = st.session_state.cargo["毛重(kg)"].sum()
            total_volume = (st.session_state.cargo["长(mm)"] * st.session_state.cargo["宽(mm)"] * st.session_state.cargo["高(mm)"]).sum() / 1e9
            
            st.divider()
            st.subheader("📊 货物统计")
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("总货物件数", total_items)
            with col2:
                st.metric("总毛重(kg)", round(total_weight, 2))
            with col3:
                st.metric("总体积(m³)", round(total_volume, 3))
            
            # 开始配箱
            if st.button("🚀 开始配箱计算", type="primary"):
                with st.spinner("🔢 正在计算最优配箱方案..."):
                    # 执行配箱算法
                    new_cargo, loading_result = pack_cargo(
                        st.session_state.cargo,
                        st.session_state.selected_container,
                        start_num=1
                    )
                    # 更新会话状态
                    st.session_state.cargo = new_cargo
                    st.session_state.loading_result = loading_result
                    st.success("✅ 配箱计算完成！")
            
            # 展示配箱结果
            if st.session_state.loading_result:
                st.divider()
                st.subheader("📦 配箱结果")
                st.dataframe(
                    pd.DataFrame(st.session_state.loading_result),
                    use_container_width=True,
                    hide_index=True
                )
                
                # 按柜号展示货物
                st.subheader("📋 按柜号分组货物")
                for plan in st.session_state.loading_result:
                    container_num = plan["柜号"]
                    with st.expander(f"柜号：{container_num}"):
                        container_cargo = st.session_state.cargo[st.session_state.cargo["柜号"] == container_num]
                        st.dataframe(container_cargo, use_container_width=True, hide_index=True)
    
    # ---------------------- 3. 唛头生成 ----------------------
    elif menu == "唛头生成":
        st.subheader("📝 唛头生成")
        
        if st.session_state.cargo.empty or all(st.session_state.cargo["柜号"] == ""):
            st.warning("⚠️ 请先完成配箱计算，再生成唛头")
        else:
            # 补充唛头信息
            st.subheader("🔍 补充唛头信息")
            col1, col2, col3 = st.columns(3)
            with col1:
                consignee = st.text_input("收货人", placeholder="输入收货人名称")
            with col2:
                destination_port = st.text_input("目的港", placeholder="输入目的港名称")
            with col3:
                origin_country = st.text_input("原产国", placeholder="输入原产国")
            
            # 生成唛头
            if st.button("🎯 生成唛头", type="primary"):
                marking_df = st.session_state.cargo.copy()
                # 添加唛头字段
                marking_df["收货人"] = consignee
                marking_df["目的港"] = destination_port
                marking_df["原产国"] = origin_country
                marking_df["箱序号"] = range(1, len(marking_df)+1)
                marking_df["总箱数"] = len(marking_df)
                # 保存到会话状态
                st.session_state.marking = marking_df
                st.success("✅ 唛头生成成功！")
                
                # 展示唛头
                st.subheader("📋 唛头信息")
                st.dataframe(marking_df, use_container_width=True, hide_index=True)
    
    # ---------------------- 4. 数据导出 ----------------------
    elif menu == "数据导出":
        st.subheader("📤 数据导出")
        
        # 导出货物清单
        if not st.session_state.cargo.empty:
            st.subheader("📦 导出货物清单")
            # 准备导出数据（添加柜号）
            export_cargo = st.session_state.cargo.copy()
            # 导出Excel
            cargo_buffer = io.BytesIO()
            with pd.ExcelWriter(cargo_buffer, engine="openpyxl") as writer:
                export_cargo.to_excel(writer, sheet_name="货物清单", index=False)
            st.download_button(
                label="📥 导出Excel格式",
                data=cargo_buffer,
                file_name=f"货物清单_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        
        # 导出唛头
        if st.session_state.marking is not None:
            st.subheader("📝 导出唛头")
            marking_buffer = io.BytesIO()
            with pd.ExcelWriter(marking_buffer, engine="openpyxl") as writer:
                st.session_state.marking.to_excel(writer, sheet_name="唛头信息", index=False)
            st.download_button(
                label="📥 导出Excel格式",
                data=marking_buffer,
                file_name=f"唛头_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        
        if st.session_state.cargo.empty and st.session_state.marking is None:
            st.warning("⚠️ 暂无可导出的数据，请先添加货物并完成配箱")
    
    # ---------------------- 5. 打印模板 ----------------------
    elif menu == "打印模板":
        print_template()
    
    # ---------------------- 6. 我的配货批次 ----------------------
    elif menu == "我的配货批次":
        st.subheader("📋 我的配货批次")
        
        # 保存当前批次
        batch_id = st.text_input("批次编号", value=f"B{datetime.now().strftime('%Y%m%d%H%M%S')}")
        if st.button("💾 保存当前批次") and not st.session_state.cargo.empty:
            st.session_state.batches[batch_id] = {
                "create_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "cargo_list": st.session_state.cargo.copy(),
                "loading_plan": st.session_state.loading_result.copy(),
                "status": "已保存"
            }
            st.success(f"✅ 批次 {batch_id} 保存成功！")
        
        # 展示所有批次
        if st.session_state.batches:
            st.subheader("📜 已保存的批次")
            selected_batch = st.selectbox(
                "选择批次查看",
                options=list(st.session_state.batches.keys()),
                format_func=lambda x: f"{x}（创建时间：{st.session_state.batches[x]['create_time']}）"
            )
            
            # 展示选中批次信息
            if selected_batch:
                batch_data = st.session_state.batches[selected_batch]
                st.subheader(f"批次详情：{selected_batch}")
                
                # 批次信息
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("创建时间", batch_data["create_time"])
                with col2:
                    st.metric("货物件数", len(batch_data["cargo_list"]))
                with col3:
                    st.metric("状态", batch_data["status"])
                
                # 加载批次
                if st.button("🔄 加载此批次"):
                    st.session_state.cargo = batch_data["cargo_list"].copy()
                    st.session_state.loading_result = batch_data["loading_plan"].copy()
                    st.success(f"✅ 已加载批次 {selected_batch}")
                
                # 展示批次配箱结果
                if batch_data["loading_plan"]:
                    st.subheader("📦 配箱方案")
                    st.dataframe(
                        pd.DataFrame(batch_data["loading_plan"]),
                        use_container_width=True,
                        hide_index=True
                    )
                
                # 展示批次货物清单
                st.subheader("📋 货物清单")
                st.dataframe(batch_data["cargo_list"], use_container_width=True, hide_index=True)
        else:
            st.info("📭 暂无保存的批次，请先保存当前配货数据")

# ====================== 页脚 ======================
st.divider()
st.caption("© 2025 国际货代集装箱配箱测算系统 - 专为发货/货代人员设计")
