import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import io
from pathlib import Path
from datetime import datetime, timedelta
import random
import requests
import os
import glob

# 可选 PDF 支持
try:
    from pypdf import PdfReader
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

# =========================
# 页面配置与样式
# =========================
st.set_page_config(page_title="FlightAI 智能航班系统", page_icon="✈️", layout="wide")
st.markdown("""
<style>
    [data-testid="stSidebarNav"] { border: 2px solid #a0c4ff; border-radius: 12px; padding: 10px; background-color: #eef5ff; }
    .sidebar .stRadio > div { border: 2px solid #a0c4ff; border-radius: 12px; padding: 8px; background-color: #eef5ff; }
    .stButton > button { background-color: #1e3c72; color: white; border-radius: 8px; border: none; padding: 0.5rem 1rem; font-weight: bold; }
    .stMetric { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 12px; padding: 16px; color: white; }
</style>
""", unsafe_allow_html=True)

st.title("✈️ FlightAI 智能航班运行分析系统")

# =========================
# 文件路径配置
# =========================
FILE_PATH = Path(r"E:\实验品1号\data\0101-0427.xls")
TAIL_MAPPING_FILE = Path("tail_mapping.csv")
MANUAL_DIR = Path(r"E:\实验品1号\手册")

# =========================
# 读取公司手册
# =========================
@st.cache_data
def load_manual(directory):
    if not directory.exists():
        st.warning(f"手册文件夹不存在：{directory}")
        return ""
    texts = []
    for filepath in glob.glob(str(directory / "*")):
        ext = Path(filepath).suffix.lower()
        try:
            if ext in ['.txt', '.md']:
                with open(filepath, 'r', encoding='utf-8') as f:
                    texts.append(f.read())
            elif ext == '.pdf' and PDF_SUPPORT:
                reader = PdfReader(filepath)
                text = "".join([page.extract_text() for page in reader.pages if page.extract_text()])
                texts.append(text)
        except Exception as e:
            st.warning(f"读取 {filepath} 失败: {e}")
    return "\n\n".join(texts)

manual_text = load_manual(MANUAL_DIR)
if manual_text:
    st.sidebar.success("✅ 已加载公司手册（用于AI建议）")
else:
    st.sidebar.info("未找到手册文件，将使用通用规则")

# =========================
# 数据加载
# =========================
@st.cache_data
def load_real_data():
    try:
        df = pd.read_excel(FILE_PATH)
    except Exception as e:
        st.error(f"读取失败：{e}")
        st.stop()
    required = ["航班号", "航班日期", "起飞站", "降落站", "起飞延误"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f"缺少列：{missing}")
        st.stop()
    numeric_cols = ["起飞延误", "放行延误", "保障延误", "到达延误", "流控时间"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["航班日期"] = pd.to_datetime(df["航班日期"], errors="coerce")
    df = df.dropna(subset=["航班日期"])
    df["正常航班"] = df["起飞延误"] <= 15
    df["航线"] = df["起飞站"].astype(str) + "-" + df["降落站"].astype(str)
    if "计划起飞时间" not in df.columns:
        def gen_plan(row):
            seed = hash(row["航班号"]) % 720
            hour = 8 + seed // 60
            minute = seed % 60
            base = datetime.combine(row["航班日期"], datetime.min.time()) + timedelta(hours=hour, minutes=minute)
            return base, base + timedelta(hours=2)
        df[["计划起飞时间", "计划到达时间"]] = df.apply(lambda r: pd.Series(gen_plan(r)), axis=1)
    else:
        df["计划起飞时间"] = pd.to_datetime(df["计划起飞时间"])
        df["计划到达时间"] = pd.to_datetime(df["计划到达时间"])
    df["实际起飞时间"] = df["计划起飞时间"] + pd.to_timedelta(df["起飞延误"], unit='m')
    df["实际到达时间"] = df["计划到达时间"] + pd.to_timedelta(df["起飞延误"], unit='m')
    if "机号" in df.columns:
        st.info("已从Excel中读取真实机号。")
        df["机号"] = df["机号"].fillna("未知")
    else:
        if TAIL_MAPPING_FILE.exists():
            mapping = pd.read_csv(TAIL_MAPPING_FILE)
            mapping_dict = dict(zip(mapping["航班号"], mapping["机号"]))
            df["机号"] = df["航班号"].map(mapping_dict).fillna("未知")
            st.info(f"已从 {TAIL_MAPPING_FILE} 加载机号映射。")
        else:
            unique_flights = df["航班号"].unique()
            tail_numbers = [f"B{1000+i}" for i in range(len(unique_flights))]
            flight_to_tail = {flight: tail_numbers[i] for i, flight in enumerate(unique_flights)}
            df["机号"] = df["航班号"].map(flight_to_tail)
            st.warning("未找到真实机号，已生成模拟机号（如B1001）。")
    return df

df_real = load_real_data()

# 提取航班模式（用于智能虚拟航班）
@st.cache_data
def extract_flight_patterns(df):
    route_map = df.groupby("航班号").agg({
        "起飞站": lambda x: x.mode()[0] if not x.mode().empty else "未知",
        "降落站": lambda x: x.mode()[0] if not x.mode().empty else "未知",
        "计划起飞时间": lambda x: x.mode()[0] if not x.mode().empty else None,
        "计划到达时间": lambda x: x.mode()[0] if not x.mode().empty else None
    }).reset_index()
    route_map["飞行时长"] = (route_map["计划到达时间"] - route_map["计划起飞时间"]).dt.total_seconds() / 60
    route_map["飞行时长"] = route_map["飞行时长"].fillna(120)
    tail_rotations = {}
    for tail, group in df.sort_values(["航班日期", "计划起飞时间"]).groupby("机号"):
        daily_seqs = group.groupby(group["航班日期"].dt.date)["航班号"].apply(list).tolist()
        if daily_seqs:
            tail_rotations[tail] = daily_seqs[0]
        else:
            tail_rotations[tail] = []
    return route_map, tail_rotations

route_map, tail_rotations = extract_flight_patterns(df_real)

def generate_smart_virtual_flights(start_date, end_date, route_map, tail_rotations, df_real):
    virtual = []
    cur = start_date
    min_turn = 45
    while cur <= end_date:
        for tail, seq in tail_rotations.items():
            prev_arr = None
            for flight in seq:
                info = route_map[route_map["航班号"] == flight]
                if info.empty:
                    continue
                dep = info.iloc[0]["起飞站"]
                arr = info.iloc[0]["降落站"]
                typ_dep = info.iloc[0]["计划起飞时间"]
                dur = info.iloc[0]["飞行时长"]
                if typ_dep:
                    plan_dep = datetime.combine(cur, typ_dep.time())
                else:
                    plan_dep = datetime.combine(cur, datetime.min.time()) + timedelta(hours=8)
                if prev_arr:
                    earliest = prev_arr + timedelta(minutes=min_turn)
                    if plan_dep < earliest:
                        plan_dep = earliest
                offset = random.randint(-15, 30)
                plan_dep += timedelta(minutes=offset)
                plan_arr = plan_dep + timedelta(minutes=dur)
                hist_delay = df_real[df_real["航班号"] == flight]["起飞延误"].mean()
                if pd.isna(hist_delay):
                    hist_delay = 0
                delay = max(0, hist_delay + random.gauss(0, 10))
                virtual.append({
                    "航班号": flight, "航班日期": cur, "机号": tail,
                    "起飞站": dep, "降落站": arr,
                    "计划起飞时间": plan_dep, "计划到达时间": plan_arr,
                    "实际起飞时间": plan_dep + timedelta(minutes=delay),
                    "实际到达时间": plan_arr + timedelta(minutes=delay),
                    "起飞延误": delay, "航线": f"{dep}-{arr}",
                    "正常航班": delay <= 15
                })
                prev_arr = plan_arr
        cur += timedelta(days=1)
    return pd.DataFrame(virtual)

# =========================
# 侧边栏导航
# =========================
st.sidebar.title("📋 功能导航")
page = st.sidebar.radio("", [
    "运行总览", "航班查询", "航班统计", "延误分析", "航线与风险", "航班甘特图（含虚拟未来）"
])

# 全局筛选（仅用于非甘特图页面）
if page != "航班甘特图（含虚拟未来）":
    st.sidebar.header("🔍 全局筛选")
    min_date = df_real["航班日期"].min().date()
    max_date = df_real["航班日期"].max().date()
    date_range = st.sidebar.date_input("日期范围", [min_date, max_date], min_value=min_date, max_value=max_date)
    if len(date_range) == 2:
        start, end = date_range
        mask = (df_real["航班日期"].dt.date >= start) & (df_real["航班日期"].dt.date <= end)
        df_filtered = df_real[mask].copy()
    else:
        df_filtered = df_real.copy()
    delay_min = st.sidebar.slider("最小起飞延误(分钟)", 0, 180, 0)
    df_filtered = df_filtered[df_filtered["起飞延误"] >= delay_min]
    if "延误原因" in df_real.columns:
        reasons = df_filtered["延误原因"].dropna().unique()
        sel_reasons = st.sidebar.multiselect("延误原因", reasons)
        if sel_reasons:
            df_filtered = df_filtered[df_filtered["延误原因"].isin(sel_reasons)]
    st.sidebar.metric("当前航班数", len(df_filtered))
else:
    df_filtered = df_real

# =========================
# 各个页面模块（非甘特图部分简化展示，实际使用时可补充完整）
# =========================
if page == "运行总览":
    st.header("📊 运行总览")
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("总航班数", len(df_filtered))
    c2.metric("航班号数量", df_filtered["航班号"].nunique())
    c3.metric("平均起飞延误", round(df_filtered["起飞延误"].mean(),1))
    c4.metric("正常率", f"{round(df_filtered['正常航班'].mean()*100,2)}%")
    daily = df_filtered.groupby(df_filtered["航班日期"].dt.date)["起飞延误"].mean().reset_index()
    fig = px.line(daily, x="航班日期", y="起飞延误", markers=True)
    st.plotly_chart(fig, use_container_width=True)

elif page == "航班查询":
    st.header("🔎 智能航班筛选")
    query = st.text_input("输入航班号或后四位")
    def match(q, flights):
        q = q.strip()
        if not q: return []
        if q.isdigit(): return [f for f in flights if f.endswith(q)]
        return [f for f in flights if q.upper() in f.upper()]
    all_flights = df_filtered["航班号"].astype(str).unique()
    matched = match(query, all_flights) if query else []
    if query and matched:
        sel = st.selectbox("选择航班", matched) if len(matched)>1 else matched[0]
        flight_df = df_filtered[df_filtered["航班号"].astype(str)==sel]
        st.subheader(f"{sel} 详情")
        a,b,c,d = st.columns(4)
        a.metric("班次", len(flight_df))
        b.metric("正常率", f"{flight_df['正常航班'].mean()*100:.1f}%")
        c.metric("平均延误", round(flight_df["起飞延误"].mean(),1))
        d.metric("最大延误", flight_df["起飞延误"].max())
        with st.expander("原始数据"):
            st.dataframe(flight_df)

elif page == "航班统计":
    st.header("📈 航班运行统计")
    summ = df_filtered.groupby("航班号").agg(班次=("航班号","count"), 正常率=("正常航班","mean"), 平均延误=("起飞延误","mean")).reset_index()
    summ["正常率"] = (summ["正常率"]*100).round(2)
    st.dataframe(summ, use_container_width=True)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        summ.to_excel(w, index=False)
    st.download_button("导出统计", buf.getvalue(), "航班统计.xlsx")

elif page == "延误分析":
    st.header("⏱️ 延误排名TOP20")
    rank = df_filtered.groupby("航班号")["起飞延误"].mean().reset_index().sort_values("起飞延误", ascending=False).head(20)
    st.dataframe(rank)
    fig = px.bar(rank, x="航班号", y="起飞延误", color="起飞延误")
    st.plotly_chart(fig, use_container_width=True)
    st.header("📉 延误分布")
    fig2 = px.histogram(df_filtered, x="起飞延误", nbins=50)
    st.plotly_chart(fig2, use_container_width=True)
    if "延误原因" in df_filtered.columns:
        st.header("延误原因")
        reason_counts = df_filtered["延误原因"].value_counts().reset_index()
        reason_counts.columns = ["原因","次数"]
        st.dataframe(reason_counts)
        fig_pie = px.pie(reason_counts.head(10), names="原因", values="次数")
        st.plotly_chart(fig_pie, use_container_width=True)

elif page == "航线与风险":
    st.header("✈️ 航线分析")
    route = df_filtered.groupby("航线").agg(航班量=("航班号","count"), 平均延误=("起飞延误","mean")).reset_index()
    st.dataframe(route)
    st.header("⚠️ 风险航班")
    risk = df_filtered.groupby("航班号").agg(平均起飞=("起飞延误","mean"), 平均放行=("放行延误","mean")).reset_index()
    risk["风险分"] = risk["平均起飞"]*0.7 + risk["平均放行"]*0.3
    st.dataframe(risk.sort_values("风险分", ascending=False).head(20))
    corr_cols = [c for c in ["起飞延误","放行延误","保障延误","到达延误"] if c in df_filtered.columns]
    if len(corr_cols)>=2:
        fig_corr = px.imshow(df_filtered[corr_cols].corr(), text_auto=True)
        st.plotly_chart(fig_corr, use_container_width=True)

# =========================
# 航班甘特图模块（核心交互）
# =========================
elif page == "航班甘特图（含虚拟未来）":
    st.header("📅 航班运行甘特图（按机号）")
    st.markdown("**操作提示**：点击航班条 → 弹窗调整延误时刻及原因。")

    # 尺寸控制
    col_w, col_h = st.columns(2)
    with col_w:
        width_pct = st.slider("图表宽度（占屏幕比例）", 0.6, 1.0, 0.95, 0.05)
    with col_h:
        chart_height = st.slider("图表高度（像素）", 400, 1500, 900)

    # 默认显示当天
    today = datetime.now().date()
    g_start = st.date_input("起始日期", today)
    g_end = st.date_input("结束日期", today)

    show_virtual = st.checkbox("包含虚拟未来航班（智能衔接）", value=False)

    # 获取真实数据
    gantt_real = df_real[(df_real["航班日期"].dt.date >= g_start) & (df_real["航班日期"].dt.date <= g_end)].copy()

    gantt_virtual = pd.DataFrame()
    if show_virtual:
        future_start = max(g_start, datetime(2026, 4, 28).date())
        future_end = g_end
        if future_start <= future_end:
            if st.button("生成/刷新虚拟航班"):
                with st.spinner("生成中..."):
                    gantt_virtual = generate_smart_virtual_flights(future_start, future_end, route_map, tail_rotations, df_real)
                    st.session_state['virtual_df'] = gantt_virtual
                    st.success(f"生成 {len(gantt_virtual)} 条虚拟航班")
        if 'virtual_df' in st.session_state:
            gantt_virtual = st.session_state['virtual_df']

    if show_virtual and not gantt_virtual.empty:
        gantt_df = pd.concat([gantt_real, gantt_virtual], ignore_index=True)
    else:
        gantt_df = gantt_real.copy()

    if gantt_df.empty:
        st.warning("所选日期无航班，请扩大范围或生成虚拟航班")
    else:
        gantt_df["延误等级"] = pd.cut(gantt_df["起飞延误"], bins=[-1,5,15,30,999], labels=["准点","轻微","中度","严重"])
        gantt_df["机号"] = gantt_df["机号"].astype(str)
        tail_order = sorted(gantt_df["机号"].unique())
        gantt_df["机号"] = pd.Categorical(gantt_df["机号"], categories=tail_order, ordered=True)
        gantt_df = gantt_df.sort_values("机号")

        fig = px.timeline(gantt_df, x_start="计划起飞时间", x_end="实际到达时间", y="机号", color="延误等级",
                          hover_data=["航班号","起飞延误"], text="航班号",
                          color_discrete_map={"准点":"#2ecc71","轻微":"#f39c12","中度":"#e67e22","严重":"#e74c3c"})
        fig.update_traces(textposition="inside", textfont=dict(size=11, family="Arial Black", color="black"))
        fig.update_layout(
            height=chart_height,
            width=st.session_state.get('gantt_width', 1200) * width_pct,
            xaxis_rangeslider_visible=True,
            xaxis_range=[datetime.combine(g_start, datetime.min.time()), datetime.combine(g_end, datetime.min.time())+timedelta(days=1)]
        )

        # 点击事件处理
        event = st.plotly_chart(fig, key="gantt", on_select="rerun", use_container_width=(width_pct==1.0))

        if event and event.get("selection"):
            points = event["selection"]["points"]
            if points:
                p = points[0]
                y_val = p.get("y")
                x_val = p.get("x")
                if y_val and x_val:
                    click_time = pd.to_datetime(x_val)
                    matched = gantt_df[(gantt_df["机号"] == y_val) & (abs(gantt_df["计划起飞时间"] - click_time) < pd.Timedelta(minutes=1))]
                    if not matched.empty:
                        flight = matched.iloc[0]
                        # 弹出调整窗口（使用 expander 或 dialog，这里用 popover）
                        with st.popover(f"✈️ 调整航班 {flight['航班号']}"):
                            st.write(f"**当前计划起飞**: {flight['计划起飞时间'].strftime('%Y-%m-%d %H:%M')}")
                            st.write(f"**当前延误**: {flight['起飞延误']:.0f} 分钟")
                            new_delay = st.number_input("调整延误（分钟，负数=提前）", value=0, step=5, key=f"delay_{flight['航班号']}_{flight['航班日期']}")
                            # 延误原因列表
                            if "延误原因" in df_real.columns:
                                reasons_list = df_real["延误原因"].dropna().unique().tolist()
                            else:
                                reasons_list = ["天气", "流控", "机械故障", "机组超时", "旅客原因", "其他"]
                            reason = st.selectbox("调整原因", reasons_list + ["自定义"])
                            if reason == "自定义":
                                reason = st.text_input("请输入具体原因")
                            if st.button("确认调整"):
                                # 判断是真实还是虚拟航班
                                if flight['航班日期'] > datetime(2026, 4, 27).date() and show_virtual and not gantt_virtual.empty:
                                    idx = gantt_virtual[(gantt_virtual["航班号"]==flight["航班号"]) & (gantt_virtual["航班日期"]==flight["航班日期"])].index
                                    if len(idx):
                                        new_delay_val = flight["起飞延误"] + new_delay
                                        gantt_virtual.loc[idx, "起飞延误"] = new_delay_val
                                        gantt_virtual.loc[idx, "实际起飞时间"] = flight["计划起飞时间"] + timedelta(minutes=new_delay_val)
                                        gantt_virtual.loc[idx, "实际到达时间"] = flight["计划到达时间"] + timedelta(minutes=new_delay_val)
                                        st.session_state['virtual_df'] = gantt_virtual
                                        st.success("虚拟航班已调整，请重新生成（点击上方按钮）")
                                else:
                                    idx = df_real[(df_real["航班号"]==flight["航班号"]) & (df_real["航班日期"]==flight["航班日期"])].index
                                    if len(idx):
                                        new_delay_val = flight["起飞延误"] + new_delay
                                        df_real.loc[idx, "起飞延误"] = new_delay_val
                                        df_real.loc[idx, "实际起飞时间"] = flight["计划起飞时间"] + timedelta(minutes=new_delay_val)
                                        df_real.loc[idx, "实际到达时间"] = flight["计划到达时间"] + timedelta(minutes=new_delay_val)
                                        if "延误原因" in df_real.columns:
                                            df_real.loc[idx, "延误原因"] = reason
                                        st.success("调整成功！请手动刷新页面（点击下方按钮）")
                                        if st.button("刷新页面"):
                                            st.experimental_rerun()

        # 批量调整面板（备用）
        st.subheader("📌 批量调整（机号/日期）")
        if not gantt_real.empty:
            sel_tail = st.selectbox("机号", gantt_real["机号"].unique())
            sel_date = st.date_input("调整日期", g_start)
            target = gantt_real[(gantt_real["机号"]==sel_tail) & (gantt_real["航班日期"].dt.date==sel_date)]
            if not target.empty:
                add = st.number_input("增加延误(分钟)", -30, 180, 0, 5)
                if st.button("应用批量调整"):
                    idx = df_real[(df_real["机号"]==sel_tail) & (df_real["航班日期"].dt.date==sel_date)].index
                    if len(idx):
                        df_real.loc[idx, "起飞延误"] += add
                        df_real.loc[idx, "实际起飞时间"] += pd.Timedelta(minutes=add)
                        st.success("已调整，请刷新")
                        if st.button("刷新"):
                            st.experimental_rerun()
            else:
                st.info("所选日期无此机号航班")

        # 天气预警
        if show_virtual and not gantt_virtual.empty:
            st.subheader("⛈️ 未来航班天气预案")
            use_api = st.checkbox("使用真实天气（需 API Key）", False)
            key = st.text_input("API Key", type="password") if use_api else ""
            stations = set(gantt_virtual["起飞站"]).union(set(gantt_virtual["降落站"]))
            weather = {}
            for s in stations:
                if use_api and key:
                    try:
                        url = f"http://api.openweathermap.org/data/2.5/forecast?q={s}&appid={key}&units=metric&cnt=1"
                        resp = requests.get(url, timeout=3)
                        if resp.status_code == 200:
                            weather[s] = resp.json()['list'][0]['weather'][0]['description']
                        else:
                            weather[s] = "无法获取"
                    except:
                        weather[s] = "API错误"
                else:
                    weather[s] = random.choice(["晴","多云","小雨","雷阵雨"])
            for _, row in gantt_virtual.iterrows():
                w = weather.get(row["起飞站"], "晴")
                sug = f"{row['航班号']} 计划 {row['计划起飞时间'].strftime('%H:%M')} 从 {row['起飞站']} 起飞。"
                if "雨" in w or "雷" in w:
                    sug += f" 天气 {w}，建议提前30分钟起飞。"
                else:
                    sug += f" 天气 {w}，正常执行。"
                st.markdown(f"- {sug}")

st.sidebar.markdown("---")
st.sidebar.info("💡 提示：点击甘特图中的航班条即可调整延误时间及原因。手册已加载，可用于后续AI扩展。")