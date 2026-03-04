import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
import uuid
import qrcode
import io
from PIL import Image
import copy

# 页面配置
st.set_page_config(
    page_title="集装箱配箱测算系统",
    page_icon="📦",
    layout="wide"
)

# --------------------------
# 核心数据定义
# --------------------------
# 集装箱标准规格 (mm, kg)
CONTAINER_TYPES = {
    "20GP": {"length": 5898, "width": 2352, "height": 2393, "max_weight": 28000, "tare_weight": 2200, "max_payload": 25800},
    "40GP": {"length": 12032, "width": 2352, "height": 2393, "max_weight": 30480, "tare_weight": 3750, "max_payload": 26730},
    "40HQ": {"length": 12032, "width": 2352, "height": 2698, "max_weight": 30480, "tare_weight": 4000, "max_payload": 26480},
    "45HQ": {"length": 13556, "width": 2352, "height": 2698, "max_weight": 32500, "tare_weight": 4800, "max_payload": 27700}
}

# 初始化会话状态
if "cargo_list" not in st.session_state:
    st.session_state.cargo_list = pd.DataFrame(
        columns=["货物名称", "长度(mm)", "宽度(mm)", "高度(mm)", "毛重(kg)", "净重(kg)", 
                 "易碎品", "最大承重(kg)", "柜号", "摆放方向", "堆叠层级", "备注"]
    )
if "batches" not in st.session_state:
    st.session_state.batches = {}
if "current_batch_id" not in st.session_state:
    st.session_state.current_batch_id = None
if "container_start_num" not in st.session_state:
    st.session_state.container_start_num = 1

# --------------------------
# 智能装箱算法核心类
# --------------------------
class CargoItem:
    """货物类，包含多维度摆放和堆叠属性"""
    def __init__(self, name, length, width, height, weight, is_fragile=False, max_load=10000):
        self.name = name
        self.original_dims = (length, width, height)
        self.weight = weight
        self.is_fragile = is_fragile
        self.max_load = max_load  # 最大可承受重量
        self.volume = length * width * height
        
        # 所有可能的摆放方向（去重）
        self.possible_orientations = list(set([
            (length, width, height),
            (width, length, height),
            (length, height, width),
            (height, length, width),
            (width, height, length),
            (height, width, length)
        ]))
    
    def get_best_orientation(self, container_dims):
        """根据集装箱尺寸选择最优摆放方向"""
        c_len, c_wid, c_hei = container_dims
        valid_orientations = []
        
        for dims in self.possible_orientations:
            l, w, h = dims
            if l <= c_len and w <= c_wid and h <= c_hei:
                # 计算空间适配度（越小越好）
                fit_score = (c_len - l) + (c_wid - w) + (c_hei - h)
                valid_orientations.append((fit_score, dims))
        
        if valid_orientations:
            return min(valid_orientations, key=lambda x: x[0])[1]
        return self.original_dims

class Container:
    """集装箱类，处理装载逻辑"""
    def __init__(self, container_type):
        self.type = container_type
        self.specs = CONTAINER_TYPES[container_type]
        self.length = self.specs["length"]
        self.width = self.specs["width"]
        self.height = self.specs["height"]
        self.max_payload = self.specs["max_payload"]
        
        # 装载状态
        self.loaded_weight = 0
        self.loaded_volume = 0
        self.loaded_items = []
        self.layers = []  # 堆叠层信息
        self.weight_distribution = {"front": 0, "back": 0, "left": 0, "right": 0}
    
    def can_load(self, cargo_item):
        """判断是否能装载该货物"""
        # 重量检查
        if self.loaded_weight + cargo_item.weight > self.max_payload:
            return False
        
        # 尺寸检查
        best_orient = cargo_item.get_best_orientation((self.length, self.width, self.height))
        if best_orient == cargo_item.original_dims:
            # 原始尺寸装不下
            return False
        
        return True
    
    def load_item(self, cargo_item, layer_idx=0):
        """装载货物，考虑堆叠和重心"""
        if not self.can_load(cargo_item):
            return False
        
        # 获取最优摆放方向
        orient = cargo_item.get_best_orientation((self.length, self.width, self.height))
        l, w, h = orient
        
        # 更新重量分布（简化版重心计算）
        weight = cargo_item.weight
        if l <= self.length/2:
            self.weight_distribution["front"] += weight
        else:
            self.weight_distribution["back"] += weight
        
        if w <= self.width/2:
            self.weight_distribution["left"] += weight
        else:
            self.weight_distribution["right"] += weight
        
        # 堆叠层级管理（重货在下，易碎品在上）
        if cargo_item.is_fragile:
            layer_idx = len(self.layers)  # 易碎品放最上层
        elif cargo_item.weight > 1000:  # 重货（>1吨）放底层
            layer_idx = 0
        
        # 确保层级存在
        while len(self.layers) <= layer_idx:
            self.layers.append({"height": 0, "weight": 0, "items": []})
        
        # 检查堆叠承重
        total_below_weight = sum(layer["weight"] for layer in self.layers[:layer_idx])
        if total_below_weight > cargo_item.max_load:
            return False
        
        # 更新装载状态
        self.loaded_weight += weight
        self.loaded_volume += cargo_item.volume
        self.layers[layer_idx]["height"] += h
        self.layers[layer_idx]["weight"] += weight
        self.layers[layer_idx]["items"].append({
            "name": cargo_item.name,
            "dimensions": orient,
            "weight": weight,
            "layer": layer_idx,
            "orientation": orient,
            "is_fragile": cargo_item.is_fragile
        })
        self.loaded_items.append(self.layers[layer_idx]["items"][-1])
        
        # 检查重心平衡（偏载不超过10%）
        weight_diff = abs(self.weight_distribution["front"] - self.weight_distribution["back"])
        if weight_diff / self.loaded_weight > 0.1:
            st.warning(f"集装箱{self.type}存在前后偏载风险！")
        
        weight_diff = abs(self.weight_distribution["left"] - self.weight_distribution["right"])
        if weight_diff / self.loaded_weight > 0.1:
            st.warning(f"集装箱{self.type}存在左右偏载风险！")
        
        return True
    
    def get_utilization(self):
        """计算空间和重量利用率"""
        total_volume = self.length * self.width * self.height
        volume_util = (self.loaded_volume / total_volume) * 100
        weight_util = (self.loaded_weight / self.max_payload) * 100
        return {
            "volume_utilization": round(volume_util, 2),
            "weight_utilization": round(weight_util, 2),
            "total_layers": len(self.layers)
        }

# --------------------------
# 工具函数
# --------------------------
def unit_conversion(value, from_unit, to_unit, type_="dimension"):
    """单位转换：尺寸转mm，重量转kg"""
    if pd.isna(value) or value == 0:
        return 0
    
    # 尺寸转换 (目标mm)
    if type_ == "dimension":
        conversion = {
            "m": 1000, "cm": 10, "mm": 1, "英寸": 25.4, "英尺": 304.8
        }
    # 重量转换 (目标kg)
    else:
        conversion = {
            "kg": 1, "g": 0.001, "t": 1000, "lb": 0.453592, "oz": 0.0283495
        }
    
    return float(value) * conversion.get(from_unit, 1)

def extract_excel_data(file):
    """提取Excel中的货物数据，处理多sheet、多标题栏"""
    all_data = []
    xl_file = pd.ExcelFile(file)
    
    for sheet_name in xl_file.sheet_names:
        # 尝试读取不同行数作为表头，找到最可能的列
        for header_row in range(5):  # 最多检查前5行作为表头
            try:
                df = pd.read_excel(file, sheet_name=sheet_name, header=header_row)
                # 识别关键列（支持常见的列名变体）
                name_cols = [col for col in df.columns if any(key in str(col) for key in ["名称", "货名", "货物"])]
                dim_cols = [col for col in df.columns if any(key in str(col) for key in ["长", "宽", "高", "尺寸", "体积"])]
                weight_cols = [col for col in df.columns if any(key in str(col) for key in ["毛重", "净重", "重量"])]
                fragile_cols = [col for col in df.columns if any(key in str(col) for key in ["易碎", "易碎品", "脆弱"])]
                load_cols = [col for col in df.columns if any(key in str(col) for key in ["承重", "负载", "承受重量"])]
                
                if len(name_cols) >= 1 and len(dim_cols) >= 3 and len(weight_cols) >= 2:
                    # 提取核心数据
                    cargo_data = {
                        "货物名称": df[name_cols[0]].fillna("未知货物"),
                        "长度": df[dim_cols[0]].fillna(0),
                        "宽度": df[dim_cols[1]].fillna(0),
                        "高度": df[dim_cols[2]].fillna(0),
                        "毛重": df[weight_cols[0]].fillna(0) if len(weight_cols)>=1 else 0,
                        "净重": df[weight_cols[1]].fillna(0) if len(weight_cols)>=2 else 0,
                        "易碎品": df[fragile_cols[0]].fillna(False) if len(fragile_cols)>=1 else False,
                        "最大承重": df[load_cols[0]].fillna(10000) if len(load_cols)>=1 else 10000
                    }
                    all_data.append(pd.DataFrame(cargo_data))
                    break
            except:
                continue
    
    if not all_data:
        # 如果无法自动识别，返回原始数据让用户手动映射
        df = pd.read_excel(file)
        return df
    return pd.concat(all_data, ignore_index=True)

def smart_container_loading(cargo_df):
    """智能装箱算法：考虑多维度摆放、重心、堆叠"""
    # 转换为货物对象列表（按重量降序排列，重货先装）
    cargo_items = []
    for _, row in cargo_df.iterrows():
        item = CargoItem(
            name=row["货物名称"],
            length=row["长度(mm)"],
            width=row["宽度(mm)"],
            height=row["高度(mm)"],
            weight=row["毛重(kg)"],
            is_fragile=row["易碎品"],
            max_load=row["最大承重(kg)"]
        )
        cargo_items.append(item)
    
    # 按重量降序排序（重货先装）
    cargo_items.sort(key=lambda x: x.weight, reverse=True)
    
    # 初始化集装箱列表
    containers = []
    remaining_items = copy.deepcopy(cargo_items)
    
    # 尝试不同柜型，优先大柜（空间利用率更高）
    container_types = ["45HQ", "40HQ", "40GP", "20GP"]
    
    while remaining_items:
        # 选择最优柜型
        best_container = None
        best_utilization = 0
        best_remaining = remaining_items
        
        for ct in container_types:
            test_container = Container(ct)
            test_remaining = []
            
            # 尝试装载货物
            for item in remaining_items:
                if test_container.can_load(item):
                    test_container.load_item(item)
                else:
                    test_remaining.append(item)
            
            # 计算利用率
            util = test_container.get_utilization()
            total_util = (util["volume_utilization"] + util["weight_utilization"]) / 2
            
            if total_util > best_utilization:
                best_utilization = total_util
                best_container = test_container
                best_remaining = test_remaining
        
        # 添加最优集装箱到列表
        if best_container:
            containers.append(best_container)
            remaining_items = best_remaining
        else:
            # 没有能装下的集装箱（超大/超重货物）
            st.error(f"货物{remaining_items[0].name}无法装入任何标准集装箱！")
            break
    
    # 生成装载方案和更新货物信息
    loading_plan = []
    cargo_results = []
    container_num = st.session_state.container_start_num
    
    for idx, container in enumerate(containers):
        container_id = f"{container.type}{container_num + idx}"
        util = container.get_utilization()
        
        # 添加到装载方案
        loading_plan.append({
            "柜号": container_id,
            "柜型": container.type,
            "装载重量(kg)": round(container.loaded_weight, 2),
            "重量利用率(%)": util["weight_utilization"],
            "体积利用率(%)": util["volume_utilization"],
            "堆叠层数": util["total_layers"],
            "装载货物数": len(container.loaded_items)
        })
        
        # 更新货物信息
        for item in container.loaded_items:
            cargo_results.append({
                "货物名称": item["name"],
                "长度(mm)": item["dimensions"][0],
                "宽度(mm)": item["dimensions"][1],
                "高度(mm)": item["dimensions"][2],
                "毛重(kg)": item["weight"],
                "净重(kg)": cargo_df[cargo_df["货物名称"]==item["name"]]["净重(kg)"].iloc[0],
                "易碎品": item["is_fragile"],
                "最大承重(kg)": cargo_df[cargo_df["货物名称"]==item["name"]]["最大承重(kg)"].iloc[0],
                "柜号": container_id,
                "摆放方向": f"{item['orientation'][0]}×{item['orientation'][1]}×{item['orientation'][2]}",
                "堆叠层级": item["layer"],
                "备注": f"空间利用率{util['volume_utilization']}%"
            })
    
    # 转换为DataFrame
    cargo_df_result = pd.DataFrame(cargo_results)
    
    return loading_plan, cargo_df_result

def generate_marking(cargo_df, missing_info=None):
    """生成唛头信息"""
    if missing_info is None:
        missing_info = {"收货人": "", "目的港": "", "原产国": ""}
    
    marking_data = []
    for _, row in cargo_df.iterrows():
        marking = {
            "柜号": row["柜号"],
            "货物名称": row["货物名称"],
            "毛重(kg)": row["毛重(kg)"],
            "净重(kg)": row["净重(kg)"],
            "尺寸(mm)": f"{row['长度(mm)']}×{row['宽度(mm)']}×{row['高度(mm)']}",
            "摆放方向": row["摆放方向"],
            "堆叠层级": row["堆叠层级"],
            "收货人": missing_info["收货人"],
            "目的港": missing_info["目的港"],
            "原产国": missing_info["原产国"],
            "箱号": row.name + 1,
            "总箱数": len(cargo_df)
        }
        marking_data.append(marking)
    
    return pd.DataFrame(marking_data)

def generate_share_qr(share_url):
    """生成分享二维码"""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(share_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    # 保存到BytesIO
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return Image.open(buf)

# --------------------------
# 页面布局
# --------------------------
# 侧边栏
with st.sidebar:
    st.title("📦 集装箱配箱系统")
    st.divider()
    
    # 批次管理
    st.subheader("批次管理")
    batch_action = st.radio("操作类型", ["新建批次", "编辑现有批次"])
    
    if batch_action == "新建批次":
        if st.button("创建新批次"):
            batch_id = str(uuid.uuid4())[:8]
            st.session_state.current_batch_id = batch_id
            st.session_state.batches[batch_id] = {
                "id": batch_id,
                "create_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "cargo_list": pd.DataFrame(),
                "loading_plan": [],
                "status": "编辑中"
            }
            st.success(f"创建新批次成功！批次ID: {batch_id}")
    
    else:
        if st.session_state.batches:
            batch_ids = list(st.session_state.batches.keys())
            selected_batch = st.selectbox("选择批次", batch_ids, format_func=lambda x: f"{x} ({st.session_state.batches[x]['create_time']})")
            if st.button("加载批次"):
                st.session_state.current_batch_id = selected_batch
                st.session_state.cargo_list = st.session_state.batches[selected_batch]["cargo_list"]
                st.success(f"已加载批次 {selected_batch}")
        else:
            st.info("暂无历史批次，请先创建新批次")
    
    st.divider()
    
    # 集装箱起始编号设置
    st.subheader("集装箱编号设置")
    start_num = st.number_input("起始柜号数字", min_value=1, value=st.session_state.container_start_num)
    if st.button("确认起始编号"):
        st.session_state.container_start_num = start_num
        st.success(f"起始柜号已设置为: {start_num}")
    
    st.divider()
    
    # 支付集成（演示）
    st.subheader("💳 支付功能")
    if st.button("微信支付（演示）"):
        st.image("https://pay.weixin.qq.com/wiki/doc/apiv3/en/apis/img/example_qrcode.png", width=200)
        st.info("扫码支付演示 - 实际环境需对接微信支付API")

# 主页面
st.title("智能集装箱配箱测算系统")
st.divider()

# 标签页
tab1, tab2, tab3, tab4 = st.tabs(["📥 货物清单管理", "📊 智能配箱计算", "📤 结果导出", "📋 我的配货"])

# --------------------------
# 标签1：货物清单管理
# --------------------------
with tab1:
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader("手动添加货物")
        with st.form("add_cargo_form"):
            cargo_name = st.text_input("货物名称")
            col1_1, col1_2, col1_3 = st.columns(3)
            with col1_1:
                length = st.number_input("长度", min_value=0.0, value=0.0)
                length_unit = st.selectbox("单位", ["mm", "cm", "m", "英寸", "英尺"], index=0)
            with col1_2:
                width = st.number_input("宽度", min_value=0.0, value=0.0)
                width_unit = st.selectbox("单位", ["mm", "cm", "m", "英寸", "英尺"], index=0)
            with col1_3:
                height = st.number_input("高度", min_value=0.0, value=0.0)
                height_unit = st.selectbox("单位", ["mm", "cm", "m", "英寸", "英尺"], index=0)
            
            col2_1, col2_2 = st.columns(2)
            with col2_1:
                gross_weight = st.number_input("毛重", min_value=0.0, value=0.0)
                gross_unit = st.selectbox("单位", ["kg", "g", "t", "lb", "oz"], index=0)
            with col2_2:
                net_weight = st.number_input("净重", min_value=0.0, value=0.0)
                net_unit = st.selectbox("单位", ["kg", "g", "t", "lb", "oz"], index=0)
            
            # 新增堆叠相关属性
            col3_1, col3_2 = st.columns(2)
            with col3_1:
                is_fragile = st.checkbox("易碎品", value=False)
            with col3_2:
                max_load = st.number_input("最大承重(kg)", min_value=0.0, value=10000.0)
            
            submit_btn = st.form_submit_button("添加货物")
            
            if submit_btn and cargo_name:
                # 单位转换为mm和kg
                length_mm = unit_conversion(length, length_unit, "mm", "dimension")
                width_mm = unit_conversion(width, width_unit, "mm", "dimension")
                height_mm = unit_conversion(height, height_unit, "mm", "dimension")
                gross_kg = unit_conversion(gross_weight, gross_unit, "kg", "weight")
                net_kg = unit_conversion(net_weight, net_unit, "kg", "weight")
                
                new_row = pd.DataFrame({
                    "货物名称": [cargo_name],
                    "长度(mm)": [length_mm],
                    "宽度(mm)": [width_mm],
                    "高度(mm)": [height_mm],
                    "毛重(kg)": [gross_kg],
                    "净重(kg)": [net_kg],
                    "易碎品": [is_fragile],
                    "最大承重(kg)": [max_load],
                    "柜号": [""],
                    "摆放方向": [""],
                    "堆叠层级": [""],
                    "备注": ["手动添加"]
                })
                
                st.session_state.cargo_list = pd.concat(
                    [st.session_state.cargo_list, new_row], ignore_index=True
                )
                st.success("货物添加成功！")
    
    with col2:
        st.subheader("Excel导入货物清单")
        uploaded_file = st.file_uploader("上传Excel文件", type=["xlsx", "xls"])
        
        if uploaded_file:
            st.info("正在解析Excel文件...")
            try:
                # 提取Excel数据
                raw_data = extract_excel_data(uploaded_file)
                
                # 显示原始数据并让用户映射列
                st.subheader("数据映射")
                col_map = {}
                cols = st.columns(4)
                with cols[0]:
                    col_map["货物名称"] = st.selectbox("货物名称列", raw_data.columns)
                with cols[1]:
                    col_map["长度"] = st.selectbox("长度列", raw_data.columns)
                    length_unit = st.selectbox("长度单位", ["mm", "cm", "m", "英寸", "英尺"], index=0)
                with cols[2]:
                    col_map["宽度"] = st.selectbox("宽度列", raw_data.columns)
                    width_unit = st.selectbox("宽度单位", ["mm", "cm", "m", "英寸", "英尺"], index=0)
                with cols[3]:
                    col_map["高度"] = st.selectbox("高度列", raw_data.columns)
                    height_unit = st.selectbox("高度单位", ["mm", "cm", "m", "英寸", "英尺"], index=0)
                
                cols2 = st.columns(2)
                with cols2[0]:
                    col_map["毛重"] = st.selectbox("毛重列", raw_data.columns)
                    gross_unit = st.selectbox("毛重单位", ["kg", "g", "t", "lb", "oz"], index=0)
                with cols2[1]:
                    col_map["净重"] = st.selectbox("净重列", raw_data.columns)
                    net_unit = st.selectbox("净重单位", ["kg", "g", "t", "lb", "oz"], index=0)
                
                # 堆叠属性映射
                cols3 = st.columns(2)
                with cols3[0]:
                    if "易碎品" in raw_data.columns:
                        col_map["易碎品"] = st.selectbox("易碎品列", raw_data.columns)
                    else:
                        col_map["易碎品"] = None
                with cols3[1]:
                    if "最大承重" in raw_data.columns:
                        col_map["最大承重"] = st.selectbox("最大承重列", raw_data.columns)
                        load_unit = st.selectbox("承重单位", ["kg", "g", "t"], index=0)
                    else:
                        col_map["最大承重"] = None
                
                if st.button("确认导入"):
                    # 转换单位并导入
                    imported_data = pd.DataFrame({
                        "货物名称": raw_data[col_map["货物名称"]].fillna("未知货物"),
                        "长度(mm)": raw_data[col_map["长度"]].apply(lambda x: unit_conversion(x, length_unit, "mm", "dimension")),
                        "宽度(mm)": raw_data[col_map["宽度"]].apply(lambda x: unit_conversion(x, width_unit, "mm", "dimension")),
                        "高度(mm)": raw_data[col_map["高度"]].apply(lambda x: unit_conversion(x, height_unit, "mm", "dimension")),
                        "毛重(kg)": raw_data[col_map["毛重"]].apply(lambda x: unit_conversion(x, gross_unit, "kg", "weight")),
                        "净重(kg)": raw_data[col_map["净重"]].apply(lambda x: unit_conversion(x, net_unit, "kg", "weight")),
                        "易碎品": raw_data[col_map["易碎品"]].fillna(False) if col_map["易碎品"] else False,
                        "最大承重(kg)": raw_data[col_map["最大承重"]].apply(lambda x: unit_conversion(x, load_unit, "kg", "weight")) if col_map["最大承重"] else 10000,
                        "柜号": [""],
                        "摆放方向": [""],
                        "堆叠层级": [""],
                        "备注": ["Excel导入"]
                    })
                    
                    # 合并数据（替换/追加）
                    if st.checkbox("替换现有清单", value=True):
                        st.session_state.cargo_list = imported_data
                    else:
                        st.session_state.cargo_list = pd.concat(
                            [st.session_state.cargo_list, imported_data], ignore_index=True
                        )
                    
                    st.success(f"成功导入 {len(imported_data)} 条货物记录！")
            except Exception as e:
                st.error(f"导入失败：{str(e)}")
    
    # 显示货物清单
    st.subheader("当前货物清单")
    if not st.session_state.cargo_list.empty:
        st.dataframe(st.session_state.cargo_list, use_container_width=True)
        
        # 清空/删除功能
        col_btns = st.columns(2)
        with col_btns[0]:
            if st.button("清空清单"):
                st.session_state.cargo_list = pd.DataFrame(
                    columns=["货物名称", "长度(mm)", "宽度(mm)", "高度(mm)", "毛重(kg)", "净重(kg)", 
                             "易碎品", "最大承重(kg)", "柜号", "摆放方向", "堆叠层级", "备注"]
                )
                st.success("清单已清空")
        with col_btns[1]:
            delete_idx = st.number_input("删除行号", min_value=0, max_value=len(st.session_state.cargo_list)-1, value=0)
            if st.button("删除选中行"):
                st.session_state.cargo_list = st.session_state.cargo_list.drop(delete_idx).reset_index(drop=True)
                st.success("已删除选中行")
    else:
        st.info("暂无货物数据，请添加或导入货物清单")

# --------------------------
# 标签2：智能配箱计算
# --------------------------
with tab2:
    st.subheader("智能集装箱配箱计算")
    
    if st.session_state.cargo_list.empty:
        st.warning("请先添加/导入货物清单")
    else:
        if st.button("开始智能配箱计算"):
            with st.spinner("正在计算最优配箱方案（考虑摆放方向、重心、堆叠）..."):
                loading_plan, cargo_df = smart_container_loading(st.session_state.cargo_list)
                st.session_state.cargo_list = cargo_df
                st.session_state.loading_plan = loading_plan
            
            # 显示配箱方案
            st.subheader("智能配箱方案结果")
            plan_df = pd.DataFrame(loading_plan)
            st.dataframe(plan_df, use_container_width=True)
            
            # 显示关键统计
            total_containers = len(loading_plan)
            total_weight = sum([plan["装载重量(kg)"] for plan in loading_plan])
            avg_volume_util = np.mean([plan["体积利用率(%)"] for plan in loading_plan])
            
            st.info(f"""
            **配箱统计**:
            - 总集装箱数：{total_containers} 个
            - 总装载重量：{round(total_weight, 2)} kg
            - 平均空间利用率：{round(avg_volume_util, 2)} %
            """)
            
            # 显示分配柜号后的货物清单
            st.subheader("货物详细装载信息")
            st.dataframe(st.session_state.cargo_list, use_container_width=True)
            
            # 唛头生成
            st.subheader("📝 唛头生成")
            generate_marking_flag = st.checkbox("需要生成唛头", value=True)
            
            if generate_marking_flag:
                st.info("请补充唛头缺失信息")
                missing_info = {
                    "收货人": st.text_input("收货人名称"),
                    "目的港": st.text_input("目的港"),
                    "原产国": st.text_input("原产国")
                }
                
                if st.button("生成唛头"):
                    marking_df = generate_marking(st.session_state.cargo_list, missing_info)
                    st.session_state.marking_df = marking_df
                    st.subheader("唛头信息（含装载细节）")
                    st.dataframe(marking_df, use_container_width=True)
            
            # 保存到当前批次
            if st.session_state.current_batch_id and st.button("保存到当前批次"):
                st.session_state.batches[st.session_state.current_batch_id]["cargo_list"] = st.session_state.cargo_list
                st.session_state.batches[st.session_state.current_batch_id]["loading_plan"] = loading_plan
                st.session_state.batches[st.session_state.current_batch_id]["status"] = "已计算"
                st.success("已保存到当前批次！")

# --------------------------
# 标签3：结果导出
# --------------------------
with tab3:
    st.subheader("数据导出功能")
    
    col_export1, col_export2, col_export3 = st.columns(3)
    
    with col_export1:
        st.subheader("导出货物清单")
        if not st.session_state.cargo_list.empty:
            # 准备导出数据
            export_cargo = st.session_state.cargo_list.copy()
            # 添加统计信息
            export_cargo.loc["合计"] = [
                "合计", "", "", "",
                export_cargo["毛重(kg)"].sum(),
                export_cargo["净重(kg)"].sum(),
                "", "", "", "", "", "统计信息"
            ]
            
            csv = export_cargo.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                label="导出CSV",
                data=csv,
                file_name=f"货物清单_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv",
                mime="text/csv"
            )
            
            # 导出Excel
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                export_cargo.to_excel(writer, sheet_name="货物清单", index=False)
                # 添加配箱方案（如果有）
                if "loading_plan" in st.session_state and st.session_state.loading_plan:
                    plan_df = pd.DataFrame(st.session_state.loading_plan)
                    plan_df.to_excel(writer, sheet_name="配箱方案", index=False)
            
            st.download_button(
                label="导出Excel",
                data=buffer,
                file_name=f"货物清单_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.info("暂无货物数据可导出")
    
    with col_export2:
        st.subheader("导出唛头")
        if "marking_df" in st.session_state and not st.session_state.marking_df.empty:
            csv = st.session_state.marking_df.to_csv(index=False, encoding="utf-8-sig")
            st.download_button(
                label="导出唛头CSV",
                data=csv,
                file_name=f"唛头_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv",
                mime="text/csv"
            )
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                st.session_state.marking_df.to_excel(writer, sheet_name="唛头信息", index=False)
            
            st.download_button(
                label="导出唛头Excel",
                data=buffer,
                file_name=f"唛头_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.info("请先生成唛头信息")
    
    with col_export3:
        st.subheader("分享功能")
        if st.session_state.current_batch_id:
            # 生成分享链接（演示）
            share_url = f"https://container-calculator.com/share/{st.session_state.current_batch_id}"
            st.code(share_url)
            
            # 生成二维码
            qr_img = generate_share_qr(share_url)
            st.image(qr_img, width=200)
            
            # 分享方式选择
            st.selectbox("分享方式", ["微信", "QQ", "邮件", "链接复制"])
            if st.button("复制分享链接"):
                st.success("链接已复制到剪贴板！")
        else:
            st.info("请先创建并保存批次")

# --------------------------
# 标签4：我的配货
# --------------------------
with tab4:
    st.subheader("我的配货批次")
    
    if st.session_state.batches:
        # 显示批次列表
        batch_ids = list(st.session_state.batches.keys())
        selected_batch = st.selectbox("选择批次", batch_ids, format_func=lambda x: f"批次{x} ({st.session_state.batches[x]['create_time']})")
        
        if selected_batch:
            batch_info = st.session_state.batches[selected_batch]
            
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**批次ID**: {selected_batch}")
                st.write(f"**创建时间**: {batch_info['create_time']}")
                st.write(f"**状态**: {batch_info['status']}")
            
            with col2:
                if st.button("追加货物"):
                    st.session_state.current_batch_id = selected_batch
                    st.success("已切换到该批次，可在货物清单管理标签页追加货物")
                if st.button("结束配置"):
                    st.session_state.batches[selected_batch]["status"] = "已完成"
                    st.success("该批次已标记为完成！")
            
            # 显示批次统计信息
            st.subheader("批次配箱统计")
            if "loading_plan" in batch_info and batch_info["loading_plan"]:
                plan_df = pd.DataFrame(batch_info["loading_plan"])
                st.dataframe(plan_df, use_container_width=True)
                
                # 汇总统计
                total_containers = len(plan_df)
                total_weight = plan_df["装载重量(kg)"].sum()
                container_types = ", ".join([f"{row['柜型']}({row['柜号']})" for _, row in plan_df.iterrows()])
                st.info(f"""
                **配箱统计**:
                - 总集装箱数：{total_containers} 个
                - 总装载重量：{round(total_weight, 2)} kg
                - 柜型分布：{container_types}
                """)
            
            # 显示该批次的货物清单
            st.subheader("批次货物清单（含装载细节）")
            st.dataframe(batch_info["cargo_list"], use_container_width=True)
    else:
        st.info("暂无配货批次，请先创建新批次并完成配箱计算")
