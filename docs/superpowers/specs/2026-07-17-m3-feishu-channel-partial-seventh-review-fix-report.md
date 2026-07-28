# 多租户 Agent 发布平台 — M3 第七轮 Review 修复报告

**日期：** 2026-07-17  
**关联 Review：** [2026-07-17-m3-feishu-channel-partial-code-seventh-review.md](./2026-07-17-m3-feishu-channel-partial-code-seventh-review.md)  
**状态：** 第七轮 Review 列出的 3 项 Spec P1 与 4 项 Spec P2 已完成代码侧修复和本地自动化核验。

---

## 1. 修复结论

| Finding | 结果 | 主要修复 | 回归证据 |
|---|---|---|---|
| P1：binding quiesce 与物理删除之间存在 lifecycle TOCTOU | 已修复 | Supervisor 在同一 ref-counted per-binding lifecycle lock 内执行 backlog 检查、runtime stop、二次检查和 owner-scoped row delete；路由按实际删除行擦除 secret。credential rotation 也进入同一临界区；startup 对 `list_active()` 快照重新查行 | DELETE 在 repository barrier 内暂停时，并发 start/rotation 均等待；删除提交后得到 not-found。startup 取得旧 active 快照后并发删除，也不会启动孤儿 runtime |
| P1：旧取消 create 会销毁后继已接管的同一 sandbox | 已修复 | 每个 backend create 分配 operation token，并记录 thread、sandbox、owner 与 use generation；late compensation 重新取得同一 thread/file lock，检查 active mapping、tracked info/warm pool 与 accepted-use generation 后才 destroy | A 取消后，B 注册并 accept 同一 sandbox，再放行 A；`destroy()` 未调用且 B 的 `get()` 有效。无后继时晚返回容量仍销毁 |
| P1：取消 OS file-lock waiter 会关闭 worker 正在使用的句柄 | 已修复 | file open + exclusive lock 合并成 shielded worker operation；取消只停止调用方等待，worker 最终取得的 handle 由专用 cleanup unlock + close | lock worker 被 barrier 阻塞时取消，句柄保持可用；原持有者释放后旧 waiter 完成清理，下一 live acquire 在短 deadline 内成功 |
| P2：create 超过 120 秒后丢失补偿所有权 | 已修复 | 120 秒改为告警 deadline，不再取消包装 `to_thread` 的 task；operation registry 与 late cleanup 一直保留到真实 backend call 返回；shutdown 显式记录未完成 operation | create 超过缩短后的 deadline 再成功，最终仍 destroy，operation registry 收敛为空 |
| P2：25 jobs / 10 秒不覆盖全局调度且 ready job 可饥饿 | 已修复 | global janitor 每轮只解析每个 JSON candidate 一次；deadline 从 discovery 前开始；全局最多选择 25 个 claimable jobs、最多 4 并发。active lease 在选取前排除，并用 file-locked persisted cursor 轮转 | 25 个 active producer 后的第 26 个 ready job 本轮完成；30 个跨 binding ready jobs 第一轮完成 25、第二轮完成 5；慢 discovery 计入总 deadline |
| P2：旧 health 快照可覆盖新 backlog | 已修复 | health 改为 local generation CAS；job write/transition/delete 在 file-locked mutation 中更新 durable generation。recovery 校验 discovery generation，并在 healthy 前重新从 durable store 投影 | 旧 recovery 暂停后持久化新 job，再放行旧 recovery；最终 `attachment_cleanup_healthy` 保持 false |
| P2：per-binding lock registry 无界增长 | 已修复 | keyed lock entry 记录 holder/waiter `users` 与 `retired`；物理删除只标记 retired，最后一个既有 waiter 退出后才删除 entry；shutdown 也退休空闲 entries | 删除期间排队 20 个 start waiter，只存在一个 lock entry；全部看到 not-found 后 registry 收敛为空 |

---

## 2. 最终运行时不变量

### 2.1 Binding 生命周期与删除

- start、stop、restart、credential rotation、runtime health/error 和 physical delete 按同一 `binding_id` 串行，不影响其他 binding。
- delete 只有两种结果：backlog 存在时返回 409 并保留 row/secret；或者 stop 完成、row 删除提交后才释放 lock，后继 lifecycle 只能看到 not-found。
- secret 删除使用 Supervisor 实际删除返回的 row；`load_active_bindings()` 获得 lock 后重新查询当前 row/status。
- deleted keyed lock 在 holder/waiter 全部退出后回收；retired entry 尚有 waiter 时不会创建第二把锁。

### 2.2 AIO create ownership 与 cancellation-safe locking

- backend create 从开始到真实 completion 始终有 operation token；120 秒只是告警，不代表底层 `to_thread` 已停止。
- 正常成功在注册 active sandbox 后结束 operation；backend/readiness 失败 destroy 后结束；取消把 operation 转交 late compensation。
- late compensation 在 thread lock 与 deterministic sandbox file lock 内检查 successor adoption/use generation；已接管时保留，无接管者时 destroy。
- file-lock worker 独占 open/lock handle。调用方取消后，由 worker completion callback 负责 unlock/close，没有并发 lock/close。

### 2.3 Bounded/fair cleanup 与 health

- global 10 秒 deadline 在 discovery 前建立；JSON candidate 在该 pass 中只解析一次。
- active producer、未过期 producer lease、active claim 和未过期 claim lease不占 25-job window。
- claimable jobs 通过 persisted cursor 轮转；每轮最多 25 jobs、最多 4 个 execution 并发，每个 acquire/delete 仍受剩余总预算和单调用 deadline 限制。
- per-binding recovery 使用同一 selector；执行阶段不为每个 job 重扫目录，只在最终可能 healthy 时做 durable projection。
- job write、phase transition、claim/release 和 final unlink 更新 file-locked store generation；healthy 只有在 local generation 未改变、discovery generation 稳定且最终 durable projection为空时提交。

---

## 3. 自动化验证

### 3.1 第七轮直接回归

执行 Supervisor、owner channel API、AIO provider、Feishu parser/cleanup、WebSocket lifecycle 与 Gateway service：

```text
121 passed, 5 warnings in 29.98s
```

覆盖 DELETE/start/rotation/startup barrier、lock registry 20 waiter、successor adoption fencing、超 deadline eventual destroy、OS file-lock cancellation、cleanup 公平/上限/discovery deadline，以及 health generation/CAS。

### 3.2 M3 聚焦集

按 `backend/CLAUDE.md` 执行 14 个 M3、legacy channel、attachment、sandbox、user-context 与 Gateway service 文件：

```text
324 passed, 8 skipped, 5 failed, 6 warnings in 64.46s
```

5 项失败与第五、六、七轮 Review 基线一致，均来自本轮未修改的 Windows LocalSandbox 路径：

```text
test_reverse_resolve_path_nested
test_execute_command_path_replacement
test_reverse_resolve_paths_in_output_supports_backslash_separator
test_read_file_reverse_resolves_local_paths_in_agent_written_files
test_write_then_read_roundtrip
```

其中 4 项为 Windows host path 反向映射/roundtrip，1 项要求本机不存在的 `/bin/sh`。本轮未修改 `local_sandbox.py`，不把它们判定为第七轮回归，也不声明 M3 Gate 全绿。

### 3.3 静态、格式、编译与差异检查

```text
ruff check --no-cache <12 changed Python files>: All checks passed!
ruff format --check --no-cache <12 changed Python files>: 12 files already formatted
python -m compileall <6 changed source files>: passed
git diff --check -- backend docs/superpowers/specs: passed
```

本轮没有数据库 migration。新增 generation 与 fairness cursor 位于现有 `${DEER_FLOW_HOME:-.deer-flow}/published-attachment-cleanup/` durable store，并使用 file lock。

### 3.4 全量 backend Gate

重新执行 `pytest tests -q`。运行超过 300 秒仍未生成最终汇总，随后已终止并确认没有残留 Python/pytest/uv 进程。本报告不声明全量 backend 通过。

---

## 4. 尚未关闭的非代码/非本轮 Gate

1. 修复或在 Linux CI 规避 5 项 Windows LocalSandbox 基线后重跑完整 M3 Gate。
2. 在可完成的 CI runner 上取得全量 backend `pytest tests -q` 最终汇总。
3. 真实 PostgreSQL 下验证 concurrent DELETE/start/rotation 和 health projection。
4. 两个真实 Feishu App 验证并行 WebSocket、凭据轮换与 backlog 409。
5. 真实远程 AIO/provisioner 验证 create cancellation、successor adoption 与超过 120 秒的 completion。
6. 至少两个 Gateway replicas 验证全局上限、公平 cursor、generation、producer/claim fencing 和进程 kill 后收敛。

第七轮 Standards 轴的 3 项 design smell 不是书面标准违规，本轮没有进行高风险的大规模模块拆分；cleanup store/coordinator 与 AIO lifecycle 聚合仍可作为后续架构重构任务，但不影响上述 7 个 Spec finding 的行为闭环。

---

## 5. 最终判定

**第七轮 Review 的 3 项 Spec P1 与 4 项 Spec P2：代码侧已关闭。**

直接回归全部通过；M3 聚焦集除 5 项既有 Windows LocalSandbox 基线外通过。由于全量 backend 无最终汇总、真实 PostgreSQL/Feishu/AIO/多副本 crash-recovery Gate 尚未执行，本报告不宣称最终 Ready to merge。
