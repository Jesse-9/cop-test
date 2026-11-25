---
title: 运动规划效率剩余工作 — P2/P3 与未覆盖运动段
type: plan
status: proposed
created: 2026-11-24
updated: 2026-11-25
scope: v1 运动规划 / 路径回放扩展 / 未实施
related:
  - ../../reports/simulation/v1-motion-planning-performance.md
  - ../../reports/simulation/v1-all-arm-path-reuse.md
  - ../../archive-plans/simulation/simulation-motion-planning-performance.md
  - ../../archive-plans/simulation/v1-all-arm-path-reuse-extension.md
---

# 运动规划效率剩余工作 — P2/P3 与未覆盖运动段

本方案只保留运动规划效率主题的**未实施**项。P0 计时字段、P1 固定路径
回放、五臂 `PLACING` 扩展和订单运行路径复用接入已实施；已有证据见
[P0/P1 报告](../../reports/simulation/v1-motion-planning-performance.md)、
[五臂扩展报告](../../reports/simulation/v1-all-arm-path-reuse.md)和
[订单运行路径复用报告](../../reports/order-orchestration/v1-order-run-path-reuse.md)。
完整原始方案已归档到
[运动规划完整方案](../../archive-plans/simulation/simulation-motion-planning-performance.md)。

## 1. 未覆盖运动段基线

**现状：**只采集了固定 `PLACING` 段；`PICKING`、`LIFTING`、
`TRANSFERRING`、home/retreat 等段和第二个动作没有同样的计时/回放闭环。
`PLACING` 规划墙钟在命中时下降，但 full_flow 总墙钟未下降。

**工作：**

1. 用同一固定场景和动作分别采集抓取、抬升、转运段的
   `search_wall_s`、`verify_wall_s`、IK、命令数和执行步数。
2. 判断每段是否具备可缓存性：起点/目标/载荷是否稳定、键是否可量化、
   执行期联锁是否保持不变。
3. 只有某段被证明可缓存且收益稳定，才扩展路径记录和回放策略；不把
   `PLACING` 的命中率推断为其他段也可命中。

**通过门槛：**每段有固定场景对照：`PLAN_ONLY` → `CACHE_ONLY` 同一目标
容差、无安全锁存、执行步数/命令数无退化；缓存未命中时明确回退或失败。

## 2. P2：按计时证据选择热点

**现状：**未开始；现有证据不足以证明全流程规划是主要瓶颈。

只有 P0/P1 数据显示在线规划仍是主要耗时时，才选择一个具体热点：

1. `search_wall_s` 占主导：检查固定动作失败前尝试了哪些策略，再评估
   优先尝试已知成功策略；不删掉失败兜底。
2. `verify_wall_s` 占主导：先确认是否对同一路径重复执行了多次最终复核；
   不通过减小碰撞采样密度解决。
3. IK 占主导：只针对固定目标测量候选 seed 是否反复求出相同解，再评估
   有限范围的 IK 结果缓存；动态目标不共用缓存。
4. 执行步数或仿真等待占主导：评估控制周期和测试等待，不用贝塞尔曲线
   掩盖问题。

每轮只改一个因素，用相同场景、起点、目标、故障设置重复对照，报告规划
成功率、路径终点误差、最小净距、规划墙钟、命令数和仿真执行时间。收益
不稳定或安全/终点指标退化时不合入。

## 3. P3：贝塞尔后处理（可选，未触发）

仅在 P0 显示需要改善自由空间轨迹平滑度时，针对一个静态、无接触、无载荷
的自由空间运动段做独立实验：

1. 从已通过在线规划的固定段导出 waypoint，保留段首、段尾和所有语义
   边界；不跨 `pick`/`lift`/`transfer`/`place`/`retreat` 拟合曲线。
2. 用三次贝塞尔拟合关节 waypoint 的中间形状，首版不自动调控制点、不加
   多阶配置。
3. 按不大于现有关节采样间隔取样，逐段交给
   `ConfigurationChecker.check_segment()`；检查机械臂、载荷/吸盘、腕部
   分支和净空，不只检查端点。
4. 检查 TCP 偏差、终点精度、关节限位、速度/加速度/jerk；任何失败放弃
   曲线，使用原始路径。
5. 只比较同一基准路径的原始/贝塞尔版本：路径长度/关节总变差、最大
   速度/加速度/jerk、执行时间、命令数和最小净距。
6. 一个离线单元测试覆盖端点保持和碰撞拒绝，再用一个固定场景实机动作
   确认；仅视觉更圆滑但指标无改善时不启用默认开关。

**结论门槛：**贝塞尔是可选后处理，不是“更快规划”的承诺；第一版保持
`TRAJECTORY_SMOOTHING_ENABLED=False`，只有单段实验全部通过后才讨论配置化。

## 4. 边界

- 不通过放宽安全容差、碰撞采样或执行联锁换命中率/速度。
- 不做跨工件路径迁移、托盘相对变换、路径平移或局部修复；这些需要单独的
  验证方案。
- 不把 `PLACING` 的 focused 结果、mock 前端请求或编译器测试写成物理
  可行性结论。
- 每轮独立提交、独立报告；没有计时证据时不调整采样/缓存/时间预算。