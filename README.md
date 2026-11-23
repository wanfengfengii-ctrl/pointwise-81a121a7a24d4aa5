# 轨旁振动采集与交班台

白班值班员登记线路换算规程。轨旁记录器提供原页、原槽和振动整数。采集员先保全原件，签存员按线路交付连续结果。次班质检凭成果号核对读值、出处和回执。

## 运行

使用 Python 3.11 标准库。从项目根目录执行完整回归和演示。`check_project.py` 由运行平台提供。

```text
python3 -B check_project.py
python3 -B window_trace.py demo
```

演示只在临时目录初始化工作库，读入 `samples/` 的固定数据，先执行原有的普通、拆分与交班流程，再按 READ_WINDOWS.md 展示单页读窗预约从实读到停源领取。普通线路将 7、11 按 3/2 换算成 21/2、33/2 微米每秒。拆分样例在 E9 早到时等待 E7，西路提交失败时东路仍签出 E7、E9。设备清退旧页并失答后，次班查实终局、关闭设备、补签 W11，并凭两路成果号完成交班复核。读窗样例中 A、B 两单共用一页读头，设备按 B、A 交错实读，站库实收后在停源会话按预约领回实际拍和原码，两槽按原线路序签存。

## Python 接口

`review.workbench.initialize_workspace(path)` 显式初始化 SQLite 库。重复初始化保留线路规程、原件、成果和复核。用 `with open_workbench(path) as desk:` 打开已初始化的工作库。每个会话使用自己的连接，离开上下文关闭连接。

1. `desk.lines.register(line, numerator, denominator)` 登记唯一线路及正倍率。`get_line(line)` 返回登记详情，未知线路抛出 `KeyError`。
2. `desk.feed.feed(PhysicalPage(page_id, line, slots))` 留存设备页。`RawSlot(SlotRef(page_id, coordinate), position, label, payload)` 中，页号与槽坐标共同标识原槽。`position` 是正的 SQLite 整数序位，页内按序位严格递增，同线路的设备序位唯一。标签只用于显示。页目录独立列出每槽的身份及原字节摘要。空页合法。停源、未知页或已清退页的原字节不可读。
3. `desk.intake.accept(page_id, coordinates=None)` 接管整页或指定的一组槽坐标，返回持久的 `VerifiedSlot`。一次请求整体提交，重投返回原接管事实，已接管原件可以在停源后重投。请求与槽证据身份不符、字节摘要不符或数值解码失败时抛出 `EvidenceMismatch`。原字节采用 `[+-]?[0-9]+`，零和负值有效。
4. `desk.splits.stop_source(line)` 在写入事务中固定实际末页并停写。`desk.splits.split(source, last_page, successors, assignments)` 固定一次拆分。后继为两个已登记、尚无设备写页或交班关系的线路。`SlotAssignment(ref, successor, position)` 指定末页槽的唯一归属和本路序位。每路旧尾的指定序位组成连续区间，后继设备序位从该区间之后开始。无旧尾的线路从 1 开始。漏槽、重复槽、外页槽、同路序位冲突或不符的末页抛出 `ValueError`。
5. `desk.retirement.retire(page_id)` 在保管不足、待查、已确认时分别返回 `RetirementState.INCOMPLETE`、`PENDING`、`CONFIRMED`。整页清退单独提交并以原页号保留终局。`desk.recovery.resume()` 查询并续办库内所有待查事项，返回按原页号索引的状态。设备当前不可查询时，已有待查事项保持待查。
6. `desk.publisher.publish(line, DeliverySegment(through, page_ids))` 请求从本路已签前缀续到含 `through` 的序位。`page_ids` 给出参与本段的本路设备页，清册旧尾按序位自动纳入，故西路可以请求 `DeliverySegment(1)`。同一页可以按序位分段签存。页号集合和截止序位组成确切请求，页号排列不影响重投身份。缺口抛出 `ContinuityGap`，仅参与已接管空页且无旧尾读值时返回 `None`。成果、回执和前缀同时提交，提交失败整体回滚。换算值使用 `Fraction`，量纲为微米每秒。
7. `desk.lookup.review(result_id)` 返回固定 `DeliveryResult`，包含按本路序位排列的读值、逐槽物理出处和回执。未知或未提交成果抛出 `ResultUnavailable`。`desk.shift.begin_batch(batch_id)` 开班，`register_item(batch_id, item_id, result_id, checklist)` 登记成果号和独立核对项，`record_review(batch_id, item_id)` 领取并记录实际出处及逐项差异，`get_batch(batch_id)` 读取历史批次。领取失败的项目仍待复核。

线路正常写入从序位 1 开始。允许按设备到达顺序接管，交付始终使用本路连续序位。原件保管与各路签存各自提交，已确认清退的原件仍可用于补签。首次签存成功后，按号领取和确切重投均使用固定历史，不受后续前缀推进影响。

窗口页的单页读窗预约入口（`feed_windowed`、`book`、`tick`、`read_terminal`、`collect`、`lookup_read`）见 READ_WINDOWS.md，普通流程不受影响。

确定性演练通过当前会话的设备替身进行。`desk.feed.paused_slots` 可暂停指定槽，`slot_replies` 可替换一次会话内对指定槽的传输内容。`desk.reclaimer.lose_reply` 与 `defer_next` 分别安排一次清退提交后失答和执行前失答。`desk.intake.fail_next` 和 `desk.publisher.fail_next` 分别按页号和线路号安排一次提交前失败。`desk.device.stop()` 暂停设备读写和查询，新会话可重新查询持久设备状态。正常数据都通过相同目录校验、接管、事务和复核入口。
