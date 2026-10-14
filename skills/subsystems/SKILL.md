---
name: subsystems
description: "面向 AI Agent 与人机协同的子系统（Subsystems）架构设计、世界模型压缩与防腐化治理技能。当用户提到“设计子系统”、“梳理模块架构”、“维护 subsystem”、“规范架构文档”、“检查代码与契约一致性”、“沉淀架构决策笔记”或需要基于 docs/subsystems 组织大型代码库时触发此技能。"
---

# Subsystems 架构治理与世界模型管理技能

本技能是一套**完全通用、技术栈无关、面向 Agent-Native 的软件架构治理引擎**。
它的核心目标是解决：
> **`Repo Information ≫ Working Context`**  
> （代码库信息量极大，而 AI Agent 的上下文窗口有限且昂贵）

通过在项目中建立和维护一组**“高压缩比的世界模型切片（docs/subsystems/*.md）”**，使任何进场的 Coding Agent 都能瞬间获得系统 80% 的关键心智模型，消除盲目试错与破坏全局契约的“垃圾代码（Slop Code）”。

---

## 核心认知分层模型 (Externalized Cognitive System)

在项目中，任何信息应清晰归位到以下 6 层：
1. **Code (代码)**：当前机器执行的物理状态。
2. **Subsystems (子系统契约)**：当前世界模型的压缩表达（职责、事实源、运行时路径、状态约束）。
3. **Agent Notes (决策沉淀)**：为什么世界是这样，以及放弃了什么（Why & What we gave up）。
4. **Tests & Invariants (不变量与测试)**：哪些系统状态绝对不允许出现。
5. **Skills (技能与 SOP)**：如何重复完成某类工程操作。
6. **Git History (历史轨迹)**：状态演化的原始记录。

---

## 核心工作流指令

### 1. `/subsystem init <name>` — 自动挖掘并压缩子系统世界模型
当需要为某个模块或跨包能力建立子系统文档时触发：

* **Step 1: 访谈代码库，而非审问人类（Interview the repo, not the user）**
  * 自动扫描目标能力的跨文件/跨包实现，提取：
    * **Surface**：对外暴露的入口（API、CLI、RPC、UI 挂载点）；
    * **Store**：状态持久化位置（DB 表模型、配置项、内存池）；
    * **Engine**：核心执行与转换逻辑；
    * **External**：第三方系统依赖。
* **Step 2: 填充标准 6 要素骨架**
  * 参照 [`references/subsystem-template.md`](references/subsystem-template.md) 生成 `docs/subsystems/<name>.md`。
  * 强制要求：所有“职责与事实源”必须精准链接到具体代码文件及符号（类、函数、结构体），严禁粘贴大段易变实现源码。
* **Step 3: 交付前自我实证（Prove before delivery）**
  * Agent 必须在后台**亲自运行一次文档第 6 节的 `Verification` 命令**。
  * 只有测试命令真实跑通，方可完成交付；绝不交付未经实测的虚假测试命令。

---

### 2. `/subsystem doctor [name]` — 轻量级只读架构体检
在对某个子系统进行任何重构或业务修改之前，先花数秒做一次只读体检：
* 验证子系统文档中引用的**文件路径和符号**是否真实存在（无断链）；
* 检查各模块间是否存在违规的逆向依赖或循环依赖（Cyclic Dependencies）；
* 检查所需的依赖项、环境变量与数据底座是否就绪。

---

### 3. `/subsystem audit` — 代码变更与契约一致性审查
在完成代码修改后、提交 PR 或 Commit 之前触发：
* **读取当前 `git diff`**，自动识别波及到的子系统；
* **核查 `Code_t ↔ Docs_t` 同步法则**：
  * 是否修改了公共接口、状态流转、运行时路径或异常行为？若是，检查对应的 `docs/subsystems/*.md` 是否已同步修改。
* **三权分立分诊（Triage）**：
  * **Doc Drift（文档滞后）**：功能是预期升级，文档未跟上 $\rightarrow$ 补全更新文档；
  * **Verification Gap（测试缺陷）**：功能完备但验证命令失效 $\rightarrow$ 修正测试命令；
  * **Architecture Regression（架构退化）**：代码改动破坏了子系统文档中的 `Invariants（核心不变量）` $\rightarrow$ **坚决阻断合并，勒令重写代码！**

---

### 4. `/subsystem note <decision>` — 架构决策沉淀 (Why & What we gave up)
在解决一个棘手问题、做出架构取舍或排除踩坑后触发：
* 将决策理由提炼并追加至对应子系统的 `## 5. 架构决策笔记 (Agent Notes)` 章节；
* 必须明确记录：
  * **选择理由**：当前选型的考量；
  * **被放弃的直觉做法（Anti-patterns）**：警告未来的 Agent“不要尝试把 X 改成 Y，因为早期证实过会导致 Z 故障”。

---

### 5. `/subsystem verify [name]` — 闭环验证执行
* 从对应的 `docs/subsystems/<name>.md` 中提取第 6 节的测试命令；
* 直接调用终端命令执行并汇报结果，确保本次改动 100% 保持绿色。

---

## 核心治理铁律 (Inviolable Rules)

1. **`Code_t ↔ Docs_t` 强一致**：改动了行为或接口，必须在同一个 PR/Commit 提交子系统契约更新。
2. **单一事实源与符号链接**：文档中只链接代码符号名，绝不贴实现代码。
3. **严禁弱化不变量**：任何人或 Agent 严禁为了使测试通过而擅自删改、放宽子系统的 `Invariants`。
