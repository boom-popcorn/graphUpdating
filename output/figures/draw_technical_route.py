from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


OUT_DIR = Path(__file__).resolve().parent
PNG_PATH = OUT_DIR / "maritime_kg_trusted_update_technical_route.png"
PDF_PATH = OUT_DIR / "maritime_kg_trusted_update_technical_route.pdf"


plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"],
    "axes.unicode_minus": False,
    "font.size": 10,
})


COLORS = {
    "ink": "#1D2939",
    "muted": "#475467",
    "line": "#667085",
    "light_line": "#D0D5DD",
    "input": "#EAF2F8",
    "stage1": "#DCEEFF",
    "stage2": "#E7F4EA",
    "stage3": "#FFF1D6",
    "stage4": "#F1E8F8",
    "constraint": "#F2F4F7",
    "output": "#E8F6F3",
    "white": "#FFFFFF",
    "blue": "#2878B5",
    "green": "#2A8C62",
    "orange": "#D17A00",
    "purple": "#7A4EAB",
}


def rounded_box(ax, x, y, w, h, text, face, edge, fontsize=9.5,
                weight="normal", radius=0.015, zorder=3, linespacing=1.35):
    patch = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0.008,rounding_size={radius}",
        linewidth=1.2,
        edgecolor=edge,
        facecolor=face,
        zorder=zorder,
    )
    ax.add_patch(patch)
    ax.text(
        x + w / 2, y + h / 2, text,
        ha="center", va="center", color=COLORS["ink"],
        fontsize=fontsize, fontweight=weight,
        linespacing=linespacing, zorder=zorder + 1,
    )
    return patch


def arrow(ax, start, end, color=None, width=1.5, style="-|>", zorder=2,
          connectionstyle="arc3"):
    arr = FancyArrowPatch(
        start, end,
        arrowstyle=style,
        mutation_scale=12,
        linewidth=width,
        color=color or COLORS["line"],
        connectionstyle=connectionstyle,
        zorder=zorder,
        shrinkA=2,
        shrinkB=2,
    )
    ax.add_patch(arr)
    return arr


fig, ax = plt.subplots(figsize=(16, 9.5), dpi=150)
fig.patch.set_facecolor(COLORS["white"])
ax.set_xlim(0, 1)
ax.set_ylim(0, 1)
ax.axis("off")

# Title
ax.text(
    0.5, 0.965,
    "面向多模态流数据的海上时空知识图谱可信增量更新技术路线",
    ha="center", va="center", fontsize=18, fontweight="bold", color=COLORS["ink"],
)
ax.text(
    0.5, 0.932,
    "双时态事件建模  ·  证据冲突消解  ·  约束最小修复  ·  增量推理与版本维护",
    ha="center", va="center", fontsize=10.5, color=COLORS["muted"],
)

# Inputs
ax.text(0.035, 0.865, "多模态流式数据输入", ha="left", va="center",
        fontsize=11, fontweight="bold", color=COLORS["ink"])
input_x = [0.19, 0.34, 0.49, 0.64, 0.79]
input_labels = ["AIS / 雷达", "图像 / 视频", "时空轨迹", "事件文本", "通信观测"]
for x, label in zip(input_x, input_labels):
    rounded_box(ax, x, 0.825, 0.12, 0.065, label, COLORS["input"], COLORS["blue"],
                fontsize=9.5, weight="bold", radius=0.012)
    arrow(ax, (x + 0.06, 0.823), (x + 0.06, 0.785), color=COLORS["blue"], width=1.2)

# Event normalization bus
rounded_box(
    ax, 0.17, 0.735, 0.76, 0.05,
    "数据预处理与事件标准化：时间同步 · 坐标转换 · 实体初步关联 · 模态特征封装",
    "#F8FAFC", COLORS["light_line"], fontsize=9.5, radius=0.01,
)
arrow(ax, (0.55, 0.733), (0.55, 0.713), color=COLORS["line"], width=1.4)

# Cross-cutting constraints, placed as an independent band to avoid crowding.
rounded_box(
    ax, 0.17, 0.665, 0.76, 0.047,
    "贯穿约束：本体约束（类型、定义域、值域、互斥、基数）  ·  "
    "时空约束（先后、重叠、位置、速度、可达性）  ·  "
    "业务约束（身份、行为、风险、证据规则）",
    COLORS["constraint"], COLORS["light_line"], fontsize=8.5, radius=0.01,
)
arrow(ax, (0.55, 0.663), (0.55, 0.635), color=COLORS["line"], width=1.4)

# Left labels and central stages
stage_y = [0.545, 0.405, 0.265, 0.125]
stage_colors = [COLORS["stage1"], COLORS["stage2"], COLORS["stage3"], COLORS["stage4"]]
stage_edges = [COLORS["blue"], COLORS["green"], COLORS["orange"], COLORS["purple"]]
stage_titles = [
    "研究内容一\n双时态事件建模",
    "研究内容二\n多模态证据冲突消解",
    "研究内容三\n多重约束下的局部最小修复",
    "研究内容四\n增量推理与版本维护",
]
stage_core = [
    "观测事件化  →  候选声明生成  →  有效时间/事务时间标注  →  目标状态演化链构建",
    "证据子图构建  →  冲突检测  →  来源可靠性动态评估  →  候选事实可信判定",
    "影响子图定位  →  修复操作生成  →  修改代价与信息损失优化  →  最小更新集选择",
    "规则依赖追踪  →  局部增量推理  →  派生结论维护  →  版本记录与历史回滚",
]
stage_outputs = [
    "观测事件流\n候选知识声明",
    "接受 / 降置信度\n暂缓 / 拒绝",
    "新增 / 修正 / 失效\n撤销 / 合并",
    "当前可信图谱\n证据链与版本",
]

for i, y in enumerate(stage_y):
    rounded_box(ax, 0.035, y, 0.15, 0.105, stage_titles[i], stage_colors[i],
                stage_edges[i], fontsize=9.5, weight="bold", radius=0.012)
    arrow(ax, (0.187, y + 0.0525), (0.215, y + 0.0525), color=stage_edges[i], width=1.5)
    rounded_box(ax, 0.218, y, 0.55, 0.105, stage_core[i], stage_colors[i],
                stage_edges[i], fontsize=9.2, radius=0.012)
    arrow(ax, (0.77, y + 0.0525), (0.795, y + 0.0525), color=stage_edges[i], width=1.5)
    rounded_box(ax, 0.798, y, 0.16, 0.105, stage_outputs[i], COLORS["white"],
                stage_edges[i], fontsize=8.8, radius=0.012)
    if i < 3:
        arrow(ax, (0.493, y - 0.003), (0.493, stage_y[i + 1] + 0.108),
              color=COLORS["line"], width=1.4)

# Application output band
for x, text in zip(
    [0.20, 0.39, 0.58, 0.77],
    ["可信海上时空知识图谱", "身份真伪判别", "异常行为识别", "威胁评估与态势研判"],
):
    rounded_box(ax, x, 0.035, 0.16, 0.06, text, COLORS["output"], COLORS["green"],
                fontsize=9.2, weight="bold", radius=0.012)

arrow(ax, (0.878, 0.133), (0.878, 0.098), color=COLORS["green"], width=1.4)

# Feedback loop: right side up to stage 1, with explicit label.
arrow(ax, (0.952, 0.17), (0.977, 0.17), color=COLORS["purple"], width=1.5)
arrow(ax, (0.977, 0.17), (0.977, 0.67), color=COLORS["purple"], width=1.5,
      style="-")
arrow(ax, (0.977, 0.67), (0.955, 0.67), color=COLORS["purple"], width=1.5)
ax.text(0.985, 0.42, "迟到证据 / 纠错信息触发历史回溯",
        rotation=90, ha="center", va="center", fontsize=8.5, color=COLORS["purple"])

# Footer annotation
ax.text(
    0.5, 0.008,
    "形成“事件接入—可信判定—一致性修复—增量维护—回溯反馈”的闭环更新机制",
    ha="center", va="bottom", fontsize=9.5, color=COLORS["muted"],
)

fig.savefig(PNG_PATH, dpi=300, bbox_inches="tight", facecolor=COLORS["white"])
fig.savefig(PDF_PATH, bbox_inches="tight", facecolor=COLORS["white"])
plt.close(fig)

print(PNG_PATH)
print(PDF_PATH)
