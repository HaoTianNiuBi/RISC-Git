// ============================================================================
// RV-Doctor 成果提交 PPT · demo2「深空 HUD」科技风
// 赛事：第二届 CIE 全国 RISC-V 高水平创新及应用大赛 · 高校赛道
// 设计：深空蓝底 070C1C / 电青 22D3EE /  violet A78BFA / 金 FBBF24
// 数据：全部来自 reports/data 实测数据包（docs/09 参赛技术文档同口径）
// 构建：node build_ppt.js（pptxgenjs 复用 demo1 的 node_modules）
// ============================================================================
const path = require("node:path");
const pptxgen = require("pptxgenjs");

const W = 13.33, H = 7.5, M = 0.55, CW = W - 2 * M;
const BG = "070C1C";          // 深空底
const PANEL = "0D1730";       // 卡片
const PANEL2 = "111E3D";      // 浅卡片
const CODEBG = "060D1A";      // 终端卡
const LINE = "1D2C52";        // 发丝线
const CYAN = "22D3EE";        // 主色 电青
const CYAN_DIM = "128DA6";
const VIOLET = "A78BFA";      // 辅色 紫
const VIOLET_DEEP = "6D5DF6";
const GOLD = "FBBF24";        // 强调 金
const RED = "F05D7B";         // 拒绝 红
const TXT = "E9F1FF";         // 主文字
const MUT = "93A4C4";         // 次文字
const MUT2 = "5E6F92";        // 弱文字（页脚）
const GHOST = "0C1734";       // 幽灵大数字
const ZH = "微软雅黑", MONO = "Consolas";
const TOTAL = 13;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.author = "RV-Doctor";
pres.title = "RV-Doctor · 面向 RISC-V 的 AI 代码性能诊断与自动优化平台";

const soft = () => ({ type: "outer", color: "000000", blur: 10, offset: 3, angle: 90, opacity: 0.4 });
const glow = (c, op = 0.45) => ({ type: "outer", color: c, blur: 14, offset: 0, angle: 0, opacity: op });
const bu = () => ({ code: "25B8", indent: 12 });

function card(s, x, y, w, h, fill = PANEL, line = LINE) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: fill }, line: { color: line, width: 0.75 }, rectRadius: 0.06, shadow: soft() });
}
// HUD 四角括号
function hud(s, x, y, w, h, c = CYAN) {
  const L = 0.16, t = 0.02;
  const seg = (sx, sy, sw, sh) => s.addShape(pres.shapes.RECTANGLE, { x: sx, y: sy, w: sw, h: sh, fill: { color: c } });
  seg(x, y, L, t); seg(x, y, t, L);
  seg(x + w - L, y, L, t); seg(x + w - t, y, t, L);
  seg(x, y + h - t, L, t); seg(x, y + h - L, t, L);
  seg(x + w - L, y + h - t, L, t); seg(x + w - t, y + h - L, t, L);
}
// 内容页骨架：幽灵数字 + kicker + 标题 + 强调条 + 页脚
function baseSlide(num, en, zhTitle) {
  const s = pres.addSlide();
  s.background = { color: BG };
  const nn = String(num).padStart(2, "0");
  s.addText(nn, { x: 9.7, y: 0.3, w: 3.1, h: 1.65, fontSize: 128, fontFace: MONO, bold: true, color: GHOST, align: "right", margin: 0 });
  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 0.47, w: 0.1, h: 0.1, fill: { color: CYAN }, shadow: glow(CYAN, 0.5) });
  s.addText(`${nn}  //  ${en}`, { x: M + 0.24, y: 0.35, w: 7.5, h: 0.32, fontSize: 11.5, fontFace: MONO, color: CYAN, charSpacing: 3, margin: 0 });
  s.addText("RV-DOCTOR · 成果提交", { x: 9.2, y: 0.35, w: 3.58, h: 0.32, fontSize: 10.5, fontFace: MONO, color: MUT2, charSpacing: 2, align: "right", margin: 0 });
  s.addText(zhTitle, { x: M, y: 0.74, w: CW - 1.4, h: 0.56, fontSize: 27, fontFace: ZH, bold: true, color: TXT, margin: 0 });
  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 1.4, w: 0.6, h: 0.05, fill: { color: CYAN } });
  s.addShape(pres.shapes.RECTANGLE, { x: M + 0.6, y: 1.4, w: 0.2, h: 0.05, fill: { color: VIOLET } });
  s.addShape(pres.shapes.LINE, { x: M + 0.95, y: 1.425, w: CW - 0.95, h: 0, line: { color: LINE, width: 0.75 } });
  s.addShape(pres.shapes.LINE, { x: M, y: 7.04, w: CW, h: 0, line: { color: LINE, width: 0.75 } });
  s.addText("RV-Doctor · 第二届 CIE 全国 RISC-V 高水平创新及应用大赛（高校赛道）", { x: M, y: 7.12, w: 9.5, h: 0.3, fontSize: 10.5, fontFace: ZH, color: MUT2, margin: 0 });
  s.addText(`${nn} / ${TOTAL}`, { x: 11.5, y: 7.12, w: 1.28, h: 0.3, fontSize: 10.5, fontFace: MONO, color: MUT2, align: "right", margin: 0 });
  return s;
}

// ============================== S1 封面 ==============================
{
  const s = pres.addSlide();
  s.background = { color: BG };
  // 装饰：同心圆环 + 点阵
  s.addShape(pres.shapes.OVAL, { x: 9.1, y: -1.7, w: 6.3, h: 6.3, fill: { type: "none" }, line: { color: "12244D", width: 1.25 } });
  s.addShape(pres.shapes.OVAL, { x: 10.5, y: -0.35, w: 3.5, h: 3.5, fill: { type: "none" }, line: { color: "1B2F5E", width: 1 } });
  s.addShape(pres.shapes.OVAL, { x: 11.85, y: 5.75, w: 2.5, h: 2.5, fill: { type: "none" }, line: { color: "143356", width: 1 } });
  for (let r = 0; r < 3; r++) for (let c = 0; c < 5; c++) {
    const hot = (r === 1 && c === 2) || (r === 0 && c === 4);
    s.addShape(pres.shapes.RECTANGLE, { x: 11.05 + c * 0.34, y: 2.05 + r * 0.34, w: 0.05, h: 0.05, fill: { color: hot ? CYAN : "1E3A6B" } });
  }
  // 顶部 kicker + 赛道徽章
  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 0.62, w: 0.11, h: 0.11, fill: { color: CYAN }, shadow: glow(CYAN, 0.55) });
  s.addText("CIE · RISC-V · 2026  //  DELIVERABLE", { x: M + 0.26, y: 0.5, w: 7.6, h: 0.34, fontSize: 12, fontFace: MONO, color: CYAN, charSpacing: 3, margin: 0 });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 10.35, y: 0.44, w: 2.43, h: 0.44, fill: { color: PANEL }, line: { color: VIOLET_DEEP, width: 1 }, rectRadius: 0.22 });
  s.addText("高校赛道 · 成果提交", { x: 10.35, y: 0.51, w: 2.43, h: 0.3, fontSize: 11, fontFace: ZH, bold: true, color: VIOLET, align: "center", margin: 0 });
  // 赛事全称
  s.addText("第二届 CIE 全国 RISC-V 高水平创新及应用大赛 —— 赛事主题「芯芯之火 · 芯芯向荣」", { x: M, y: 1.42, w: 12.2, h: 0.36, fontSize: 13.5, fontFace: ZH, color: MUT, charSpacing: 1, margin: 0 });
  // 主标题
  s.addText([
    { text: "RV-", options: { color: TXT } },
    { text: "Doctor", options: { color: CYAN } },
  ], { x: M, y: 1.95, w: 12.2, h: 1.3, fontSize: 68, fontFace: ZH, bold: true, margin: 0 });
  s.addText("面向 RISC-V 的 AI 代码性能诊断与自动优化平台", { x: M, y: 3.5, w: 12.2, h: 0.55, fontSize: 23, fontFace: ZH, color: TXT, margin: 0 });
  s.addText("每一条结论，都有指令级证据。", { x: M, y: 4.16, w: 10, h: 0.45, fontSize: 15.5, fontFace: ZH, italic: true, color: MUT, margin: 0 });
  // 强调条
  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 4.92, w: 0.9, h: 0.055, fill: { color: CYAN }, shadow: glow(CYAN, 0.5) });
  s.addShape(pres.shapes.RECTANGLE, { x: M + 0.9, y: 4.92, w: 0.28, h: 0.055, fill: { color: VIOLET } });
  s.addShape(pres.shapes.LINE, { x: M + 1.35, y: 4.948, w: CW - 1.35, h: 0, line: { color: LINE, width: 0.75 } });
  // 三个关键数字
  const stats = [["166", "pytest 用例全绿", CYAN], ["10", "基准程序实测冻结", CYAN], ["100%", "动态指令数可复现", GOLD]];
  stats.forEach((st, i) => {
    const x = M + i * 3.35;
    s.addText(st[0], { x, y: 5.28, w: 3.1, h: 1.0, fontSize: 48, fontFace: ZH, bold: true, color: st[2], margin: 0 });
    s.addText(st[1], { x, y: 6.34, w: 3.1, h: 0.35, fontSize: 12.5, fontFace: ZH, color: MUT, margin: 0 });
  });
  // 底部工具链
  s.addText("TOOLCHAIN  //  xPack riscv-none-elf-gcc 15.2.0  ·  QEMU 11.1.0  ·  Python 3.14  ·  Windows 11 x64", { x: M, y: 6.92, w: 12.2, h: 0.3, fontSize: 10.5, fontFace: MONO, color: MUT2, margin: 0 });
  s.addNotes("开场一句话定位：输入 C 代码，输出一份经过验证的 RISC-V 优化报告。三个数字先建立可信度：166 测试、10 基准、指令数 100% 可复现。强调纯软件、Windows 原生、无开发板。");
}

// ============================== S2 项目概述 ==============================
{
  const s = baseSlide(2, "OVERVIEW", "项目是什么");
  s.addText([
    { text: "输入 C 源码，输出一份", options: {} },
    { text: "经过验证", options: { color: GOLD, bold: true } },
    { text: "的 RISC-V 优化报告：自动交叉编译 → QEMU 指令级运行 → 定位瓶颈 → 规则引擎 + 大模型给出带评分的建议 → 自动修改代码 → ", options: {} },
    { text: "验证失败即回滚", options: { color: CYAN, bold: true } },
    { text: "。", options: {} },
  ], { x: M, y: 1.64, w: CW, h: 0.98, fontSize: 17.5, fontFace: ZH, color: TXT, lineSpacing: 29, margin: 0 });

  const feats = [
    ["纯软件 · 无硬件", "一台 Windows 电脑完成全部工作：xPack GCC 15.2 + QEMU 11.1 裸机半主机，无需开发板 / WSL / Linux", CYAN],
    ["全自动验证闭环", "编译 → 输出一致 → 指令实测下降，三关全过才接受；任何一步失败自动回滚并保留失败证据", VIOLET],
    ["数据可溯源", "每个任务独立目录留存编译日志 / 输出 / trace / 指标 JSON，界面与报告的每个数字都可复算", CYAN],
  ];
  feats.forEach((f, i) => {
    const x = M + i * 4.175, w = 3.88;
    card(s, x, 2.9, w, 2.42);
    hud(s, x, 2.9, w, 2.42, f[2]);
    s.addText(f[0], { x: x + 0.3, y: 3.18, w: w - 0.6, h: 0.42, fontSize: 16.5, fontFace: ZH, bold: true, color: f[2], margin: 0 });
    s.addText(f[1], { x: x + 0.3, y: 3.74, w: w - 0.6, h: 1.45, fontSize: 12.5, fontFace: ZH, color: MUT, lineSpacing: 20, margin: 0 });
  });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: M, y: 5.66, w: CW, h: 0.62, fill: { color: PANEL2 }, line: { color: LINE, width: 0.75 }, rectRadius: 0.06 });
  s.addText([
    { text: "对应赛事主题方向   //   ", options: { fontFace: MONO, color: CYAN, fontSize: 12 } },
    { text: "编译器 · 模拟器 · 调试器 / 工具软件 · RISC-V 软件生态创新应用", options: { fontFace: ZH, color: TXT, fontSize: 13 } },
  ], { x: M + 0.3, y: 5.8, w: CW - 0.6, h: 0.36, fontSize: 13, margin: 0 });
  s.addNotes("先讲是什么，再讲三个差异化特性：无硬件依赖降低复现门槛；验证闭环是可信度核心；数据可溯源回应「数字是不是编的」。");
}

// ============================== S3 痛点与方案 ==============================
{
  const s = baseSlide(3, "WHY", "为什么做：AI 改代码，谁说了算？");
  const rows = [
    ["AI 生成的优化代码不可信", "建立验证闭环：AI 的任何修改必须通过编译、输出一致、指标实测下降三关，失败自动回滚"],
    ["性能数字常靠“跑个分”拍脑袋", "主指标 = 动态指令数：QEMU 逐条执行 trace 精确统计，两次运行完全一致、可手工复核"],
    ["Windows 没有 qemu-user 模式", "自走 qemu-system 裸机 + semihosting 路线：自研启动代码 + 半主机 I/O，Windows 原生全流程"],
    ["优化建议是黑盒，风险不可评估", "统一可信度评分：每条建议带置信度 / 收益 / 风险 / 汇编级证据，无证据不输出"],
  ];
  rows.forEach((r, i) => {
    const y = 1.74 + i * 1.24;
    s.addShape(pres.shapes.RECTANGLE, { x: M, y: y + 0.1, w: 0.1, h: 0.1, fill: { color: VIOLET } });
    s.addText(r[0], { x: M + 0.28, y: y - 0.04, w: 4.3, h: 0.62, fontSize: 15.5, fontFace: ZH, bold: true, color: TXT, margin: 0 });
    s.addShape(pres.shapes.LINE, { x: 5.05, y: y + 0.16, w: 0.62, h: 0, line: { color: CYAN, width: 1.75, endArrowType: "triangle" } });
    s.addText(r[1], { x: 6.0, y: y - 0.04, w: 6.78, h: 1.05, fontSize: 12.5, fontFace: ZH, color: MUT, lineSpacing: 20, margin: 0 });
    if (i < rows.length - 1) s.addShape(pres.shapes.LINE, { x: M, y: y + 1.04, w: CW, h: 0, line: { color: LINE, width: 0.75 } });
  });
  s.addNotes("四个真实痛点对应四个设计决策。重点讲第一条：AI 在项目里的角色是流水线的一环，产出必须过验证关。");
}

// ============================== S4 系统架构 ==============================
{
  const s = baseSlide(4, "ARCHITECTURE", "系统架构：UI 不碰工具链，一切经 Pipeline");
  const BX = 2.3, BW = W - M - BX;
  const arrow = (y) => s.addShape(pres.shapes.LINE, { x: W / 2, y, w: 0, h: 0.24, line: { color: CYAN, width: 1.5, endArrowType: "triangle" } });
  const label = (y, h, zh, en) => {
    s.addText(zh, { x: M, y: y + h / 2 - 0.3, w: 1.6, h: 0.34, fontSize: 14.5, fontFace: ZH, bold: true, color: CYAN, margin: 0 });
    s.addText(en, { x: M, y: y + h / 2 + 0.04, w: 1.6, h: 0.28, fontSize: 9.5, fontFace: MONO, color: MUT2, margin: 0 });
  };
  const box = (x, y, w, h, name, desc) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, fill: { color: PANEL }, line: { color: LINE, width: 0.75 }, rectRadius: 0.05 });
    s.addText(name, { x: x + 0.14, y: y + 0.09, w: w - 0.28, h: 0.32, fontSize: 13, fontFace: MONO, bold: true, color: TXT, margin: 0 });
    if (desc) s.addText(desc, { x: x + 0.14, y: y + 0.44, w: w - 0.28, h: h - 0.52, fontSize: 10.5, fontFace: ZH, color: MUT, lineSpacing: 15, margin: 0 });
  };
  label(1.62, 0.72, "UI 层", "STREAMLIT");
  box(BX, 1.62, BW, 0.72, "Streamlit 网页界面", "代码输入 · 编译配置 · 性能体检 · 建议列表 · 验证结果 · 对比报告");
  arrow(2.38);
  label(2.66, 0.72, "编排层", "PIPELINE");
  box(BX, 2.66, BW, 0.72, "pipeline.py", "任务目录管理 · 步骤编排 · 异常包装 · 证据链落盘");
  arrow(3.42);
  label(3.7, 1.12, "能力层", "MODULES");
  const caps = [
    ["compiler", "编译封装\n反汇编 + 静态统计"],
    ["profiler", "QEMU 运行器\ntrace 解析 / 指标 / 能耗"],
    ["optimizer", "规则引擎 R1~R9\nLLM 建议 + 验证闭环"],
    ["reports", "对比报告生成\n数字只取自实测 JSON"],
  ];
  const cw4 = (BW - 3 * 0.18) / 4;
  caps.forEach((c, i) => box(BX + i * (cw4 + 0.18), 3.7, cw4, 1.12, c[0], c[1]));
  arrow(4.86);
  label(5.14, 0.78, "工具层", "TOOLCHAIN");
  const tools = [["xPack GCC 15.2", "riscv-none-elf-gcc · objdump"], ["QEMU 11.1", "one-insn-per-tb 逐条执行"], ["LLM API（可选）", "OpenAI 兼容 · 缺省降级规则模式"]];
  const cw3 = (BW - 2 * 0.18) / 3;
  tools.forEach((t, i) => box(BX + i * (cw3 + 0.18), 5.14, cw3, 0.78, t[0], t[1]));
  s.addText("workdir/<task_id>/  //  build.log · stdout.txt · trace.log · trace_stats.json · metrics.json · verdict.json", { x: BX, y: 6.14, w: BW, h: 0.32, fontSize: 10.5, fontFace: MONO, color: MUT2, margin: 0 });
  s.addNotes("分层讲：界面只调 pipeline，工具链全部封装在能力层。所有中间产物落盘，失败产物也保留——这是可溯源的架构基础。");
}

// ============================== S5 核心技术① 精确计数 ==============================
{
  const s = baseSlide(5, "KEY TECH 01", "核心技术①：逐条执行 trace，动态指令精确计数");
  // 左：终端卡
  card(s, M, 1.62, 6.55, 1.92, CODEBG);
  s.addShape(pres.shapes.OVAL, { x: M + 0.18, y: 1.78, w: 0.1, h: 0.1, fill: { color: RED } });
  s.addShape(pres.shapes.OVAL, { x: M + 0.36, y: 1.78, w: 0.1, h: 0.1, fill: { color: GOLD } });
  s.addShape(pres.shapes.OVAL, { x: M + 0.54, y: 1.78, w: 0.1, h: 0.1, fill: { color: CYAN } });
  s.addText("qemu — one-insn-per-tb", { x: M + 0.8, y: 1.7, w: 4.5, h: 0.26, fontSize: 10, fontFace: MONO, color: MUT2, margin: 0 });
  s.addText([
    { text: "qemu-system-riscv64 -M virt -nographic -bios none \\\n", options: {} },
    { text: "  -semihosting-config enable=on,target=native \\\n", options: {} },
    { text: "  -accel tcg,one-insn-per-tb=on \\\n", options: {} },
    { text: "  -d in_asm,exec,nochain -D trace.log -kernel app.elf", options: {} },
  ], { x: M + 0.26, y: 2.06, w: 6.1, h: 1.4, fontSize: 11.5, fontFace: MONO, color: "8CE6CF", lineSpacing: 19, margin: 0 });
  // 左下：三个工程要点
  const pts = [
    ["PC 字段自适应定位", "不硬编码版本偏移：以 IN: 段 PC 全集交叉验证自动定位，同一解析器兼容 QEMU 8 / 9 / 11 日志布局"],
    ["符号注解行解析", "GCC 产物带符号表时行尾附加符号名——该缺陷曾致漏计 84% 指令，已用真实 trace 夹具锁定"],
    ["引导 ROM 剔除", "只计 DRAM 区（≥ 0x80000000）执行：内置引导 stub 与程序无关且计数抖动，剔除量进诊断字段"],
  ];
  pts.forEach((p, i) => {
    const y = 3.82 + i * 0.94;
    s.addText("▸ " + p[0], { x: M, y, w: 6.55, h: 0.34, fontSize: 14, fontFace: ZH, bold: true, color: CYAN, margin: 0 });
    s.addText(p[1], { x: M + 0.24, y: y + 0.36, w: 6.3, h: 0.56, fontSize: 11.5, fontFace: ZH, color: MUT, lineSpacing: 16, margin: 0 });
  });
  // 右：确定性红线
  card(s, 7.35, 1.62, 5.43, 4.82, PANEL2);
  hud(s, 7.35, 1.62, 5.43, 4.82, VIOLET);
  s.addText("确定性红线", { x: 7.7, y: 1.98, w: 4.7, h: 0.36, fontSize: 15, fontFace: ZH, bold: true, color: TXT, margin: 0 });
  s.addText("同一 ELF\n两次运行\n完全一致", { x: 7.7, y: 2.44, w: 4.8, h: 2.2, fontSize: 37, fontFace: ZH, bold: true, color: GOLD, lineSpacing: 50, margin: 0 });
  s.addText([
    { text: "in_asm 段 → PC → 助记符映射（静态）", options: { bullet: bu(), breakLine: true } },
    { text: "exec 段 → 逐条执行记录（动态计数）", options: { bullet: bu(), breakLine: true } },
    { text: "七类指令分布统计，映射表独立可配", options: { bullet: bu() } },
  ], { x: 7.7, y: 4.95, w: 4.75, h: 1.3, fontSize: 12.5, fontFace: ZH, color: TXT, paraSpaceAfter: 8, margin: 0 });
  s.addNotes("为什么主指标是指令数而不是秒数：逐条执行 trace 统计是确定性的，两次运行完全一致、可手工复核；墙上时间受宿主负载影响只能作参考。三个要点是 Windows 实测踩坑后锁定的格式红线。");
}

// ============================== S6 核心技术② 验证闭环 ==============================
{
  const s = baseSlide(6, "KEY TECH 02", "核心技术②：验证闭环状态机 —— 可信度靠机制");
  const gates = [
    ["PROPOSED", "提交候选"],
    ["COMPILED", "① 编译通过"],
    ["TESTED", "② 输出与 golden 一致"],
    ["MEASURED", "③ 确定性校验通过"],
    ["ACCEPTED", "④ 指令数实测下降"],
  ];
  const bw = 2.03, gap = 0.49, y0 = 2.0;
  gates.forEach((g, i) => {
    const x = M + i * (bw + gap), last = i === gates.length - 1;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: y0, w: bw, h: 1.0, fill: { color: last ? CYAN : PANEL }, line: last ? { color: CYAN, width: 0 } : { color: LINE, width: 0.75 }, rectRadius: 0.06, shadow: last ? glow(CYAN, 0.4) : soft() });
    s.addText(g[0], { x: x + 0.08, y: y0 + 0.14, w: bw - 0.16, h: 0.32, fontSize: 13.5, fontFace: MONO, bold: true, color: last ? BG : TXT, align: "center", margin: 0 });
    s.addText(g[1], { x: x + 0.06, y: y0 + 0.52, w: bw - 0.12, h: 0.4, fontSize: 10.5, fontFace: ZH, color: last ? "052E33" : MUT, align: "center", margin: 0 });
    if (!last) s.addShape(pres.shapes.LINE, { x: x + bw + 0.07, y: y0 + 0.5, w: gap - 0.14, h: 0, line: { color: CYAN, width: 1.75, endArrowType: "triangle" } });
  });
  // REJECT 分支
  s.addShape(pres.shapes.LINE, { x: 6.67, y: 3.06, w: 0, h: 0.56, line: { color: RED, width: 1.25, endArrowType: "triangle", dashType: "dash" } });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 4.42, y: 3.68, w: 4.5, h: 0.95, fill: { color: PANEL2 }, line: { color: "5B2733", width: 1 }, rectRadius: 0.06 });
  s.addText("REJECTED", { x: 4.52, y: 3.82, w: 4.3, h: 0.32, fontSize: 13.5, fontFace: MONO, bold: true, color: RED, align: "center", margin: 0 });
  s.addText("任一关失败 → 自动回滚原版 · 失败证据留存", { x: 4.52, y: 4.18, w: 4.3, h: 0.34, fontSize: 11, fontFace: ZH, color: MUT, align: "center", margin: 0 });
  s.addText([
    { text: "原版源码永不覆盖：优化版写入任务目录 optimized/，多个候选取动态指令数最小者", options: { bullet: bu(), breakLine: true } },
    { text: "判定数据全部来自实测 metrics.json；LLM 只给建议与候选源码，不给任何数字", options: { bullet: bu(), breakLine: true } },
    { text: "M5 实测：2 个基准走完 PROPOSED → ACCEPTED（调用 5,406 → 5,106，指令 −300）；刻意坏修改走完 REJECT → 回滚", options: { bullet: bu() } },
  ], { x: M, y: 5.2, w: CW, h: 1.5, fontSize: 13, fontFace: ZH, color: TXT, paraSpaceAfter: 9, lineSpacing: 19, margin: 0 });
  s.addNotes("这是项目最核心的机制创新。四关全部来自实测判定，任何一关失败自动回滚。现场可演示一次刻意构造的坏修改被回滚。");
}

// ============================== S7 四大创新点 ==============================
{
  const s = baseSlide(7, "INNOVATION", "四大创新点");
  const items = [
    ["01", "AI 验证代码，而非 AI 写代码", "分析 → 生成 → 验证 → 回滚闭环：可信度从“AI 说了算”变成“流水线说了算”", CYAN],
    ["02", "面向 RISC-V 指令特征的优化", "分析真实汇编与执行 trace：软浮点退化、循环不变量、寄存器溢出等 9 类问题，证据到汇编级", VIOLET],
    ["03", "优化建议可信度评分", "置信度 / 收益 / 风险 / 依据四要素；依据引用真实动态计数与反汇编片段，无证据不输出", VIOLET],
    ["04", "性能—能效联合视角", "可配置权重的能耗估算分 Σ(类别计数 × 权重)，全链路强制免责声明，服务绿色计算", CYAN],
  ];
  items.forEach((it, i) => {
    const x = M + (i % 2) * 6.23, y = 1.66 + Math.floor(i / 2) * 2.5, w = 6.0, h = 2.26;
    card(s, x, y, w, h);
    hud(s, x, y, w, h, it[3]);
    s.addText(it[0], { x: x + 0.3, y: y + 0.22, w: 1.0, h: 0.72, fontSize: 36, fontFace: MONO, bold: true, color: it[3], margin: 0 });
    s.addText(it[1], { x: x + 1.35, y: y + 0.3, w: w - 1.65, h: 0.5, fontSize: 16.5, fontFace: ZH, bold: true, color: TXT, margin: 0 });
    s.addText(it[2], { x: x + 1.35, y: y + 0.92, w: w - 1.65, h: 1.15, fontSize: 12.5, fontFace: ZH, color: MUT, lineSpacing: 19, margin: 0 });
  });
  s.addNotes("四个创新点按评审关注点排序：机制创新第一，RISC-V 含量第二，可解释性第三，绿色计算第四。每条都能在现场用平台当场演示。");
}

// ============================== S8 实测成果总览 ==============================
{
  const s = baseSlide(8, "RESULTS", "实测成果：10 个基准，全部真实可跑");
  const names = ["b01_sum_array", "b02_matrix_mul", "b03_crc32", "b04_string_search", "b05_bubble_sort",
                 "b06_grayscale", "b07_fib", "b08_dot_product", "b09_strlen_loop", "b10_window_sum"];
  const vals = [95021, 163456, 24353, 137475, 278255, 28369, 162583, 167089, 46318, 320853];
  const labels = names.map(n => n.slice(0, 3));
  // 横向条形图自下而上渲染 → 反转让 b01 在顶部
  const rLabels = [...labels].reverse();
  const rVals = [...vals].reverse();
  const rColors = rLabels.map(n => (n === "b06" || n === "b09") ? GOLD : CYAN_DIM);
  s.addChart(pres.charts.BAR, [{ name: "动态指令数", labels: rLabels, values: rVals }], {
    x: M, y: 1.55, w: 7.25, h: 4.9, barDir: "bar",
    chartColors: rColors, varyColors: true,
    chartArea: { fill: { color: BG } }, plotArea: { fill: { color: BG } },
    catAxisLabelColor: "AAB6CE", catAxisLabelFontSize: 11.5, catAxisLabelFontFace: MONO,
    valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" },
    showValue: true, dataLabelPosition: "outEnd", dataLabelColor: TXT, dataLabelFontSize: 10.5, dataLabelFontFace: MONO, dataLabelFormatCode: "#,##0",
    showLegend: false, showTitle: false, valAxisMaxVal: 360000,
  });
  card(s, 8.1, 1.55, 4.68, 4.9, PANEL2);
  hud(s, 8.1, 1.55, 4.68, 4.9, CYAN);
  s.addText("读数要点", { x: 8.45, y: 1.9, w: 4, h: 0.36, fontSize: 15, fontFace: ZH, bold: true, color: TXT, margin: 0 });
  s.addText([
    { text: "规模 2.4 万 ~ 32.1 万条，落在设计区间（10⁴ ~ 5×10⁶）", options: { bullet: bu(), breakLine: true } },
    { text: "b09 / b06（金色）为演示招牌：循环不变量外提 & 软浮点退化", options: { bullet: bu(), breakLine: true } },
    { text: "golden 输出经独立 Python 参考实现逐字符比对后冻结，回归零容差", options: { bullet: bu() } },
  ], { x: 8.45, y: 2.4, w: 4.0, h: 2.5, fontSize: 12.5, fontFace: ZH, color: TXT, paraSpaceAfter: 10, lineSpacing: 18, margin: 0 });
  s.addText("配置 rv64gc / -O0\nxPack GCC 15.2.0 · QEMU 11.1.0", { x: 8.45, y: 5.25, w: 4.0, h: 0.8, fontSize: 11.5, fontFace: ZH, color: MUT, lineSpacing: 17, margin: 0 });
  s.addText("数据来源：reports/data/baseline_overview.md（M3 冻结快照，动态指令数两次运行完全一致）", { x: M, y: 6.6, w: 11.5, h: 0.32, fontSize: 11, fontFace: ZH, color: MUT2, margin: 0 });
  s.addNotes("十个基准覆盖数组、矩阵、排序、字符串、递归、浮点、滑窗七类典型负载，全部真实编译运行。金色的 b06/b09 是后面两页的主角。");
}

// ============================== S9 R9 高光：b06 三架构 ==============================
{
  const s = baseSlide(9, "ARCH COMPARE", "R9 高光：同一浮点程序，三架构 12.4 倍差距");
  s.addChart(pres.charts.BAR, [{
    name: "动态指令数",
    labels: ["RV64GC 硬浮点", "RV64I 软浮点", "RV32I 软浮点"],
    values: [28369, 352363, 465454],
  }], {
    x: M, y: 1.6, w: 6.85, h: 4.4, barDir: "col",
    chartColors: [CYAN, VIOLET, "3E5A87"], varyColors: true,
    chartArea: { fill: { color: BG } }, plotArea: { fill: { color: BG } },
    catAxisLabelColor: "AAB6CE", catAxisLabelFontSize: 12, catAxisLabelFontFace: ZH,
    valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" },
    showValue: true, dataLabelPosition: "outEnd", dataLabelColor: TXT, dataLabelFontSize: 11.5, dataLabelFontFace: MONO, dataLabelFormatCode: "#,##0",
    showLegend: false, showTitle: false, valAxisMaxVal: 520000,
  });
  s.addText("b06_grayscale · -O0 · 逐条执行 trace 实测", { x: M, y: 6.1, w: 6.85, h: 0.32, fontSize: 11, fontFace: ZH, color: MUT2, margin: 0 });
  card(s, 7.7, 1.6, 5.08, 4.4, PANEL2);
  hud(s, 7.7, 1.6, 5.08, 4.4, VIOLET);
  s.addText("12.4×", { x: 8.05, y: 1.85, w: 4.4, h: 1.25, fontSize: 62, fontFace: ZH, bold: true, color: GOLD, margin: 0 });
  s.addText("RV64I 相对 RV64GC 的动态指令倍数（352,363 vs 28,369）", { x: 8.05, y: 3.22, w: 4.4, h: 0.55, fontSize: 12.5, fontFace: ZH, color: TXT, lineSpacing: 18, margin: 0 });
  s.addText([
    { text: "无 F/D 扩展时 float 退化为 libgcc 软浮点例程（__mulsf3 等）", options: { bullet: bu(), breakLine: true } },
    { text: "函数调用 164 → 4,537 次（27.7 倍）；RV32I 达 7,735 次", options: { bullet: bu(), breakLine: true } },
    { text: "R9 规则据此取证：建议整数移位近似重写，指令数骤降", options: { bullet: bu() } },
  ], { x: 8.05, y: 3.95, w: 4.45, h: 1.85, fontSize: 12, fontFace: ZH, color: MUT, paraSpaceAfter: 8, lineSpacing: 17, margin: 0 });
  s.addText("数据来源：reports/data/b06_arch_compare.md", { x: 7.7, y: 6.1, w: 5.08, h: 0.32, fontSize: 11, fontFace: ZH, color: MUT2, align: "right", margin: 0 });
  s.addNotes("这是「面向 RISC-V 指令特征的优化」最直观的证明：同一算法，架构约束不同，最优写法不同。软浮点退化肉眼可见——12.4 倍指令、27.7 倍调用。");
}

// ============================== S10 编译等级边界 ==============================
{
  const s = baseSlide(10, "COMPILER BOUNDARY", "诚实回应：“-O2 不就优化了吗”");
  s.addChart(pres.charts.BAR, [{
    name: "动态指令数", labels: ["-O0", "-O2"], values: [46318, 9075],
  }], {
    x: M, y: 1.65, w: 5.85, h: 4.25, barDir: "col",
    chartColors: [CYAN_DIM, CYAN], varyColors: true,
    chartArea: { fill: { color: BG } }, plotArea: { fill: { color: BG } },
    catAxisLabelColor: "AAB6CE", catAxisLabelFontSize: 13, catAxisLabelFontFace: MONO,
    valAxisHidden: true, valGridLine: { style: "none" }, catGridLine: { style: "none" },
    showValue: true, dataLabelPosition: "outEnd", dataLabelColor: TXT, dataLabelFontSize: 12.5, dataLabelFontFace: MONO, dataLabelFormatCode: "#,##0",
    showLegend: false, showTitle: false, valAxisMaxVal: 52000,
  });
  s.addText("b09_strlen_loop · rv64gc · 同源码仅改优化等级", { x: M, y: 6.0, w: 5.85, h: 0.32, fontSize: 11, fontFace: ZH, color: MUT2, margin: 0 });
  s.addText("-80.4%", { x: 6.85, y: 1.68, w: 5.9, h: 1.25, fontSize: 60, fontFace: ZH, bold: true, color: GOLD, margin: 0 });
  s.addText("指令量大幅削减，但函数调用仅 -43.1%（346 → 197）", { x: 6.85, y: 3.05, w: 5.9, h: 0.5, fontSize: 15, fontFace: ZH, bold: true, color: TXT, margin: 0 });
  s.addText([
    { text: "-O2 能告诉你“编译器已经做了什么”，本平台将其量化", options: { bullet: bu(), breakLine: true } },
    { text: "循环不变量外提、算法级重复等源码级优化空间依然存在", options: { bullet: bu(), breakLine: true } },
    { text: "对非内建函数 / 指针副作用场景，编译器无法外提 —— 平台价值在于给出两者的实测边界", options: { bullet: bu() } },
  ], { x: 6.85, y: 3.75, w: 5.9, h: 2.2, fontSize: 13, fontFace: ZH, color: MUT, paraSpaceAfter: 10, lineSpacing: 19, margin: 0 });
  s.addText("数据来源：reports/data/b09_opt_compare.md", { x: 6.85, y: 6.0, w: 5.9, h: 0.32, fontSize: 11, fontFace: ZH, color: MUT2, align: "right", margin: 0 });
  s.addNotes(" preempt 评审最常见的质疑。不回避 -O2 的强大，而是用实测数据划出编译器优化与源码级优化的边界——这正是平台的诊断价值。");
}

// ============================== S11 质量保证 ==============================
{
  const s = baseSlide(11, "QUALITY", "质量保证：166 个测试，一键全量复算");
  const layers = [
    ["单元层", "不依赖工具链：固定样例 trace 日志锁定格式（空日志 / 截断 / 异常行容错 / 旧版适配）、参数映射、分类映射", "pytest 随时可跑", false],
    ["集成层", "真实 GCC + QEMU：编译失败路径、端到端体检、确定性双运行校验、超时强杀", "工具缺失自动跳过", false],
    ["回归层", "10 基准 ×（golden 输出比对 + 指标快照零浮动）；不一致即回归，须重新冻结并说明原因", "数据红线机械化", true],
  ];
  layers.forEach((l, i) => {
    const y = 1.7 + i * 1.32;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: M, y, w: 2.15, h: 1.08, fill: { color: l[3] ? CYAN : PANEL2 }, line: l[3] ? { color: CYAN, width: 0 } : { color: LINE, width: 0.75 }, rectRadius: 0.06, shadow: l[3] ? glow(CYAN, 0.35) : soft() });
    s.addText(l[0], { x: M, y: y + 0.34, w: 2.15, h: 0.42, fontSize: 16, fontFace: ZH, bold: true, color: l[3] ? BG : TXT, align: "center", margin: 0 });
    s.addText(l[1], { x: 3.0, y: y + 0.06, w: 6.75, h: 1.0, fontSize: 12.5, fontFace: ZH, color: TXT, lineSpacing: 19, margin: 0 });
    s.addText(l[2], { x: 10.0, y: y + 0.36, w: 2.75, h: 0.4, fontSize: 11.5, fontFace: ZH, color: MUT, margin: 0 });
    if (i < 2) s.addShape(pres.shapes.LINE, { x: 3.0, y: y + 1.2, w: 9.75, h: 0, line: { color: LINE, width: 0.75 } });
  });
  card(s, M, 5.9, CW, 0.9, PANEL2);
  hud(s, M, 5.9, CW, 0.9, CYAN);
  s.addText([
    { text: "一键自检   ", options: { fontFace: ZH, bold: true, color: GOLD, fontSize: 15 } },
    { text: "scripts\\run_all_checks.bat    ", options: { fontFace: MONO, color: CYAN, fontSize: 13 } },
    { text: "pytest 全层 + 10 基准 golden + 指标快照回归 → ALL CHECKS PASSED，评审可当场复算", options: { fontFace: ZH, color: MUT, fontSize: 12.5 } },
  ], { x: M + 0.32, y: 6.16, w: CW - 0.6, h: 0.42, fontSize: 13, margin: 0 });
  s.addNotes("质量故事分三层讲，落点是一键自检：评审不需要相信我们，运行一条批处理就能复算全部数据。");
}

// ============================== S12 边界声明 ==============================
{
  const s = baseSlide(12, "HONESTY", "边界声明：诚实是本项目的默认设置");
  const rows = [
    ["纯软件，无硬件", "不测量、不声称测量任何真实芯片的功耗、温度、主频"],
    ["能耗是估算", "指令类别权重相对估算，仅用于优化前后趋势对比，界面与报告强制附免责声明"],
    ["TCG 不模拟缓存", "QEMU 为指令级模拟，缓存友好性只作静态提示，不作为实测指标"],
    ["主指标是动态指令数", "确定性、可复核；墙上时间受宿主负载影响，仅作参考值并标注"],
    ["结论是相对的", "限于同基准、同架构、同输入的对比，不外推为绝对性能"],
  ];
  rows.forEach((r, i) => {
    const y = 1.74 + i * 1.0;
    s.addShape(pres.shapes.RECTANGLE, { x: M, y: y + 0.08, w: 0.1, h: 0.1, fill: { color: CYAN } });
    s.addText(r[0], { x: M + 0.26, y, w: 3.35, h: 0.48, fontSize: 15.5, fontFace: ZH, bold: true, color: CYAN, margin: 0 });
    s.addText(r[1], { x: 4.15, y: y + 0.02, w: 8.6, h: 0.8, fontSize: 13, fontFace: ZH, color: TXT, lineSpacing: 20, margin: 0 });
    if (i < rows.length - 1) s.addShape(pres.shapes.LINE, { x: M, y: y + 0.82, w: CW, h: 0, line: { color: LINE, width: 0.75 } });
  });
  s.addNotes("主动讲边界反而是加分项：每条声明都对应一条评审可能的质疑，先把答案放在桌面上。");
}

// ============================== S13 结尾 ==============================
{
  const s = pres.addSlide();
  s.background = { color: BG };
  s.addShape(pres.shapes.OVAL, { x: -2.2, y: 4.6, w: 5.4, h: 5.4, fill: { type: "none" }, line: { color: "12244D", width: 1.25 } });
  s.addShape(pres.shapes.OVAL, { x: -1.1, y: 5.7, w: 3.2, h: 3.2, fill: { type: "none" }, line: { color: "1B2F5E", width: 1 } });
  s.addText("13  //  CLOSING", { x: M, y: 0.72, w: 6, h: 0.34, fontSize: 11.5, fontFace: MONO, color: CYAN, charSpacing: 3, margin: 0 });
  s.addText("不只让 AI 改代码，\n更让流水线证明它改对了。", { x: M, y: 1.35, w: 11.8, h: 1.9, fontSize: 40, fontFace: ZH, bold: true, color: TXT, lineSpacing: 58, margin: 0 });
  s.addShape(pres.shapes.RECTANGLE, { x: M, y: 3.55, w: 0.9, h: 0.055, fill: { color: CYAN }, shadow: glow(CYAN, 0.5) });
  s.addShape(pres.shapes.RECTANGLE, { x: M + 0.9, y: 3.55, w: 0.28, h: 0.055, fill: { color: VIOLET } });
  s.addShape(pres.shapes.LINE, { x: M + 1.35, y: 3.578, w: CW - 1.35, h: 0, line: { color: LINE, width: 0.75 } });
  const cols = [
    ["已交付", "全流程平台 · 10 基准实测数据包 · 166 测试全绿 · 一键自检脚本", CYAN],
    ["工程纪律", "M0~M7 里程碑制，每个里程碑有 DoD 与 git tag；AI 生成代码逐段审查后合入", VIOLET],
    ["路线展望", "TCG 插件级性能分析 · 更大规模基准集 · 教学场景适配", CYAN],
  ];
  cols.forEach((c, i) => {
    const x = M + i * 4.21;
    card(s, x, 4.05, 3.9, 1.85, PANEL);
    hud(s, x, 4.05, 3.9, 1.85, c[2]);
    s.addText(c[0], { x: x + 0.3, y: 4.3, w: 3.3, h: 0.4, fontSize: 15.5, fontFace: ZH, bold: true, color: c[2], margin: 0 });
    s.addText(c[1], { x: x + 0.3, y: 4.82, w: 3.35, h: 1.0, fontSize: 11.5, fontFace: ZH, color: MUT, lineSpacing: 17, margin: 0 });
  });
  s.addText("RV-Doctor · 每一条结论，都有指令级证据。", { x: M, y: 6.35, w: 11, h: 0.4, fontSize: 14, fontFace: ZH, bold: true, color: TXT, margin: 0 });
  s.addText("第二届 CIE 全国 RISC-V 高水平创新及应用大赛（高校赛道）· 成果提交", { x: M, y: 6.82, w: 11, h: 0.32, fontSize: 11, fontFace: ZH, color: MUT2, margin: 0 });
  s.addNotes("收尾回到主线口号。已交付讲存量，工程纪律讲过程可信度，展望讲演进空间。留问答时间。");
}

pres.writeFile({ fileName: path.join(__dirname, "RV-Doctor成果提交.pptx") }).then(() => console.log("PPTX written OK"));
