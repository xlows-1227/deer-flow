# Sandbox Python 环境与预装包建议

这份文档整理了当前项目里默认会启用的 public skills 对 Python 环境和第三方包的需求，并给出一份更适合常见办公分析场景的预装建议，重点覆盖 Excel 分析、PPT 生成和 UML/图表绘制。

## 结论

如果你的 sandbox 想尽量“开箱即用”，最低建议至少预装下面这些 Python 包：

- `duckdb`
- `openpyxl`
- `requests`
- `Pillow`
- `python-pptx`
- `PyYAML`

如果你经常做 Excel 分析、PPT 输出和 UML/关系图，推荐再补一组常用数据与制图包：

- `pandas`
- `numpy`
- `pyarrow`
- `xlsxwriter`
- `matplotlib`
- `seaborn`
- `plotly`
- `networkx`
- `graphviz`
- `lxml`

## 当前项目默认会用到的 skills

当前配置里没有看到对 public skills 的白名单限制，所以“默认可用”的基本可以理解为 `skills/public` 下的全部 21 个技能目录。

其中，真正依赖 Python 运行时或第三方包的主要是这几类：

| Skill | 作用 | 依赖情况 |
| --- | --- | --- |
| `data-analysis` | 数据分析、表格处理 | 运行时会尝试安装 `duckdb`、`openpyxl` |
| `image-generation` | 生成图片类工作流 | 用到 `requests`、`Pillow` |
| `ppt-generation` | 生成 PPT | 用到 `Pillow`、`python-pptx` |
| `skill-creator` | 创建/打包 skill | 用到 `PyYAML`，其余基本是标准库 |
| `github-deep-research` | 深度研究报告 | 优先用 `requests`，没有则走标准库回退 |
| `systematic-literature-review` | 文献综述 | 优先用 `requests`，没有则走标准库回退 |
| `podcast-generation` | 播客生成 | 用到 `requests` |
| `video-generation` | 视频生成 | 用到 `requests` |

### 代码参考

- [技能配置入口](D:/code/deer-flow/config.yaml:152)
- [data-analysis 脚本](D:/code/deer-flow/skills/public/data-analysis/scripts/analyze.py:21)
- [ppt-generation 技能](D:/code/deer-flow/skills/public/ppt-generation/SKILL.md)
- [image-generation 技能](D:/code/deer-flow/skills/public/image-generation/SKILL.md)
- [skill-creator 技能](D:/code/deer-flow/skills/public/skill-creator/SKILL.md)

## 最低必装的 Python 包

这部分是“让现有默认 skills 尽量少踩坑”的最低集合。

| 包名 | 为什么需要 |
| --- | --- |
| `duckdb` | 数据分析 skill 会直接依赖它，适合处理大表和 SQL 分析 |
| `openpyxl` | 读写 `.xlsx`，Excel 分析几乎必备 |
| `requests` | 多个生成/研究类 skill 会用到 |
| `Pillow` | 图片生成、PPT 生成都会用到 |
| `python-pptx` | 生成 PowerPoint 的核心包 |
| `PyYAML` | skill-creator 里会解析 YAML |

## 推荐预装的 Python 包

如果你希望 sandbox 更适合日常分析和输出，我建议按下面三组补齐。

### 1. Excel / 数据分析

- `pandas`
- `numpy`
- `pyarrow`
- `polars`
- `scipy`
- `statsmodels`
- `scikit-learn`
- `xlsxwriter`
- `xlrd`
- `pyxlsb`
- `odfpy`
- `sqlalchemy`

用途上可以简单理解为：

- `pandas` / `numpy`：数据清洗、透视、聚合、统计
- `pyarrow` / `polars`：更快的数据处理和列式存储支持
- `scipy` / `statsmodels`：统计分析
- `scikit-learn`：基础建模和聚类
- `xlsxwriter`：更强的 Excel 写出能力
- `xlrd` / `pyxlsb` / `odfpy`：兼容更多 Excel/表格格式
- `sqlalchemy`：方便连接数据库并做分析

### 2. PPT / 图表输出

- `python-pptx`
- `Pillow`
- `lxml`
- `matplotlib`
- `seaborn`
- `plotly`
- `kaleido`
- `cairosvg`
- `svgwrite`
- `reportlab`
- `jinja2`

用途上可以理解为：

- `python-pptx`：程序化生成 PPT
- `Pillow`：图片合成、裁切、字体渲染
- `matplotlib` / `seaborn`：静态图表
- `plotly` / `kaleido`：交互图表和导出图片
- `cairosvg` / `svgwrite`：SVG 转换和生成
- `reportlab`：复杂版式输出
- `jinja2`：模板化内容生成

### 3. UML / 关系图 / 结构图

- `graphviz`
- `pydot`
- `networkx`
- `diagrams`
- `plantuml`
- `mermaid-cli` 对应的 Node 工具链

用途上可以理解为：

- `graphviz`：流程图、类图、依赖图的基础渲染
- `pydot`：Python 和 Graphviz 之间的桥接
- `networkx`：复杂关系网络建模
- `diagrams`：云架构/系统图
- `plantuml`：UML 类图、时序图、活动图

## 建议的完整 Python 预装清单

如果你想一次性配得比较顺手，我建议把 sandbox 的 Python 预装包分成“基础层”和“增强层”。

### 基础层

```txt
duckdb
openpyxl
requests
Pillow
python-pptx
PyYAML
```

### 增强层

```txt
pandas
numpy
pyarrow
polars
scipy
statsmodels
scikit-learn
xlsxwriter
xlrd
pyxlsb
odfpy
sqlalchemy
matplotlib
seaborn
plotly
kaleido
lxml
cairosvg
svgwrite
reportlab
jinja2
graphviz
pydot
networkx
diagrams
plantuml
```

## 还需要注意的系统依赖

有些能力不是 Python 包本身能解决的，sandbox 里最好也一起预装系统组件。

| 能力 | 建议系统依赖 |
| --- | --- |
| Graphviz 渲染 | `graphviz` 可执行文件 |
| PlantUML | `openjdk` + PlantUML jar |
| Mermaid CLI | `nodejs` + `@mermaid-js/mermaid-cli` |
| PPT/图片字体 | `fontconfig`、中文字体、emoji 字体 |
| 图像导出 | `chromium` 或等效 headless 浏览器 |
| 办公文档转换 | `libreoffice` |
| 视频类输出 | `ffmpeg` |

如果你要做中文报表或中文 PPT，我还建议至少准备：

- `fonts-noto-cjk`
- `fonts-noto-color-emoji`

## 实际落地建议

如果你想把配置做得稳一点，我建议按这个优先级来装：

1. 先装“基础层” Python 包，确保默认 skills 不报缺包。
2. 再装 Excel/数据分析包，这是最常用、性价比最高的一层。
3. 最后补 PPT/UML 相关包和系统组件。

如果你愿意，我下一步可以继续帮你整理成一份可直接粘进 sandbox 镜像的安装清单，比如：

- `requirements.txt`
- `apt-get install` 列表
- `uv` / `pip` 可直接执行的安装命令
