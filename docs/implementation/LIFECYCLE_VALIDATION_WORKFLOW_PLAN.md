# 生命周期校验流程调整方案

- 状态：已实施（2026-10-26）
- 日期：2026-10-26
- 范围：减少生命周期 CLI/MCP 与阶段运行入口中的重复手动校验步骤，同时保留启动前的完整校验

## 结论

允许加载工件后直接请求启动，不要求用户先单独执行 validate。start 必须继续在取得运行锁后执行完整只读工件校验，校验失败则拒绝启动。validate 命令和 MCP 工具保留，作为用户主动检查和查看详细差异报告的入口。

构建流程中，build 继续执行该阶段现有的构建契约检查；构建本身没有工件 manifest，不能声称完成了与已保存工件比较的全量校验。若要得到完整工件校验结果，应 build → save：save 会保存、回读并校验工件。临时构建场景不保存时，启动前继续执行 scene.verify() 契约检查。connect 只建立 ZMQ 连接，不承担场景校验。

因此，日常已保存工件的操作目标是 connect → load → start，而不是删掉 start 内部的校验；首次/显式构建的目标是 connect → build → save → start。单独调用 validate 仍可用于检查或诊断，属于可选的用户操作。

## 实施前的实现与重复工作（2026-10-26 前）

- LifecycleManager.load() 检查 manifest schema、场景文件存在性及其 SHA-256，并恢复场景句柄；成功后状态为 SCENE_LOADED。
- LifecycleManager.validate() 对已加载工件执行 manifest、对象、配置、模型及阶段扩展契约的只读比较；成功后状态为 SCENE_VALIDATED。
- 状态机当时只允许从 SCENE_VALIDATED 执行 start，因而强迫 CLI/MCP 调用方先显式 validate。
- LifecycleManager.prepare_start() 无论此前是否已校验，都会先取得运行锁，再对工件执行完整校验；临时构建场景则重新执行构建契约检查。
- 阶段一运行入口当时 load → validate → prepare_start，存在两次连续的完整工件校验。阶段二/三入口使用各自 runner 检查工件后也调用同一生命周期启动准备流程，需以源码确认其是否同样重复，调整时不改变独立 runner 的运行契约。
- LifecycleManager.save() 写入工件后会重新加载和校验，并以 SCENE_VALIDATED 状态返回。随后 prepare_start() 再校验一次；计划第一轮仍保留这次启动前校验，不引入校验缓存或“刚保存可信”的旁路。

## 目标行为

| 当前场景 | 调用 | 目标结果 |
|---|---|---|
| 已连接，无场景 | start | 拒绝，提示先 build 或 load |
| 刚 load 的工件（SCENE_LOADED） | start | 取运行锁、全量校验；通过才进入运行，失败进入 ERROR 并释放锁 |
| 已显式 validate 的工件（SCENE_VALIDATED） | start | 仍取运行锁并在启动点校验；通过才运行 |
| build 后未保存的场景 | start | 取运行锁并重新执行构建契约检查；通过才运行 |
| build → save 后的工件 | start | 取运行锁并执行启动点全量校验；通过才运行 |
| 校验失败 | 任意 start | 拒绝启动，保留验证报告/错误信息，释放本次运行锁 |

显式 validate 成功后状态仍表示场景可运行，但它不构成跳过启动点检查的许可。场景可能在显式校验后发生变化，且其他进程状态会变化；启动点校验和锁顺序是现有完整性与并发保证，应保留。

## 实施步骤

1. 状态机允许 SCENE_LOADED + start 转换，但仅供 prepare_start() 完成锁内全量校验后提交状态转换。不能让状态机转换本身绕过 prepare_start()。
2. 更新 allowed_actions()，使已加载场景显示 start 可用；继续显示可选的 validate。无场景的 CONNECTED_STOPPED 仍不可启动。
3. 更新生命周期 CLI 的 start 帮助和结果语义，支持 load current → start；直接调用 start 时仍拒绝未加载场景。
4. 阶段 runner：移除阶段一 load() 后紧邻 prepare_start() 的冗余 manager.validate()；让 prepare_start() 成为本次启动唯一的完整校验入口。阶段二/三只移除经源码确认的重复前置校验，不改变其阶段专属契约验证或失败处理。
5. MCP：start_simulation 对 SCENE_LOADED 场景直接进入现有 prepare_start()，由该方法校验并在失败时返回现有错误结构。工具文档的工作流从 load → validate → start 更新为 load → start，并说明 validate_scene 是可选诊断工具。
6. 文档：同步更新 CLI/MCP 生命周期说明、项目使用指南、场景工件说明及对应实施记录。明确 build 的契约校验与工件 manifest 全量校验不是同一个动作。

## 验收

- 状态机允许 SCENE_LOADED → start，但不允许没有场景时启动，也不允许仿真运行中执行 validate/build/load/save/第二次 start。
- 对已加载工件直接 prepare_start() 时，测试确认验证发生在运行锁获取之后；验证失败时不启动、运行锁释放、状态和报告反映失败。
- 显式 validate 仍可产生完整报告；之后调用 start 仍执行启动点校验。
- 临时构建场景的启动仍执行构建契约复核；不把无 manifest 的场景报告成工件级全量验证通过。
- CLI 可执行 load current → start；MCP 可在 load 后直接 start，并仍能返回校验失败详情。
- 阶段一、二、三 runner 均不在 prepare_start() 前重复执行相同的完整校验；阶段特有检查保持有效。
- 更新状态机、lifecycle manager、CLI/MCP 工作流测试；运行受影响的生命周期与阶段入口测试。

## 明确不在本方案范围内

- 不移除 validate 命令、MCP 工具或详细校验报告。
- 不从 connect 自动加载场景，也不让 connect 隐式校验。
- 不让 build 虚构 manifest 或生成临时工件，只为把工件校验器搬到构建路径。
- 不移除 prepare_start() 中持有运行锁后的完整工件校验。
- 不增加校验缓存、有效期、信任令牌、配置开关或自动修复。
- 不改变 start 不隐式构建/清场、工件保护、阶段父子关系和校验失败不得启动的契约。

## 实施记录（2026-10-26）

| 方案步骤 | 落地内容 |
|---|---|
| 1. 状态机 | `_TRANSITIONS` 新增 `(SCENE_LOADED, "start") → SIMULATION_RUNNING`；状态转换仍只由 `prepare_start()` 在锁内校验通过后提交 |
| 2. `allowed_actions()` | 已加载工件（`SCENE_LOADED`）显示 `start`，并继续显示可选的 `validate`；无场景的 `CONNECTED_STOPPED` 仍不显示 `start` |
| 3. CLI | `start` 文案改为“取运行锁后执行启动点全量校验”；`load` 文案说明加载后可直接 start；`validate` 文案标注为可选诊断。`load` 返回字段 `requires_validation` 改为 `validation_at_start` |
| 4. 阶段 runner | `stage1.run.main()`、`stage2.run.main()`、`stage3.run.main()` 均移除 load 后紧邻的 `manager.validate()`；阶段专属契约仍通过 `prepare_start()` 的 `extra_validator` 校验，runner 的阶段检查与失败处理不变 |
| 5. MCP | `start_simulation` 对 `SCENE_LOADED` 直接进入 `prepare_start()`；错误提示与工具文档改为 load → start，明确 `validate_scene` 为可选诊断；`load_scene` 返回值同步改为 `validation_at_start` |
| 6. 文档 | 更新 USAGE、MCP_USAGE、场景工件说明、06-08、工程参考、README 与本记录；明确 `build` 只有构建契约、无 manifest，`save` 仍要求 `SCENE_VALIDATED` |

验证：

- 状态机测试覆盖 `SCENE_LOADED → start` 允许、`CONNECTED_STOPPED`/`DISCONNECTED`
  启动拒绝、运行中动作拒绝。
- `LifecycleManager` 测试覆盖 load 后直接 `prepare_start()`：校验发生在
  运行锁获取之后；校验失败进入 `ERROR` 并释放锁；显式 validate 后 start
  仍重新校验；临时构建场景仍执行 `scene.verify()`。
- CLI 帮助与 MCP 工具文档测试同步更新；另覆盖 load 与 start 分属不同
  manager（模拟 CLI 每条命令独立进程）时的会话恢复与锁内校验。
- 全量离线测试：`428 passed, 3 skipped`。
- 未修改任何场景工件、manifest schema 或受保护基线，未新增/删除工件版本。

