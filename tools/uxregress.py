r"""用户体验回归：验证高风险的可恢复操作和页面接线。

用法：
    .venv\Scripts\python.exe tools\uxregress.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "uxregress"
os.environ["PAWPET_HOME"] = str(SCRATCH)
os.environ["PAWPET_INSTANCE_SUFFIX"] = "uxregress"

PASSED = 0
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED.append(f"{label} {detail}".strip())
        print(f"  [XX] {label} {detail}")


def main() -> int:
    print("小爪用户体验回归\n")
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, Qt, QUrl
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtQml import QQmlComponent, QQmlEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication

    from pawpet.backend import Backend
    from pawpet.config import QML_DIR
    from pawpet.store import Store

    app = QApplication.instance() or QApplication(sys.argv[:1])
    store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
    store.load()
    backend = Backend(store)

    print("=== 一、误删可恢复 ===")
    backend.tasks.add("可撤销待办", 1)
    task = store.tasks[-1]
    task_id = task["id"]
    backend.tasks.remove(task_id)
    check("删除后出现撤销状态", backend.tasks.canUndo)
    check("撤销提示保留原文", backend.tasks.lastRemovedText == "可撤销待办",
          backend.tasks.lastRemovedText)
    backend.tasks.undoRemove()
    restored = next((item for item in store.tasks if item.get("id") == task_id), None)
    check("撤销后恢复原待办", restored is not None)
    check("撤销后保留优先级", restored is not None and restored.get("priority") == 1)
    backend.tasks.remove(task_id)
    backend.tasks.add("新待办会清掉旧撤销", 0)
    check("产生新操作后清除旧撤销", not backend.tasks.canUndo)

    print("\n=== 二、搜索结果跟随改名 ===")
    backend.tasks.add("搜索中的旧标题", 0)
    renamed_task_id = store.tasks[-1]["id"]
    backend.tasks.searchText = "搜索中的"
    check("改名前搜索能命中", backend.tasks.count == 1,
          str(backend.tasks.count))
    backend.tasks.rename(renamed_task_id, "已经改过的新标题")
    check("改名后不再错误留在旧搜索结果", backend.tasks.count == 0,
          str(backend.tasks.count))
    backend.tasks.searchText = ""
    backend.tasks.remove(renamed_task_id)

    print("\n=== 三、智能排序跟随完成状态 ===")
    backend.tasks.showDone = True
    backend.tasks.sortMode = "smart"
    backend.tasks.add("较早的排序任务", 0)
    older_task_id = store.tasks[-1]["id"]
    import time
    time.sleep(0.01)
    backend.tasks.add("较晚的排序任务", 0)
    newer_task_id = store.tasks[-1]["id"]
    task_id_role = Qt.UserRole + 1

    def visible_task_ids() -> list[str]:
        return [
            backend.tasks.data(backend.tasks.index(index, 0), task_id_role)
            for index in range(backend.tasks.rowCount())
        ]

    check("智能排序把较新的未完成任务放前面",
          visible_task_ids()[0] == newer_task_id,
          str(visible_task_ids()[:2]))
    backend.tasks.toggle(newer_task_id)
    check("完成后智能排序把未完成任务移到前面",
          visible_task_ids()[0] == older_task_id,
          str(visible_task_ids()[:2]))
    backend.tasks.remove(older_task_id)
    backend.tasks.remove(newer_task_id)
    backend.tasks.showDone = False

    print("\n=== 四、便签自动保存保持当前项 ===")
    backend.notes.add("较旧便签", "旧内容")
    older_note_id = backend.notes.currentId
    import time
    time.sleep(0.01)
    backend.notes.add("较新便签", "新内容")
    newer_note_id = backend.notes.currentId
    older_index = next(
        index for index in range(backend.notes.rowCount())
        if backend.notes.data(
            backend.notes.index(index, 0), Qt.UserRole + 1
        ) == older_note_id
    )
    backend.notes.currentIndex = older_index
    backend.saveNote(older_note_id, "较旧便签已编辑", "自动保存后的内容")
    check("便签保存后仍选中原便签",
          backend.notes.currentId == older_note_id,
          backend.notes.currentId)
    check("便签保存后内容仍在当前编辑项",
          backend.notes.currentText == "自动保存后的内容",
          backend.notes.currentText)
    backend.notes.remove(older_note_id)
    backend.notes.remove(newer_note_id)

    print("\n=== 五、间隔提醒编辑数据 ===")
    backend.reminders.add("每隔提醒", "", "interval", 45)
    reminder = next(item for item in store.reminders if item["title"] == "每隔提醒")
    reminder_role = Qt.UserRole + 1
    reminder_index = next(
        index for index in range(backend.reminders.rowCount())
        if backend.reminders.data(
            backend.reminders.index(index, 0), reminder_role
        ) == reminder["id"]
    )
    every_role = next(
        role for role, name in backend.reminders.roleNames().items()
        if bytes(name) == b"every"
    )
    every = backend.reminders.data(
        backend.reminders.index(reminder_index, 0), every_role
    )
    check("提醒模型暴露 every 字段", every == 45, str(every))
    reminder_id = reminder["id"]
    backend.reminders.remove(reminder_id)
    check("删除提醒后出现撤销状态", backend.reminders.canUndo)
    check("撤销提示保留提醒标题",
          backend.reminders.lastRemovedTitle == "每隔提醒",
          backend.reminders.lastRemovedTitle)
    backend.reminders.undoRemove()
    restored_reminder = next(
        (item for item in store.reminders if item.get("id") == reminder_id),
        None,
    )
    check("撤销后恢复提醒", restored_reminder is not None)
    backend.reminders.remove(reminder_id)
    backend.reminders.add("新提醒会清掉旧撤销", "09:00", "daily", 0)
    check("产生新提醒操作后清除旧撤销", not backend.reminders.canUndo)

    print("\n=== 六、页面接线防回归 ===")
    notes = (QML_DIR / "PawPet" / "page" / "NotesPage.qml").read_text(
        encoding="utf-8"
    )
    ai_page = (QML_DIR / "PawPet" / "page" / "AiPage.qml").read_text(
        encoding="utf-8"
    )
    ai_settings = (QML_DIR / "PawPet" / "AiSettingsPanel.qml").read_text(
        encoding="utf-8"
    )
    ai_history = (QML_DIR / "PawPet" / "AiHistoryPanel.qml").read_text(
        encoding="utf-8"
    )
    reminders = (QML_DIR / "PawPet" / "page" / "RemindersPage.qml").read_text(
        encoding="utf-8"
    )
    today = (QML_DIR / "PawPet" / "page" / "TodayPage.qml").read_text(
        encoding="utf-8"
    )
    tasks = (QML_DIR / "PawPet" / "page" / "TasksPage.qml").read_text(
        encoding="utf-8"
    )
    focus = (QML_DIR / "PawPet" / "page" / "FocusPage.qml").read_text(
        encoding="utf-8"
    )
    dashboard = (QML_DIR / "PawPet" / "Dashboard.qml").read_text(
        encoding="utf-8"
    )
    settings = (QML_DIR / "PawPet" / "page" / "SettingsPage.qml").read_text(
        encoding="utf-8"
    )
    check("便签切换前会立即保存", "page.flushSave()" in notes)
    check("便签切换会停止旧保存计时器", "saveTimer.stop()" in notes)
    check("便签支持 Ctrl+S", 'sequence: "Ctrl+S"' in notes)
    check("便签支持 Ctrl+N 新建", 'sequence: "Ctrl+N"' in notes
          and "page.createNote()" in notes)
    check("便签删除有撤销入口", "backend.notes.undoRemove()" in notes)
    check("待办页有搜索框", "taskSearchField" in tasks and
          "backend.tasks.searchText = text" in tasks)
    check("待办搜索支持一键清空", "clearTaskSearchButton" in tasks
          and "tooltipText: \"清空搜索\"" in tasks)
    check("待办搜索支持 Esc 清空", "Qt.Key_Escape" in tasks
          and "clear()" in tasks)
    check("今日页有直接开始专注入口",
          "todayFocusAction" in today and "backend.focus.toggle()" in today)
    check("今日页有直接进入待办入口",
          "todayTasksAction" in today and "backend.showDashboard(\"tasks\")" in today)
    check("待办改名失焦会保存", "onEditingFinished" in tasks
          and "page.saveEdit(model.taskId, text)" in tasks)
    check("待办编辑按 Esc 会取消而不是保存",
          "cancellingEdit" in tasks and "event.accepted = true" in tasks)
    check("提醒编辑带回原间隔", "model.every" in reminders)
    check("提醒编辑关闭会清理旧状态", "onClosed" in reminders
          and "page.editingId = \"\"" in reminders)
    check("提醒删除有撤销入口", "backend.reminders.undoRemove()" in reminders)
    check("提醒删除会显示撤销条", 'objectName: "reminderUndoBar"' in reminders)
    check("提醒页面暴露时间校验控件",
          "remindersPage" in reminders and "reminderTimeField" in reminders)
    check("待办页有撤销入口", "backend.tasks.undoRemove()" in tasks)
    check("待办创建后仍可调整优先级",
          "taskPriorityMenu" in tasks
          and "backend.tasks.setPriority(priorityMenu.taskId" in tasks
          and 'tooltipText: "设置优先级"' in tasks)
    check("清除已完成待办接确认框",
          "clearDoneDialog" in tasks
          and "backend.tasks.clearDone()" in tasks)
    check("专注页可关联待办",
          "focusTaskBox" in focus
          and "backend.focusTaskOptions" in focus
          and "backend.focus.taskId" in focus)
    check("专注计时中锁定待办选择",
          "enabled: !backend.focus.running" in focus)
    check("专注完成提示关联任务番茄",
          "takePendingTaskPomodoro" in (QML_DIR / ".." / "backend.py").read_text(
              encoding="utf-8"
          ) and "+1 个番茄" in focus)
    check("AI 历史删除接确认框",
          "confirmDeleteHistory" in ai_page
          and "historyConfirmDialog" in ai_page)
    check("AI 历史清空接确认框",
          "confirmClearHistory" in ai_page
          and "backend.ai.clearHistory()" in ai_page)
    check("AI 记忆清空接确认框",
          "aiClearMemory()" in ai_settings
          and "clearConfirmDialog" in ai_settings)
    check("AI 知识库清空接确认框",
          "aiClearKnowledge()" in ai_settings
          and "clearConfirmDialog" in ai_settings)
    check("AI 历史仍通过面板发起删除",
          "deleteConversationRequested" in ai_history)
    check("工作台有七个键盘导航快捷键",
          sum(f'Alt+{index}' in dashboard for index in range(1, 8)) == 7)
    check("侧栏导航支持键盘聚焦与激活",
          "activeFocusOnTab: true" in dashboard
          and "Keys.onPressed" in dashboard
          and "Qt.Key_Space" in dashboard)
    paw_button = (QML_DIR / "PawPet" / "PawButton.qml").read_text(
        encoding="utf-8"
    )
    check("图标按钮支持悬停提示", "property string tooltipText" in paw_button
          and "ToolTip.visible" in paw_button)
    check("高风险图标操作有明确提示",
           'tooltipText: "删除待办"' in tasks
           and 'tooltipText: "删除提醒"' in reminders)
    check("快捷键页显示真实注册结果",
          "hotkeyRegistrationStatus" in settings
          and "修改后需要重启才生效" in settings)

    print("\n=== 七、真实页面联调 ===")
    QQuickStyle.setStyle("Basic")
    engine = QQmlEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("backend", backend)
    component = QQmlComponent(
        engine,
        QUrl.fromLocalFile(str(QML_DIR / "PawPet" / "Main.qml")),
    )
    root = component.create(engine.rootContext())
    check("工作台真实页面可加载", root is not None)
    if root is not None:
        def pump(milliseconds: int) -> None:
            import time

            deadline = time.time() + milliseconds / 1000
            while time.time() < deadline:
                app.processEvents()
                time.sleep(0.008)

        backend.showDashboard("notes")
        pump(350)
        notes_page = root.findChild(QObject, "notesPage", Qt.FindChildrenRecursively)
        title_field = root.findChild(
            QObject, "noteTitleField", Qt.FindChildrenRecursively
        )
        check("真实便签控件存在", notes_page is not None and title_field is not None)
        if notes_page is not None and title_field is not None:
            note_id = backend.notes.currentId
            title_field.setProperty("text", "页面联调保存")
            pump(40)
            invoked = QMetaObject.invokeMethod(notes_page, "flushSave")
            pump(80)
            check("切换前立即保存真实便签", invoked and
                  backend.notes.titleOf(note_id) == "页面联调保存",
                  backend.notes.titleOf(note_id))

            title_field.setProperty("text", "退出前同步保存")
            # 模拟托盘退出：自动保存计时器还没到点时，应用会先发这个
            # 信号，让 QML 把当前编辑框内容立即提交到 Store。
            backend.shutdownRequested.emit()
            pump(40)
            check("退出前同步保存真实便签",
                  backend.notes.titleOf(note_id) == "退出前同步保存",
                  backend.notes.titleOf(note_id))

            backend.newNote()
            note_to_delete = backend.notes.currentId
            backend.saveNote(note_to_delete, "页面便签撤销", "撤销正文")
            backend.deleteNote(note_to_delete)
            pump(80)
            note_undo_bar = root.findChild(
                QObject, "noteUndoBar", Qt.FindChildrenRecursively
            )
            check("真实便签删除后显示撤销条", note_undo_bar is not None and
                  bool(note_undo_bar.property("visible")))
            backend.notes.undoRemove()
            pump(50)
            check("真实页面撤销后恢复便签",
                  any(item.get("id") == note_to_delete for item in store.notes))

        backend.showDashboard("tasks")
        pump(250)
        tasks_page = root.findChild(QObject, "tasksPage", Qt.FindChildrenRecursively)
        check("真实待办页面有可定位的编辑区域", tasks_page is not None)
        backend.tasks.add("Esc 取消测试", 0)
        esc_task_id = store.tasks[-1]["id"]
        if tasks_page is not None:
            tasks_page.setProperty("editingId", esc_task_id)
            pump(80)
            task_list = root.findChild(QObject, "taskList", Qt.FindChildrenRecursively)
            edit_field = None
            content_item = (
                task_list.property("contentItem")
                if task_list is not None else None
            )

            def find_quick_item(item, object_name):
                if item is None:
                    return None
                if item.objectName() == object_name:
                    return item
                for child in item.childItems():
                    found = find_quick_item(child, object_name)
                    if found is not None:
                        return found
                return None

            if content_item is not None:
                edit_field = find_quick_item(
                    content_item, "taskEditField_" + esc_task_id)
            check("真实待办编辑框能打开", edit_field is not None)
            if edit_field is not None:
                edit_field.setProperty("text", "不应保存的改名")
                pump(30)
                QCoreApplication.sendEvent(
                    edit_field,
                    QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier),
                )
                QCoreApplication.sendEvent(
                    edit_field,
                    QKeyEvent(QEvent.KeyRelease, Qt.Key_Escape, Qt.NoModifier),
                )
                pump(80)
                saved_esc_task = next(
                    (item for item in store.tasks
                     if item.get("id") == esc_task_id),
                    None,
                )
                check("真实按 Esc 后退出编辑", tasks_page.property("editingId") == "")
                check("真实按 Esc 不保存编辑内容",
                      saved_esc_task is not None
                      and saved_esc_task.get("text") == "Esc 取消测试",
                      str(saved_esc_task))
        backend.tasks.remove(esc_task_id)
        clear_search_button = root.findChild(
            QObject, "clearTaskSearchButton", Qt.FindChildrenRecursively
        )
        check("真实待办搜索有清空按钮", clear_search_button is not None)
        backend.tasks.add("页面优先级调整", 0)
        priority_task_id = store.tasks[-1]["id"]
        pump(120)
        priority_menu = root.findChild(
            QObject, "taskPriorityMenu", Qt.FindChildrenRecursively
        )
        opened_priority = (QMetaObject.invokeMethod(priority_menu, "open")
                           if priority_menu is not None else False)
        pump(50)
        check("真实待办优先级菜单已加载",
              priority_menu is not None)
        check("真实待办优先级菜单能打开",
              opened_priority and priority_menu is not None
              and bool(priority_menu.property("visible")))
        if priority_menu is not None and bool(priority_menu.property("visible")):
            QMetaObject.invokeMethod(priority_menu, "close")
            pump(30)
        backend.tasks.setPriority(priority_task_id, 2)
        pump(50)
        priority_task = next(
            (item for item in store.tasks if item.get("id") == priority_task_id),
            None,
        )
        check("优先级调整会落盘",
              priority_task is not None and priority_task.get("priority") == 2)
        backend.tasks.remove(priority_task_id)
        backend.tasks.add("页面撤销测试", 0)
        live_task_id = store.tasks[-1]["id"]
        backend.tasks.remove(live_task_id)
        pump(80)
        undo_bar = root.findChild(QObject, "taskUndoBar", Qt.FindChildrenRecursively)
        check("真实待办删除后显示撤销条", undo_bar is not None and
              bool(undo_bar.property("visible")))
        backend.tasks.undoRemove()
        pump(50)
        check("真实页面撤销后恢复待办",
              any(item.get("id") == live_task_id for item in store.tasks))
        clear_done_confirm = root.findChild(
            QObject, "clearDoneConfirmDialog", Qt.FindChildrenRecursively
        )
        check("真实清除已完成确认框存在", clear_done_confirm is not None)
        backend.tasks.add("确认框取消测试", 0)
        confirm_task_id = store.tasks[-1]["id"]
        backend.tasks.toggle(confirm_task_id)
        done_before_confirm = backend.tasks.doneCount
        cancel_button = (clear_done_confirm.findChild(
            QObject, "confirmCancelButton", Qt.FindChildrenRecursively
        ) if clear_done_confirm is not None else None)
        opened = (QMetaObject.invokeMethod(clear_done_confirm, "open")
                  if clear_done_confirm is not None else False)
        pump(50)
        canceled = (QMetaObject.invokeMethod(cancel_button, "click")
                    if cancel_button is not None else False)
        pump(50)
        check("清除已完成确认框可打开并取消",
              opened and canceled
              and clear_done_confirm is not None
              and not bool(clear_done_confirm.property("visible"))
              and backend.tasks.doneCount == done_before_confirm,
              f"opened={opened}, canceled={canceled}, done={backend.tasks.doneCount}")
        backend.tasks.remove(confirm_task_id)

        backend.tasks.add("only search result", 0)
        search_field = root.findChild(
            QObject, "taskSearchField", Qt.FindChildrenRecursively
        )
        check("真实待办搜索控件存在", search_field is not None)
        if search_field is not None:
            search_field.setProperty("text", "only search result")
            pump(80)
            check("真实待办搜索会过滤列表", backend.tasks.count == 1,
                  str(backend.tasks.count))
            search_field.setProperty("text", "")
            pump(80)

        backend.tasks.add("页面专注关联", 1)
        focus_task_id = store.tasks[-1]["id"]
        backend.showDashboard("focus")
        pump(250)
        focus_task_box = root.findChild(
            QObject, "focusTaskBox", Qt.FindChildrenRecursively
        )
        focus_task_status = root.findChild(
            QObject, "focusTaskStatus", Qt.FindChildrenRecursively
        )
        check("真实专注页有待办下拉", focus_task_box is not None)
        if focus_task_box is not None:
            focus_option_ids = [
                item.get("taskId") for item in backend.focusTaskOptions
            ]
            check("真实专注页加载待办选项", focus_task_id in focus_option_ids)
            backend.focus.reset()
            backend.focus.taskId = focus_task_id
            pump(80)
            check("真实专注页能保存关联待办",
                  backend.focus.taskId == focus_task_id)
            check("真实专注页显示当前关联任务", focus_task_status is not None
                  and "页面专注关联" in str(focus_task_status.property("text")))
            backend.tasks.rename(focus_task_id, "页面专注关联已改名")
            pump(80)
            check("待办改名后专注状态即时更新",
                  focus_task_status is not None
                  and "页面专注关联已改名" in str(focus_task_status.property("text")))
            backend.focus.startFocus()
            pump(60)
            check("真实专注计时中禁用待办切换",
                  not bool(focus_task_box.property("enabled")))
            backend.focus.reset()
            pump(60)
            backend.tasks.remove(focus_task_id)
            pump(80)
            check("真实专注关联待办删除后仍可用",
                  backend.focus.taskLabel == "待办已删除")
            check("删除最后关联待办后仍可取消关联",
                  bool(focus_task_box.property("enabled")))
            backend.focus.taskId = ""

        backend.showDashboard("today")
        pump(250)
        today_focus_action = root.findChild(
            QObject, "todayFocusAction", Qt.FindChildrenRecursively
        )
        today_tasks_action = root.findChild(
            QObject, "todayTasksAction", Qt.FindChildrenRecursively
        )
        check("真实今日页有开始专注按钮",
              today_focus_action is not None)
        check("真实今日页有处理待办按钮",
              today_tasks_action is not None)
        if today_focus_action is not None:
            backend.focus.reset()
            clicked = QMetaObject.invokeMethod(today_focus_action, "click")
            pump(80)
            check("今日页按钮能直接启动专注",
                  clicked and backend.focus.running)
            backend.focus.reset()

        backend.showDashboard("reminders")
        pump(250)
        reminders_page = root.findChild(
            QObject, "remindersPage", Qt.FindChildrenRecursively
        )
        reminder_title_field = root.findChild(
            QObject, "reminderTitleField", Qt.FindChildrenRecursively
        )
        reminder_time_field = root.findChild(
            QObject, "reminderTimeField", Qt.FindChildrenRecursively
        )
        reminder_time_error = root.findChild(
            QObject, "reminderTimeError", Qt.FindChildrenRecursively
        )
        edit_reminder_dialog = root.findChild(
            QObject, "editReminderDialog", Qt.FindChildrenRecursively
        )
        check("真实提醒时间校验控件存在",
              reminders_page is not None
              and reminder_title_field is not None
              and reminder_time_field is not None
              and reminder_time_error is not None)
        check("真实提醒编辑对话框存在", edit_reminder_dialog is not None)
        if reminders_page is not None and edit_reminder_dialog is not None:
            reminders_page.setProperty("editingId", "cleanup-test")
            opened = QMetaObject.invokeMethod(edit_reminder_dialog, "open")
            pump(40)
            closed = QMetaObject.invokeMethod(edit_reminder_dialog, "close")
            pump(40)
            check("关闭提醒编辑框会清掉旧编辑状态",
                  opened and closed
                  and reminders_page.property("editingId") == "",
                  str(reminders_page.property("editingId")))
        if (reminders_page is not None and reminder_title_field is not None
                and reminder_time_field is not None):
            before_invalid = backend.reminders.count
            reminder_title_field.setProperty("text", "非法时间提醒")
            reminder_time_field.setProperty("text", "99:99")
            QMetaObject.invokeMethod(reminders_page, "addReminder")
            pump(80)
            check("非法时间不会创建提醒",
                  backend.reminders.count == before_invalid)
            check("非法时间会显示可读提示",
                  reminder_time_error is not None
                  and bool(reminder_time_error.property("visible")))
            reminder_time_field.setProperty("text", "09:30")
            QMetaObject.invokeMethod(reminders_page, "addReminder")
            pump(80)
            check("修正时间后可以正常创建提醒",
                  backend.reminders.count == before_invalid + 1)
        backend.reminders.add("页面提醒撤销", "09:00", "daily", 0)
        live_reminder = next(
            item for item in store.reminders
            if item.get("title") == "页面提醒撤销"
        )
        backend.reminders.remove(live_reminder["id"])
        pump(80)
        reminder_undo_bar = root.findChild(
            QObject, "reminderUndoBar", Qt.FindChildrenRecursively
        )
        check("真实提醒删除后显示撤销条",
              reminder_undo_bar is not None
              and bool(reminder_undo_bar.property("visible")))
        backend.reminders.undoRemove()
        pump(50)
        check("真实页面撤销后恢复提醒",
              any(item.get("id") == live_reminder["id"]
                  for item in store.reminders))

        backend.showDashboard("ai")
        pump(250)
        history_confirm = root.findChild(
            QObject, "historyConfirmDialog", Qt.FindChildrenRecursively
        )
        ai_clear_confirm = root.findChild(
            QObject, "aiClearConfirmDialog", Qt.FindChildrenRecursively
        )
        check("真实 AI 历史确认框存在", history_confirm is not None)
        check("真实 AI 清空确认框存在", ai_clear_confirm is not None)
        for dialog, label in (
            (history_confirm, "AI 历史确认框"),
            (ai_clear_confirm, "AI 清空确认框"),
        ):
            cancel = (dialog.findChild(
                QObject, "confirmCancelButton", Qt.FindChildrenRecursively
            ) if dialog is not None else None)
            opened = (QMetaObject.invokeMethod(dialog, "open")
                      if dialog is not None else False)
            pump(40)
            canceled = (QMetaObject.invokeMethod(cancel, "click")
                        if cancel is not None else False)
            pump(40)
            check(label + "可打开并取消",
                  opened and canceled and dialog is not None
                  and not bool(dialog.property("visible")),
                  f"opened={opened}, canceled={canceled}")

        root.deleteLater()
        app.processEvents()
        del engine

    backend.shutdown()
    app.quit()

    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
