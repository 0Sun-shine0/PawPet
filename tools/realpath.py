"""用你的真实数据副本，走和 .cmd 完全一样的装配路径，看最终用的是几步。"""

import io
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRATCH = ROOT / ".cache" / "realcopy"
if SCRATCH.exists():
    shutil.rmtree(SCRATCH, ignore_errors=True)
SCRATCH.mkdir(parents=True, exist_ok=True)

# 复制你的真实数据（只复制，不动原件）
shutil.copy2(ROOT / "pet_data.json", SCRATCH / "pet_data.json")
os.environ["PAWPET_HOME"] = str(SCRATCH)

from pawpet.backend import Backend  # noqa: E402
from pawpet.config import APP_VERSION, DATA_FILE  # noqa: E402
from pawpet.store import Store  # noqa: E402

print("--- 和 .cmd 一样的装配路径 ---")
print("DATA_FILE      =", DATA_FILE)
print("APP_VERSION    =", APP_VERSION)

store = Store(SCRATCH / "pet_data.json", SCRATCH / "pet_data.backup.json")
store.load()
print()
print("1. Store.load() 之后")
print("   ai_max_steps =", store.settings.get("ai_max_steps"))

backend = Backend(store)
print()
print("2. Backend 装配之后（QML 读到的就是这些）")
print("   backend.aiMaxSteps =", backend.aiMaxSteps)
print("   buildInfo:")
for line in backend.buildInfo.splitlines():
    print("     ", line)
print()
print("3. 下拉框会选中哪一项")
options = backend.aiStepOptions
index = next((i for i, o in enumerate(options)
              if o["value"] == backend.aiMaxSteps), -1)
print("   ", options[index] if index >= 0 else f"没匹配上（index={index}）")

print()
print("4. AgentRunner 实际会拿到几步")
from pawpet.ai.agent import clamp_max_steps  # noqa: E402
print("   clamp_max_steps(backend.aiMaxSteps) =", clamp_max_steps(backend.aiMaxSteps))

backend.shutdown()
print()
if backend.aiMaxSteps == 100:
    print("结论：现在这份代码 + 这份数据，就是 100 步。")
    print("      你之前看到 20 步，只能是当时跑的那份代码还没有这个设置。")
else:
    print(f"结论：这条路上变成了 {backend.aiMaxSteps} 步 —— 代码有问题，需要继续查。")
