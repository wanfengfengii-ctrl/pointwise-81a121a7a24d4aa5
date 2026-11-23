# 轨旁单页读窗预约

A、B 共用所在页的读头。预约只承诺能在余拍内采到原槽。只有设备实际执行的那一拍才是采集出处。读头在站库未接件时照常运行，两侧沿用 README.md 的工作库和会话入口。

## 窗口页与设备预约

- `desk.feed.feed_windowed(page, windows)` 登记新窗口页，返回 `PageHeader`。`page` 沿用非空 `PhysicalPage`，`windows` 按槽坐标给出非空的正 SQLite 整数拍集合，每槽恰有一项。窗口可以不连续。原页、原序位、槽身份及目录沿用原约束。每页独立推进，拍从 1 到该页窗口的最大拍。`read_page`、`read_slot` 和 `desk.intake.accept` 对窗口页抛出 `PageUnavailable`，页目录仍可查。
- `desk.feed.book(ticket, ref)` 接收非空字符串预约号及窗口页的 `SlotRef`，返回 `"accepted"` 或 `"unavailable"`。只有余拍可同时兑现该页全部尚未实读的已接预约和新单时才返回 `"accepted"`。未读预约可改派。已经推进的拍都从余拍剔除，包括空拍。同号同槽重投返回其原接单结果，窗口和余拍均不重新占用。同号异槽或异号争用已接预约的槽抛出 `ValueError`。非法票号、普通页或未知槽也抛出 `ValueError`。`"unavailable"` 不占槽或读拍。
- `desk.feed.tick(page_id, beat)` 由设备逐拍调用，每拍最多实读一槽。每次推进后余拍仍须足以兑现全部尚未实读的已接预约。有多个合法分配时任选。实读返回预约号，空拍返回 `None`。重投已执行拍返回原结果，包括空拍。跳拍、超窗、非法拍或非窗口页抛出 `ValueError`。预约、拍进度及已发生读次在重开后继续有效。
- `desk.feed.read_terminal(ticket)` 从在线设备取已执行的实读终局，采用 `recorder.feed.WindowRead(ticket, slot, beat)`。`slot` 为现有 `RawSlot`，保留页号、坐标、标签、原序位和原字节。终局在首次实际读槽时固定。未知、未读或源件已清退时返回 `None`，停源沿用 `DeviceUnavailable`。读取终局可重投，不触发新的实读。

## 站库实收与交班

- `desk.intake.collect(ticket, terminal=None)` 按预约核收一份实际读件，成功返回 `WindowRead`。省略 `terminal` 时取设备终局。提供 `terminal` 时用同一个接口接收传来的候选件。候选件预约号与请求不符，或其预约、原槽、拍、原字节与可核对的终局冲突，抛出 `EvidenceMismatch`。已有站库实收时用原实收核对重投。无实收且暂时无可核对的设备实读时返回 `"pending"`。
- 站库既要核对预约实际读次，也要沿用槽目录与原码校验。原件与预约实收出处一起提交。`desk.intake.fail_next.add(page_id)` 仍注入一次 `CaptureCommitFailed`，失败后原件和预约实收均不新增，沿原读次重交可以完成实收。同件复领原实收，停源时也可复领已实收的预约。
- `desk.intake.lookup_read(ticket)` 返回站库已提交的 `WindowRead`，未知或尚未实收返回 `None`。重开后不访问设备也能领取。签存入口及前缀规则照旧。整页清退沿用原有逐槽保管齐备条件，未预约或未实收的槽仍阻止清退。

## 样例与运行

`samples/read_windows.json` 给出 RPA 来单次序、离散窗口、设备拍及原槽字节。两个预约都在第 1 拍前提出。设备应按 B、A 实读，站库可以先收 A 后收 B。期望停源领回 A 的原码 7 与第 2 拍、B 的原码 11 与第 1 拍。线路倍率为 3/2，签存明细按 A、B 排列，依次为 21/2、33/2 微米每秒。

`python3 -B window_trace.py fixture` 读取样例并核对页结构。新增入口接通后，`python3 -B window_trace.py trace` 可核验先预约后改拍、错件、写前失败续收及停源领取，输出实际实收、签存明细和原回执。核验失败以非零状态退出。现有 `.pointwise-runtime.json` 使用 CLI 运行清单，`setup` 是准备命令列表，`verify` 与 `demo` 是各自的参数数组。让 `verify` 运行原回归并核验预约，让 `demo` 展示预约从实读到停源领取的结果。
